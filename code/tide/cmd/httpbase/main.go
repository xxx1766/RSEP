package main

import (
	"bytes"
	_ "embed"
	"encoding/csv"
	"flag"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

//go:embed dog.jpg
var dogJPEG []byte

const (
	backendImage = "docker.io/tide/workload:yolox-nano"
	ctrNs        = "default"
)

type config struct {
	cc            string
	rps           int
	duration      time.Duration
	outfile       string
	deadline      time.Duration
	backendBind   string
	backendID     string
	warmupPolls   int
	warmupDelay   time.Duration
	skipContainer bool
	endpoint      string
	warmup        bool
}

func main() {
	cfg := parseFlags()
	if !cfg.skipContainer {
		if err := startBackend(cfg); err != nil {
			die("startBackend", err)
		}
		defer stopBackend(cfg.backendID)
	}

	if err := waitHealthy(cfg); err != nil {
		die("waitHealthy", err)
	}
	if cfg.warmup {
		warm := *cfg
		workers := 6
		if cfg.cc == "on" {
			workers = 3
		}
		warm.rps, warm.duration = workers*4, time.Second
		warm.outfile = filepath.Join(filepath.Dir(cfg.outfile), "warmup.csv")
		if err := runClient(&warm); err != nil {
			die("warmup", err)
		}
	}

	if err := runClient(cfg); err != nil {
		die("runClient", err)
	}
	fmt.Fprintf(os.Stderr, "[httpbase] wrote %s\n", cfg.outfile)
}

func parseFlags() *config {
	cc := flag.String("cc", "off", "concurrency control: on|off (workers=3|6)")
	rps := flag.Int("rps", 4, "requests per second (open loop)")
	duration := flag.Duration("duration", 20*time.Second, "run length")
	outfile := flag.String("outfile", "raw.csv", "per-request CSV output path")
	deadline := flag.Duration("deadline", 30*time.Second, "per-request hard timeout")
	bind := flag.String("backend-bind", "127.0.0.1:8080", "backend TIDE_BIND")
	id := flag.String("backend-id", "httpbase-backend", "containerd container id")
	warmupPolls := flag.Int("warmup-polls", 20, "max /healthz polls at startup")
	warmupDelay := flag.Duration("warmup-delay", 1*time.Second, "delay between polls")
	skip := flag.Bool("skip-container", false, "assume backend is already running (dev)")
	warmup := flag.Bool("warmup", true, "run an unmeasured inference warmup before the measured window")
	flag.Parse()

	cc2 := strings.ToLower(*cc)
	if cc2 != "on" && cc2 != "off" {
		die("flag -cc", fmt.Errorf("must be on|off, got %q", *cc))
	}
	if *rps <= 0 || *duration <= 0 {
		die("flags", fmt.Errorf("rps>0 and duration>0 required"))
	}
	return &config{
		cc:            cc2,
		rps:           *rps,
		duration:      *duration,
		outfile:       *outfile,
		deadline:      *deadline,
		backendBind:   *bind,
		backendID:     *id,
		warmupPolls:   *warmupPolls,
		warmupDelay:   *warmupDelay,
		skipContainer: *skip,
		endpoint:      "http://" + *bind + "/infer",
		warmup:        *warmup,
	}
}

func startBackend(cfg *config) error {
	workers := "6"
	if cfg.cc == "on" {
		workers = "3"
	}
	_ = exec.Command("ctr", "-n", ctrNs, "tasks", "kill", "-s", "SIGKILL", cfg.backendID).Run()
	_ = exec.Command("ctr", "-n", ctrNs, "tasks", "rm", cfg.backendID).Run()
	_ = exec.Command("ctr", "-n", ctrNs, "containers", "rm", cfg.backendID).Run()

	cpuset := os.Getenv("WORKLOAD_CPUSET")
	if cpuset == "" {
		cpuset = "2-7"
	}
	detsPerReq := os.Getenv("TIDE_DETECTIONS_PER_REQ")
	if detsPerReq == "" {
		detsPerReq = "10"
	}
	cmd := exec.Command(
		"ctr", "-n", ctrNs, "run", "-d", "--net-host",
		"--env", "WORKLOAD_MODE=http",
		"--env", "TIDE_BIND="+cfg.backendBind,
		"--env", "TIDE_WORKERS="+workers,
		"--env", "WORKLOAD_CPUSET="+cpuset,
		"--env", "TIDE_DETECTIONS_PER_REQ="+detsPerReq,
		backendImage, cfg.backendID,
	)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("%v: %s", err, out)
	}
	fmt.Fprintf(os.Stderr, "[httpbase] backend started (cc=%s workers=%s id=%s)\n",
		cfg.cc, workers, cfg.backendID)
	return nil
}

