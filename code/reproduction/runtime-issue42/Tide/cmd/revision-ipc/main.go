package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/csv"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/ict/tide/pkg/executor/instance/ipc"
	"github.com/ict/tide/proto/execinspb"
	"google.golang.org/protobuf/proto"
)

func run(worker, out string, count, padding int) error {
	if err := os.Mkdir(out, 0755); err != nil {
		return err
	}
	signalCtx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	ctx, cancel := context.WithTimeout(signalCtx, 25*time.Second)
	defer cancel()
	os.Setenv("TIDE_MQ_MESSAGE_BYTES", "8192")
	os.Setenv("TIDE_MQ_MAX_MESSAGES", "10")
	prefix := fmt.Sprintf("revision-ipc-%d-%d", os.Getpid(), time.Now().UnixNano())
	setup, _ := json.Marshal(map[string]any{"queue_names": []string{prefix + "-in", prefix + "-out"}, "pid": os.Getpid(), "internal_timeout_s": 25})
	if err := os.WriteFile(filepath.Join(out, "setup.json"), setup, 0644); err != nil {
		return err
	}
	in, err := ipc.New(prefix + "-in")
	if err != nil {
		return err
	}
	defer in.Close()
	outq, err := ipc.New(prefix + "-out")
	if err != nil {
		return err
	}
	defer outq.Close()
	child := exec.Command("taskset", "-c", "0,1", worker, "--mode=paix")
	child.Env = append(os.Environ(), "GOMAXPROCS=1", "TIDE_IN_MQ="+prefix+"-in", "TIDE_OUT_MQ="+prefix+"-out")
	var childlog bytes.Buffer
	child.Stderr = &childlog
	if err = child.Start(); err != nil {
		return err
	}
	childDone := make(chan struct{})
	var childErr error
	go func() { childErr = child.Wait(); close(childDone) }()
	type received struct {
		evt *execinspb.Event
		err error
	}
	replies := make(chan received, 1)
	readDone := make(chan struct{})
	go func() {
		defer close(readDone)
		for {
			b, e := outq.Receive()
			if ctx.Err() != nil {
				return
			}
			var evt execinspb.Event
			if e == nil {
				e = proto.Unmarshal(b, &evt)
			}
			select {
			case replies <- received{&evt, e}:
			case <-ctx.Done():
				return
			}
			if e != nil {
				return
			}
		}
	}()
	defer func() {
		cancel()
		child.Process.Kill()
		<-childDone
		outq.Send([]byte{})
		<-readDone
		os.WriteFile(filepath.Join(out, "worker.log"), childlog.Bytes(), 0644)
	}()
	receive := func() (*execinspb.Event, error) {
		select {
		case r := <-replies:
			return r.evt, r.err
		case <-childDone:
			return nil, fmt.Errorf("worker exited before reply: %v", childErr)
		case <-ctx.Done():
			return nil, ctx.Err()
		}
	}
	init, err := receive()
	if err != nil {
		return err
	}
	if init.Type != execinspb.Event_INIT {
		return fmt.Errorf("missing INIT")
	}
	args, _ := json.Marshal(map[string]any{"kind": "cpu", "bytes": 8, "iterations": 1, "padding": strings.Repeat("x", padding)})
	f, err := os.Create(filepath.Join(out, "raw.csv"))
	if err != nil {
		return err
	}
	defer f.Close()
	w := csv.NewWriter(f)
	defer w.Flush()
	w.Write([]string{"request_id", "phase", "round_trip_ns", "worker_service_ns", "request_wire_bytes", "response_wire_bytes"})
	start := time.Now()
	var measuredStart time.Time
	for i := 0; i < count+50; i++ {
		evt := &execinspb.Event{Type: execinspb.Event_ROUTINE_SUBMIT, Routine: &execinspb.Routine{Id: fmt.Sprint(i), FnName: "cpu", Args: args}}
		t0 := time.Now()
		if i == 50 {
			measuredStart = t0
		}
		b, err := proto.Marshal(evt)
		if err != nil {
			return err
		}
		if err = in.Send(b); err != nil {
			return err
		}
		reply, err := receive()
		if err != nil {
			return err
		}
		elapsed := time.Since(t0).Nanoseconds()
		if reply.Type != execinspb.Event_EXIT || reply.GetStatus() != execinspb.Event_SUCC {
			return fmt.Errorf("unsuccessful PAIX reply")
		}
		var data struct {
			ServiceNs int64 `json:"service_ns"`
			Result    struct {
				BytesProcessed int64 `json:"bytes_processed"`
			} `json:"result"`
		}
		if err = json.Unmarshal(reply.Result, &data); err != nil {
			return err
		}
		if data.ServiceNs <= 0 || data.Result.BytesProcessed != 8 {
			return fmt.Errorf("kernel reply mismatch")
		}
		phase := "measured"
		if i < 50 {
			phase = "warmup"
		}
		w.Write([]string{strconv.Itoa(i), phase, strconv.FormatInt(elapsed, 10), strconv.FormatInt(data.ServiceNs, 10), strconv.Itoa(len(b)), strconv.Itoa(proto.Size(reply))})
	}
	elapsed := time.Since(measuredStart).Nanoseconds()
	w.Flush()
	if err = w.Error(); err != nil {
		return err
	}
	workerBytes, err := os.ReadFile(worker)
	if err != nil {
		return err
	}
	sum := sha256.Sum256(workerBytes)
	meta := map[string]any{"status": "completed", "requests": count, "warmup_requests": 50, "padding_bytes": padding, "measurement_duration_ns": elapsed, "started_utc": start.UTC(), "worker_sha256": hex.EncodeToString(sum[:]), "pid": os.Getpid(), "worker_pid": child.Process.Pid, "argv": os.Args, "worker_affinity": "0,1", "worker_gomaxprocs": 1, "mq_message_bytes": 8192, "mq_max_messages": 10, "scope": "two OS processes, PAIX protobuf over POSIX mqueue, sequential round trips including minimal SHA256(8bytes) kernel; not full scheduler throughput", "rtt_semantics": "request marshal through response unmarshal; service_ns nested, no exact subtraction called pure control", "throughput_semantics": "measured requests/wall time includes client validation and buffered CSV recording"}
	data, _ := json.MarshalIndent(meta, "", "  ")
	return os.WriteFile(filepath.Join(out, "run.json"), data, 0644)
}

func main() {
	worker := flag.String("worker", "./revision-worker", "existing worker binary")
	out := flag.String("out", "ipc-run", "new output directory")
	n := flag.Int("requests", 3000, "measured round trips plus50warmup")
	padding := flag.Int("padding", 64, "JSON padding bytes, at most4096")
	flag.Parse()
	if *n < 1 || *padding < 0 || *padding > 4096 {
		panic("invalid arguments")
	}
	if err := run(*worker, *out, *n, *padding); err != nil {
		panic(err)
	}
}
