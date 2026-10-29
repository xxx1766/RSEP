// revision-http-run measures a serial FCFS application served by the same
// worker kernel over HTTP. It has no predictive QoS admission policy.
package main

import (
	"bytes"
	"context"
	"encoding/csv"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"math"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"sync"
	"time"

	"github.com/ict/tide/pkg/revisionworkload"
)

type runConfig struct {
	Requests int
	RPS      float64
	Timeout  time.Duration
	QoSMS    float64
	URL      string
	Work     []byte
}
type record struct {
	ID                     int
	Submit, Reply, Service int64
	Status                 int
	Error                  string
}
type job struct {
	ctx    context.Context
	finish func(int, int64, string)
}

func runHTTP(c runConfig) ([]record, time.Time, error) {
	if c.Requests < 1 || c.RPS <= 0 || math.IsNaN(c.RPS) || math.IsInf(c.RPS, 0) || c.Timeout <= 0 || c.QoSMS <= 0 {
		return nil, time.Time{}, fmt.Errorf("invalid run configuration")
	}
	var work revisionworkload.Config
	if err := json.Unmarshal(c.Work, &work); err != nil {
		return nil, time.Time{}, err
	}
	if work.Bytes < 8 || work.Iterations < 1 {
		return nil, time.Time{}, fmt.Errorf("invalid workload")
	}
	transport := &http.Transport{MaxIdleConnsPerHost: 1}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport}
	queue := make(chan job, c.Requests)
	results := make(chan record, c.Requests)
	var submitted, worker sync.WaitGroup
	worker.Add(1)
	go func() {
		defer worker.Done()
		available := true
		for j := range queue {
			if j.ctx.Err() != nil {
				j.finish(504, 0, "external timeout in FCFS queue")
				continue
			}
			if !available {
				j.finish(503, 0, "execution channel unavailable after transport failure")
				continue
			}
			// An external response timeout does not cancel a non-cancellable
			// kernel. Retain the serial slot until its actual response arrives.
			// Bound broken transport separately from queue-inclusive reporting.
			execCtx, execCancel := context.WithTimeout(context.Background(), c.Timeout)
			req, err := http.NewRequestWithContext(execCtx, "POST", c.URL, bytes.NewReader(c.Work))
			if err != nil {
				execCancel()
				j.finish(500, 0, err.Error())
				continue
			}
			req.Header.Set("Content-Type", "application/json")
			response, err := client.Do(req)
			if err != nil {
				execCancel()
				available = false // remote execution state is unknown; never overlap another kernel
				status := 500
				if j.ctx.Err() != nil || errors.Is(err, context.DeadlineExceeded) {
					status = 504
				}
				j.finish(status, 0, err.Error())
				continue
			}
			var reply struct {
				Result    revisionworkload.Result `json:"result"`
				ServiceNs int64                   `json:"service_ns"`
				Error     string                  `json:"error"`
			}
			err = json.NewDecoder(response.Body).Decode(&reply)
			response.Body.Close()
			execCancel()
			if err != nil {
				j.finish(500, 0, err.Error())
				continue
			}
			if response.StatusCode != 200 || reply.Error != "" {
				j.finish(response.StatusCode, reply.ServiceNs, reply.Error)
				continue
			}
			if reply.ServiceNs <= 0 || reply.Result.BytesProcessed != int64(work.Bytes)*int64(work.Iterations) {
				j.finish(500, reply.ServiceNs, "worker reply did not match requested work")
				continue
			}
			j.finish(200, reply.ServiceNs, "")
		}
	}()
	start := time.Now()
	for i := 0; i < c.Requests; i++ {
		if wait := time.Until(start.Add(time.Duration(float64(i) / c.RPS * float64(time.Second)))); wait > 0 {
			time.Sleep(wait)
		}
		id, submit := i, time.Since(start).Nanoseconds()
		ctx, cancel := context.WithTimeout(context.Background(), c.Timeout)
		submitted.Add(1)
		var once sync.Once
		finish := func(status int, service int64, message string) {
			once.Do(func() {
				results <- record{ID: id, Submit: submit, Reply: time.Since(start).Nanoseconds(), Service: service, Status: status, Error: message}
				cancel()
				submitted.Done()
			})
		}
		context.AfterFunc(ctx, func() { finish(504, 0, "external response wait expired") })
		queue <- job{ctx, finish}
	}
	close(queue)
	submitted.Wait()
	worker.Wait()
	close(results)
	rows := make([]record, 0, c.Requests)
	for r := range results {
		rows = append(rows, r)
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].ID < rows[j].ID })
	// Retain samples through the full offered-load window, even at low load.
	if wait := time.Until(start.Add(time.Duration(float64(c.Requests) / c.RPS * float64(time.Second)))); wait > 0 {
		time.Sleep(wait)
	}
	return rows, start, nil
}