func stopBackend(id string) {
	_ = exec.Command("ctr", "-n", ctrNs, "tasks", "kill", "-s", "SIGKILL", id).Run()
	time.Sleep(500 * time.Millisecond)
	_ = exec.Command("ctr", "-n", ctrNs, "tasks", "rm", id).Run()
	_ = exec.Command("ctr", "-n", ctrNs, "containers", "rm", id).Run()
}

func waitHealthy(cfg *config) error {
	url := "http://" + cfg.backendBind + "/healthz"
	client := &http.Client{Timeout: 2 * time.Second}
	for i := 0; i < cfg.warmupPolls; i++ {
		resp, err := client.Get(url)
		if err == nil {
			resp.Body.Close()
			if resp.StatusCode == 200 {
				fmt.Fprintf(os.Stderr, "[httpbase] backend healthy after %d polls\n", i+1)
				_, _ = client.Get(url)
				return nil
			}
		}
		time.Sleep(cfg.warmupDelay)
	}
	return fmt.Errorf("backend never became healthy (polls=%d, delay=%s)",
		cfg.warmupPolls, cfg.warmupDelay)
}

type record struct {
	reqID  uint64
	tSub   int64
	tRep   int64
	status int
}

func runClient(cfg *config) error {
	if err := os.MkdirAll(dirOf(cfg.outfile), 0o755); err != nil {
		return err
	}
	f, err := os.Create(cfg.outfile)
	if err != nil {
		return err
	}
	defer f.Close()
	w := csv.NewWriter(f)
	defer w.Flush()
	_ = w.Write([]string{"req_id", "t_submit_ns", "t_reply_ns", "latency_ms", "status"})

	interval := time.Duration(int64(time.Second) / int64(cfg.rps))
	iter := int(cfg.duration.Nanoseconds() / interval.Nanoseconds())
	fmt.Fprintf(os.Stderr, "[httpbase] rps=%d interval=%s iter=%d duration=%s\n",
		cfg.rps, interval, iter, cfg.duration)

	records := make(chan record, iter*2)
	var wg sync.WaitGroup
	var idCounter uint64

	transport := &http.Transport{
		MaxIdleConns:        cfg.rps * 2,
		MaxIdleConnsPerHost: cfg.rps * 2,
		IdleConnTimeout:     60 * time.Second,
	}
	client := &http.Client{Transport: transport, Timeout: cfg.deadline}

	t0 := time.Now()
	ticker := time.NewTicker(interval)
	for i := 0; i < iter; i++ {
		<-ticker.C
		wg.Add(1)
		go func() {
			defer wg.Done()
			id := atomic.AddUint64(&idCounter, 1)
			submit := time.Since(t0).Nanoseconds()
			req, _ := http.NewRequest(http.MethodPost, cfg.endpoint, bytes.NewReader(dogJPEG))
			req.Header.Set("Content-Type", "image/jpeg")
			resp, err := client.Do(req)
			reply := time.Since(t0).Nanoseconds()
			status := 0
			if err == nil {
				status = resp.StatusCode
				resp.Body.Close()
			}
			records <- record{id, submit, reply, status}
		}()
	}
	ticker.Stop()
	wg.Wait()
	close(records)

	var n, ok int
	for r := range records {
		latMs := float64(r.tRep-r.tSub) / 1e6
		_ = w.Write([]string{
			strconv.FormatUint(r.reqID, 10),
			strconv.FormatInt(r.tSub, 10),
			strconv.FormatInt(r.tRep, 10),
			strconv.FormatFloat(latMs, 'f', 3, 64),
			strconv.Itoa(r.status),
		})
		n++
		if r.status == 200 {
			ok++
		}
	}
	fmt.Fprintf(os.Stderr, "[httpbase] completed: %d total, %d ok (status=200)\n", n, ok)
	return nil
}

func dirOf(p string) string {
	for i := len(p) - 1; i >= 0; i-- {
		if p[i] == '/' {
			return p[:i]
		}
	}
	return "."
}

func die(stage string, err error) {
	fmt.Fprintf(os.Stderr, "FAIL %s: %v\n", stage, err)
	os.Exit(1)
}
