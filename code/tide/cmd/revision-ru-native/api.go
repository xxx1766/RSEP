package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"reflect"
	"sync"
	"sync/atomic"
	"time"

	"github.com/ict/tide/pkg/revisionworkload"
)

const appID = "r-u-20261020"
const workerImage = "docker.io/tide/fig6-cluster-20261014"

type nodeInfo struct {
	Name     string   `json:"name"`
	Address  string   `json:"address"`
	Role     string   `json:"role"`
	CPUs     int      `json:"cpus"`
	Slots    int      `json:"slots"`
	Stub     string   `json:"stub"`
	PID      int      `json:"pid"`
	Policy   string   `json:"policy"`
	Image    string   `json:"image"`
	Commands []string `json:"commands"`
	Root     string   `json:"root"`
}
type destination struct {
	Cloud bool   `json:"cloud"`
	ID    string `json:"id"`
}
type nativeResult struct {
	Status  int
	Success bool
	Result  []byte
	Error   string
}
type pendingRequest interface {
	waitData() <-chan struct{}
	outcome() nativeResult
	receiver() destination
}
type submitFn func(context.Context, string, []byte) (pendingRequest, error)
type runResponse struct {
	RequestID            string                  `json:"request_id"`
	ClientRequestID      string                  `json:"client_request_id"`
	Policy               string                  `json:"policy"`
	Workload             revisionworkload.Config `json:"workload"`
	InputSHA256          string                  `json:"input_sha256"`
	SubmittedNS          int64                   `json:"submitted_unix_ns"`
	ReturnedNS           int64                   `json:"returned_unix_ns"`
	SubmittedMonotonicNS int64                   `json:"submitted_monotonic_ns"`
	ReturnedMonotonicNS  int64                   `json:"returned_monotonic_ns"`
	LatencyMS            float64                 `json:"latency_ms"`
	Status               string                  `json:"status"`
	NativeStatus         int                     `json:"native_status"`
	Result               []byte                  `json:"result_base64"`
	ResultJSON           json.RawMessage         `json:"result_json,omitempty"`
	Error                string                  `json:"error,omitempty"`
	Before               destination             `json:"receiver_before_wait"`
	After                destination             `json:"receiver_after_wait"`
}
type api struct {
	self                              nodeInfo
	timeout                           time.Duration
	submit                            submitFn
	install                           func([]nodeInfo) error
	snapshot                          func() map[string]any
	gate                              *faultGate
	mu                                sync.Mutex
	peers                             []nodeInfo
	joined                            atomic.Bool
	seq, active, submitted, completed atomic.Int64
}

