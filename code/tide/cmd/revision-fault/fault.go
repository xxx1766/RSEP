// revision-fault is a measured-kernel HTTP fault adapter, not Tide's native
// distributed submission/event-return path.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"net/http"
	"os"
	"sort"
	"sync"
	"time"

	"github.com/ict/tide/pkg/revisionworkload"
	"github.com/ict/tide/pkg/schedpolicy"
)

type Candidate struct{ FinishNS, EstimateNS, NetworkNS int64 }

// Choose mirrors the local-first eligibility decision, then uses the shared
// score for remote candidates. Two-worker experiments have one remote candidate.
func Choose(nodes []Candidate, localEligible bool, now, deadline int64) int {
	feasible := func(n Candidate) bool { return max(now, n.FinishNS)+n.EstimateNS+n.NetworkNS <= deadline }
	if now >= deadline {
		return -1
	}
	if localEligible && feasible(nodes[0]) {
		return 0
	}
	selected := -1
	best := math.Inf(-1)
	for j := 1; j < len(nodes); j++ {
		n := nodes[j]
		if !feasible(n) {
			continue
		}
		latency := float64(max(now, n.FinishNS)+n.EstimateNS+n.NetworkNS-now) / 1e6
		score := schedpolicy.Score(0, latency, 1, [3]float64{.3, .5, .2}, 100)
		if score > best {
			best = score
			selected = j
		}
	}
	return selected
}

type Gate struct {
	mu        sync.Mutex
	connected bool
	target    string
	active    map[string]context.CancelFunc
	transport *http.Transport
}

func NewGate(url string) *Gate {
	return &Gate{connected: true, target: url, active: map[string]context.CancelFunc{}, transport: &http.Transport{MaxIdleConnsPerHost: 4}}
}
func (g *Gate) Close() { g.SetConnected(false); g.transport.CloseIdleConnections() }

// Cancellation closes only this adapter's upstream HTTP requests. It cannot
// cancel an already-running worker kernel; the worker retains its serial slot.
func (g *Gate) SetConnected(connected bool) []string {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.connected = connected
	ids := []string{}
	if !connected {
		for id, cancel := range g.active {
			ids = append(ids, id)
			cancel()
		}
	}
	sort.Strings(ids)
	return ids
}
func (g *Gate) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithCancel(r.Context())
	defer cancel()
	id := r.Header.Get("X-Request-ID")
	g.mu.Lock()
	if !g.connected {
		g.mu.Unlock()
		http.Error(w, "experiment remote endpoint disconnected", http.StatusServiceUnavailable)
		return
	}
	if _, exists := g.active[id]; exists {
		g.mu.Unlock()
		http.Error(w, "duplicate active request ID", http.StatusConflict)
		return
	}
	g.active[id] = cancel
	g.mu.Unlock()
	defer func() { g.mu.Lock(); delete(g.active, id); g.mu.Unlock() }()
	req, err := http.NewRequestWithContext(ctx, r.Method, g.target+r.URL.Path, r.Body)
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	req.Header = r.Header.Clone()
	response, err := g.transport.RoundTrip(req)
	if err != nil {
		http.Error(w, "scoped remote transport: "+err.Error(), 503)
		return
	}
	defer response.Body.Close()
	for name, values := range response.Header {
		for _, value := range values {
			w.Header().Add(name, value)
		}
	}
	w.WriteHeader(response.StatusCode)
	_, _ = io.Copy(w, response.Body)
}

type WorkerReply struct {
	Result          revisionworkload.Result `json:"result"`
	RequestID       string                  `json:"request_id"`
	StartUnixNS     int64                   `json:"start_unix_ns"`
	FinishUnixNS    int64                   `json:"finish_unix_ns"`
	ServiceNS       int64                   `json:"service_ns"`
	Error           string                  `json:"error,omitempty"`
	ClientCancelled bool                    `json:"client_cancelled_at_kernel_finish"`
}
type Worker struct {
	slot      chan struct{}
	mu        sync.Mutex
	active    int
	completed int
	log       io.Writer
}

func NewWorker(log io.Writer) *Worker { return &Worker{slot: make(chan struct{}, 1), log: log} }
func (worker *Worker) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path == "/health" || r.URL.Path == "/state" {
		worker.mu.Lock()
		defer worker.mu.Unlock()
		_ = json.NewEncoder(w).Encode(map[string]int{"active": worker.active, "completed": worker.completed, "pid": os.Getpid()})
		return
	}
	if r.URL.Path != "/run" || r.Method != "POST" {
		http.Error(w, "POST /run required", 405)
		return
	}
	var work revisionworkload.Config
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 8192)).Decode(&work); err != nil {
		http.Error(w, err.Error(), 400)
		return
	}
	select {
	case worker.slot <- struct{}{}:
	case <-r.Context().Done():
		return
	}
	defer func() { <-worker.slot }()
	if r.Context().Err() != nil {
		return
	}
	worker.mu.Lock()
	worker.active++
	worker.mu.Unlock()
	start := time.Now()
	reply := WorkerReply{RequestID: r.Header.Get("X-Request-ID"), StartUnixNS: start.UnixNano()}
	result, err := revisionworkload.Run(work)
	reply.Result = result
	reply.ServiceNS = time.Since(start).Nanoseconds()
	reply.FinishUnixNS = time.Now().UnixNano()
	reply.ClientCancelled = r.Context().Err() != nil
	if err != nil {
		reply.Error = err.Error()
	}
	worker.mu.Lock()
	worker.active--
	worker.completed++
	if worker.log != nil {
		_ = json.NewEncoder(worker.log).Encode(reply)
	}
	worker.mu.Unlock()
	w.Header().Set("Content-Type", "application/json")
	if err != nil {
		w.WriteHeader(400)
	}
	if err := json.NewEncoder(w).Encode(reply); err != nil && worker.log != nil {
		worker.mu.Lock()
		fmt.Fprintf(worker.log, "{\"response_write_error\":%q,\"request_id\":%q}\n", err.Error(), reply.RequestID)
		worker.mu.Unlock()
	}
}
