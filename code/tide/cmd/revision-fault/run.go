package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"math/rand"
	"net"
	"net/http"
	"sort"
	"sync"
	"time"

	"github.com/ict/tide/pkg/revisionworkload"
)

type RunConfig struct {
	LocalURL      string                  `json:"local_url"`
	RemoteURL     string                  `json:"remote_url"`
	Phase         time.Duration           `json:"phase_ns"`
	Timeout       time.Duration           `json:"external_timeout_ns"`
	QoS           time.Duration           `json:"qos_ns"`
	Estimate      time.Duration           `json:"service_estimate_ns"`
	Work          revisionworkload.Config `json:"workload"`
	Trace         []int64                 `json:"offered_trace_ns"`
	LocalEligible bool                    `json:"local_eligible"`
	RunID         string                  `json:"run_id"`
}
type Record struct {
	ID                                       int
	RequestID                                string
	PlannedNS, SubmitNS, DispatchNS, ReplyNS int64
	Node, Status                             int
	ServiceNS                                int64
	WorkerStartUnixNS, WorkerFinishUnixNS    int64
	SubmitPhase, ReplyPhase                  string
	OnTime, WithinWindow                     bool
	Error                                    string
}
type FaultEvent struct {
	Phase       string   `json:"phase"`
	StartedNS   int64    `json:"transition_started_ns"`
	AtNS        int64    `json:"transition_completed_ns"`
	InflightIDs []string `json:"affected_inflight_request_ids"`
}