func newAPI(self nodeInfo, timeout time.Duration, submit submitFn, install func([]nodeInfo) error, snapshot func() map[string]any, g *faultGate) *api {
	return &api{self: self, timeout: timeout, submit: submit, install: install, snapshot: snapshot, gate: g}
}
func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(v)
}
func method(w http.ResponseWriter, r *http.Request, want string) bool {
	if r.Method != want {
		w.Header().Set("Allow", want)
		http.Error(w, want+" required", 405)
		return false
	}
	return true
}
func decodeStrict(r *http.Request, v any) error {
	b, err := io.ReadAll(io.LimitReader(r.Body, 32769))
	if err != nil {
		return err
	}
	if len(b) > 32768 {
		return fmt.Errorf("body too large")
	}
	// A second Decode rejects trailing JSON and non-whitespace data.
	d := json.NewDecoder(bytes.NewReader(b))
	d.DisallowUnknownFields()
	if err = d.Decode(v); err != nil {
		return err
	}
	var extra any
	if err = d.Decode(&extra); err != io.EOF {
		return fmt.Errorf("trailing JSON")
	}
	return nil
}
func validatePeers(self nodeInfo, p []nodeInfo) error {
	if len(p) != 2 {
		return fmt.Errorf("exactly two peers required")
	}
	seen := map[string]bool{}
	addresses := map[string]bool{}
	found := false
	for _, n := range p {
		want := "edge"
		if n.Name == "ru-cloud" {
			want = "cloud"
		} else if n.Name != "ru-local" {
			return fmt.Errorf("invalid node")
		}
		if n.Role != want || n.CPUs != 2 || n.Slots != 1 || n.Stub == "" || n.PID <= 0 || seen[n.Name] || addresses[n.Address] || n.Policy != self.Policy || n.Image != self.Image {
			return fmt.Errorf("invalid matched native inventory")
		}
		if !reflect.DeepEqual(n.Commands, []string{"/worker", "--mode=paix", "--node=" + n.Name, "--tmpdir=/owned"}) {
			return fmt.Errorf("worker command differs from frozen profile")
		}
		if _, _, err := net.SplitHostPort(n.Address); err != nil {
			return err
		}
		seen[n.Name] = true
		addresses[n.Address] = true
		if n.Name == self.Name {
			if !reflect.DeepEqual(n, self) {
				return fmt.Errorf("self identity mismatch")
			}
			found = true
		}
	}
	if !found {
		return fmt.Errorf("self absent")
	}
	return nil
}
func (a *api) handler() http.Handler {
	m := http.NewServeMux()
	m.HandleFunc("/clock", func(w http.ResponseWriter, r *http.Request) {
		if !method(w, r, "GET") {
			return
		}
		c, err := readClock()
		if err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
		writeJSON(w, 200, c)
	})
	m.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		if method(w, r, "GET") {
			writeJSON(w, 200, a.self)
		}
	})
	m.HandleFunc("/peers", a.peersHandler)
	m.HandleFunc("/state", func(w http.ResponseWriter, r *http.Request) {
		if !method(w, r, "GET") {
			return
		}
		s := map[string]any{}
		if a.snapshot != nil {
			s = a.snapshot()
		}
		s["self"] = a.self
		s["joined"] = a.joined.Load()
		s["active_http_requests"] = a.active.Load()
		s["submitted_requests"] = a.submitted.Load()
		s["completed_requests"] = a.completed.Load()
		s["unresolved_requests"] = a.submitted.Load() - a.completed.Load()
		if a.gate != nil {
			s["gate"] = a.gate.state()
		}
		writeJSON(w, 200, s)
	})
	m.HandleFunc("/gate", func(w http.ResponseWriter, r *http.Request) {
		if !method(w, r, "POST") {
			return
		}
		var req struct {
			Enabled *bool `json:"enabled"`
		}
		if err := decodeStrict(r, &req); err != nil || req.Enabled == nil {
			http.Error(w, "expected exactly {enabled:bool}", 400)
			return
		}
		if a.gate == nil {
			http.Error(w, "gate unavailable", 503)
			return
		}
		v, err := a.gate.set(*req.Enabled)
		if err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
		writeJSON(w, 200, v)
	})
	m.HandleFunc("/run", a.runHandler)
	return m
}
func (a *api) peersHandler(w http.ResponseWriter, r *http.Request) {
	if !method(w, r, "POST") {
		return
	}
	var p []nodeInfo
	if err := decodeStrict(r, &p); err != nil {
		http.Error(w, err.Error(), 400)
		return
	}
	if err := validatePeers(a.self, p); err != nil {
		http.Error(w, err.Error(), 400)
		return
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.joined.Load() {
		http.Error(w, "peers already installed", 409)
		return
	}
	if a.install != nil {
		if err := a.install(p); err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
	}
	a.peers = append([]nodeInfo(nil), p...)
	a.joined.Store(true)
	writeJSON(w, 200, map[string]any{"self": a.self, "peers": p})
}
func validWork(c revisionworkload.Config) bool {
	return c.Kind == "cpu" && c.Bytes == 65536 && c.Iterations == 100 || c.Kind == "memory" && c.Bytes == 16777216 && c.Iterations == 4
}
func (a *api) runHandler(w http.ResponseWriter, r *http.Request) {
	if !method(w, r, "POST") {
		return
	}
	if !a.joined.Load() {
		http.Error(w, "peers required", 503)
		return
	}
	var work revisionworkload.Config
	if err := decodeStrict(r, &work); err != nil || !validWork(work) {
		http.Error(w, "expected a frozen CPU or memory workload", 400)
		return
	}
	body, _ := json.Marshal(work)
	start := time.Now()
	stamp, err := readClock()
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	sum := sha256.Sum256(body)
	res := runResponse{RequestID: fmt.Sprintf("%s-%d-%d", a.self.Name, a.self.PID, a.seq.Add(1)), ClientRequestID: r.Header.Get("X-Request-ID"), Policy: a.self.Policy, Workload: work, InputSHA256: hex.EncodeToString(sum[:]), SubmittedNS: start.UnixNano(), Status: "submission_error"}
	res.SubmittedMonotonicNS = stamp.MonotonicNS
	w.Header().Set("X-Tide-Request-ID", res.RequestID)
	a.active.Add(1)
	defer a.active.Add(-1)
	ctx, cancel := context.WithTimeout(r.Context(), a.timeout)
	defer cancel()
	code := 503
	p, err := a.submit(ctx, res.RequestID, body)
	if err != nil {
		res.Error = err.Error()
	} else {
		a.submitted.Add(1)
		go func() { <-p.waitData(); a.completed.Add(1) }()
		res.Before = p.receiver()
		select {
		case <-p.waitData():
			out := p.outcome()
			res.NativeStatus = out.Status
			res.Result = out.Result
			res.Error = out.Error
			res.Status = "failure"
			if out.Success {
				res.Status = "success"
				code = 200
			}
			if json.Valid(out.Result) {
				res.ResultJSON = append(json.RawMessage(nil), out.Result...)
			}
		case <-ctx.Done():
			res.Status = "timeout"
			res.Error = ctx.Err().Error()
			code = 504
		}
		res.After = p.receiver()
	}
	res.ReturnedNS = time.Now().UnixNano()
	if end, err := readClock(); err == nil {
		res.ReturnedMonotonicNS = end.MonotonicNS
	}
	res.LatencyMS = float64(time.Since(start)) / 1e6
	writeJSON(w, code, res)
}