func main() {
	n := flag.Int("requests", 1, "offered request count")
	rps := flag.Float64("rps", 1, "offered requests/s")
	url := flag.String("url", "http://127.0.0.1:19280/run", "worker endpoint")
	work := flag.String("work", `{"kind":"cpu","bytes":65536,"iterations":100}`, "workload JSON")
	qos := flag.Float64("qos-ms", 500, "deadline for reporting, not an admission rule")
	timeout := flag.Duration("timeout", 5*time.Second, "external timeout including FCFS queue")
	out := flag.String("out", "results-http", "new output directory")
	system := flag.String("system", "Bare HTTP FCFS", "system label")
	classFlag := flag.String("qos-classes-ms", "", "alternating deadline classes for reporting; no priority or QoS admission")
	flag.Parse()
	classes, err := revisionworkload.ParseDeadlineClasses(*classFlag)
	if err != nil {
		panic(err)
	}
	var workload revisionworkload.Config
	if err := json.Unmarshal([]byte(*work), &workload); err != nil {
		panic(err)
	}
	if err := os.Mkdir(*out, 0755); err != nil {
		panic(err)
	}
	rows, start, err := runHTTP(runConfig{*n, *rps, *timeout, *qos, *url, []byte(*work)})
	if err != nil {
		panic(err)
	}
	f, err := os.Create(filepath.Join(*out, "raw.csv"))
	if err != nil {
		panic(err)
	}
	writer := csv.NewWriter(f)
	writer.Write([]string{"req_id", "t_submit_ns", "t_reply_ns", "latency_ms", "status", "qos_ms", "submit_call_ns", "worker_service_ns", "error", "fn_name"})
	ok := 0
	for _, r := range rows {
		if r.Status == 200 {
			ok++
		}
		fn, deadlineMS := classes.ForRequest(r.ID, workload.Kind, int(*qos))
		qosText := fmt.Sprint(*qos)
		if len(classes) > 0 {
			qosText = strconv.Itoa(deadlineMS)
		}
		writer.Write([]string{strconv.Itoa(r.ID), strconv.FormatInt(r.Submit, 10), strconv.FormatInt(r.Reply, 10), fmt.Sprintf("%.6f", float64(r.Reply-r.Submit)/1e6), strconv.Itoa(r.Status), qosText, "0", strconv.FormatInt(r.Service, 10), r.Error, fn})
	}
	writer.Flush()
	if err = writer.Error(); err != nil {
		panic(err)
	}
	f.Close()
	meta := map[string]any{"status": "completed", "system": *system, "requests": *n, "successes": ok, "rps": *rps, "cpus": 2, "workload": workload, "qos_ms": *qos, "measurement_start_ns": 0, "measurement_end_ns": int64(float64(*n) / *rps * float64(time.Second)), "measurement_start_unix_ns": start.UnixNano(), "external_timeout_ns": timeout.Nanoseconds(), "argv": os.Args, "pid": os.Getpid(), "application_concurrency": 1, "queue_policy": "FIFO, external timeout only; no predictive QoS admission", "worker_url": *url, "submit_call_ns_semantics": "not measured; zero placeholder field not used for control-plane costs"}
	meta["qos_classes_ms"] = classes
	data, err := json.MarshalIndent(meta, "", "  ")
	if err != nil {
		panic(err)
	}
	if err = os.WriteFile(filepath.Join(*out, "run.json"), data, 0644); err != nil {
		panic(err)
	}
	fmt.Println(string(data))
	// Keep the controller observable through the end of the measurement window.
	time.Sleep(200 * time.Millisecond)
}