func GenerateTrace(seed int64, rate float64, duration time.Duration) []int64 {
	rng := rand.New(rand.NewSource(seed))
	count := int(math.Round(duration.Seconds() * rate))
	trace := make([]int64, count)
	for i := range trace {
		trace[i] = int64((float64(i) + rng.Float64()*.2) / rate * float64(time.Second))
	}
	return trace
}
func phaseAt(ns int64, events []FaultEvent) string {
	phase := "connected"
	for _, event := range events {
		if ns >= event.AtNS {
			phase = event.Phase
		}
	}
	return phase
}
func Run(c RunConfig) ([]Record, []FaultEvent, time.Time, error) {
	if c.Phase <= 0 || c.Timeout <= 0 || c.QoS <= 0 || c.Estimate <= 0 || c.LocalURL == "" || c.RemoteURL == "" {
		return nil, nil, time.Time{}, fmt.Errorf("invalid run configuration")
	}
	for i, arrival := range c.Trace {
		if arrival < 0 || arrival >= int64(3*c.Phase) || (i > 0 && arrival < c.Trace[i-1]) {
			return nil, nil, time.Time{}, fmt.Errorf("invalid recorded arrival trace")
		}
	}
	work, err := json.Marshal(c.Work)
	if err != nil {
		return nil, nil, time.Time{}, err
	}
	gate := NewGate(c.RemoteURL)
	defer gate.Close()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return nil, nil, time.Time{}, err
	}
	server := &http.Server{Handler: gate, ReadHeaderTimeout: time.Second}
	defer server.Close()
	go server.Serve(listener)
	urls := []string{c.LocalURL, "http://" + listener.Addr().String()}
	transport := &http.Transport{MaxIdleConnsPerHost: 2}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport}
	type job struct {
		record Record
		ctx    context.Context
		cancel context.CancelFunc
	}
	queues := []chan job{make(chan job, len(c.Trace)), make(chan job, len(c.Trace))}
	results := make(chan Record, len(c.Trace))
	pending := []int{0, 0}
	var mu sync.Mutex
	var workers sync.WaitGroup
	started := time.Now()
	for node := range queues {
		workers.Add(1)
		go func(node int) {
			defer workers.Done()
			for task := range queues[node] {
				record := task.record
				record.DispatchNS = time.Since(started).Nanoseconds()
				if task.ctx.Err() != nil {
					record.Status = 504
					record.Error = "external timeout while queued"
				} else {
					req, e := http.NewRequestWithContext(task.ctx, "POST", urls[node]+"/run", bytes.NewReader(work))
					if e != nil {
						record.Status = 500
						record.Error = e.Error()
					} else {
						req.Header.Set("Content-Type", "application/json")
						req.Header.Set("X-Request-ID", record.RequestID)
						response, e := client.Do(req)
						if e != nil {
							record.Status = 503
							record.Error = e.Error()
							if task.ctx.Err() != nil {
								record.Status = 504
							}
						} else {
							record.Status = response.StatusCode
							if response.StatusCode != 200 {
								body, _ := io.ReadAll(io.LimitReader(response.Body, 4096))
								record.Error = string(body)
							} else {
								var reply WorkerReply
								e = json.NewDecoder(response.Body).Decode(&reply)
								if e != nil {
									record.Status = 500
									record.Error = e.Error()
								} else {
									record.ServiceNS = reply.ServiceNS
									record.WorkerStartUnixNS = reply.StartUnixNS
									record.WorkerFinishUnixNS = reply.FinishUnixNS
									if reply.Error != "" || reply.RequestID != record.RequestID || reply.Result.BytesProcessed != int64(c.Work.Bytes)*int64(c.Work.Iterations) || reply.ServiceNS <= 0 {
										record.Status = 500
										record.Error = "worker result identity or work validation failed: " + reply.Error
									}
								}
							}
							response.Body.Close()
						}
					}
				}
				record.ReplyNS = time.Since(started).Nanoseconds()
				task.cancel()
				mu.Lock()
				pending[node]--
				mu.Unlock()
				record.OnTime = record.Status == 200 && record.ReplyNS-record.SubmitNS <= int64(c.QoS)
				record.WithinWindow = record.Status == 200 && record.ReplyNS < int64(3*c.Phase)
				results <- record
			}
		}(node)
	}
	events := []FaultEvent{{Phase: "connected", InflightIDs: []string{}}}
	eventsDone := make(chan struct{})
	go func() {
		defer close(eventsDone)
		for index, phase := range []string{"disconnected", "recovered"} {
			time.Sleep(max(0, time.Until(started.Add(time.Duration(index+1)*c.Phase))))
			before := time.Since(started).Nanoseconds()
			ids := gate.SetConnected(index == 1)
			events = append(events, FaultEvent{Phase: phase, StartedNS: before, AtNS: time.Since(started).Nanoseconds(), InflightIDs: ids})
		}
	}()
	for id, planned := range c.Trace {
		time.Sleep(max(0, time.Until(started.Add(time.Duration(planned)))))
		submitted := time.Since(started).Nanoseconds()
		record := Record{ID: id, RequestID: fmt.Sprintf("%s-%d", c.RunID, id), PlannedNS: planned, SubmitNS: submitted, Node: -1}
		mu.Lock()
		candidates := []Candidate{{FinishNS: submitted + int64(pending[0])*int64(c.Estimate), EstimateNS: int64(c.Estimate)}, {FinishNS: submitted + int64(pending[1])*int64(c.Estimate), EstimateNS: int64(c.Estimate), NetworkNS: int64(5 * time.Millisecond)}}
		chosen := Choose(candidates, c.LocalEligible, submitted, submitted+int64(c.QoS))
		if chosen >= 0 {
			pending[chosen]++
		}
		mu.Unlock()
		record.Node = chosen
		if chosen < 0 {
			record.Status = 429
			record.Error = "no predicted deadline-feasible eligible node"
			record.ReplyNS = time.Since(started).Nanoseconds()
			results <- record
			continue
		}
		ctx, cancel := context.WithTimeout(context.Background(), c.Timeout)
		queues[chosen] <- job{record: record, ctx: ctx, cancel: cancel}
	}
	for _, queue := range queues {
		close(queue)
	}
	workers.Wait()
	close(results)
	<-eventsDone
	time.Sleep(max(0, time.Until(started.Add(3*c.Phase))))
	records := make([]Record, 0, len(c.Trace))
	for record := range results {
		record.SubmitPhase = phaseAt(record.SubmitNS, events)
		record.ReplyPhase = phaseAt(record.ReplyNS, events)
		records = append(records, record)
	}
	sort.Slice(records, func(i, j int) bool { return records[i].ID < records[j].ID })
	return records, events, started, nil
}
