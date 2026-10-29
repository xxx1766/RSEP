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

	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/revisiondomainworkload"
)

const appID = "domain-resilience-20261027"
const workerImage = "docker.io/tide/domain-resilience-20261027"

type nodeInfo struct {
	Name     string   `json:"name"`
	Address  string   `json:"address"`
	Role     string   `json:"role"`
	CPUs     int      `json:"cpus"`
	Slots    int      `json:"slots"`
	Stub     string   `json:"stub"`
	PID      int      `json:"pid"`
	Initial  string   `json:"initial"`
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
	RequestID            string                        `json:"request_id"`
	ClientRequestID      string                        `json:"client_request_id"`
	Workload             revisiondomainworkload.Config `json:"workload"`
	InputSHA256          string                        `json:"input_sha256"`
	SubmittedNS          int64                         `json:"submitted_unix_ns"`
	ReturnedNS           int64                         `json:"returned_unix_ns"`
	SubmittedMonotonicNS int64                         `json:"submitted_monotonic_ns"`
	ReturnedMonotonicNS  int64                         `json:"returned_monotonic_ns"`
	LatencyMS            float64                       `json:"latency_ms"`
	Status               string                        `json:"status"`
	NativeStatus         int                           `json:"native_status"`
	Result               []byte                        `json:"result_base64"`
	ResultJSON           json.RawMessage               `json:"result_json,omitempty"`
	Error                string                        `json:"error,omitempty"`
	Before               destination                   `json:"receiver_before_wait"`
	After                destination                   `json:"receiver_after_wait"`
}
type api struct {
	self                         nodeInfo
	timeout                      time.Duration
	submit                       submitFn
	install                      func([]nodeInfo) error
	snapshot                     func() map[string]any
	mu                           sync.Mutex
	peers                        []nodeInfo
	requestIDs                   map[string]bool
	joined                       atomic.Bool
	active, submitted, completed atomic.Int64
}

func workerCommands(node string) []string {
	return []string{"/worker", "--mode=paix", "--node=" + node, "--tmpdir=/tmp/domain"}
}
func newAPI(self nodeInfo, timeout time.Duration, submit submitFn, install func([]nodeInfo) error, snapshot func() map[string]any) *api {
	return &api{self: self, timeout: timeout, submit: submit, install: install, snapshot: snapshot, requestIDs: map[string]bool{}}
}
func validateNativeConfig(c dev.Config) error {
	if c.QoSMS <= 0 || c.TTLMS != 86400000 || !c.C4Functions["cpu"] {
		return fmt.Errorf("positive QoS,24h TTL,and CPU C4 are required")
	}
	for _, name := range []string{"A", "B", "C"} {
		if c.NodeExecutionMS[name] <= 0 {
			return fmt.Errorf("positive execution estimate required for %s", name)
		}
	}
	if dev.GetReqResource().CpuNum != 2 || dev.GetStubResource().CpuNum != 2 {
		return fmt.Errorf("native request/stub must reserve two CPUs")
	}
	return nil
}
func validatePeers(self nodeInfo, peers []nodeInfo) error {
	if len(peers) != 3 {
		return fmt.Errorf("exactly A,B,C are required")
	}
	seen := map[string]bool{}
	addresses := map[string]bool{}
	found := false
	for _, n := range peers {
		role, cpus, slots := "edge", 2, 1
		if n.Name == "C" {
			role, cpus, slots = "cloud", 0, 0
		} else if n.Name == "B" && self.Initial == "cold" {
			slots = 0
		} else if n.Name != "A" && n.Name != "B" {
			return fmt.Errorf("unknown node")
		}
		if n.Role != role || n.CPUs != cpus || n.Slots != slots || (n.Stub != "") != (slots == 1) || n.PID <= 0 || n.Initial != self.Initial || n.Image != workerImage || seen[n.Name] || addresses[n.Address] {
			return fmt.Errorf("unmatched application inventory")
		}
		if n.Name == "C" {
			if len(n.Commands) != 0 {
				return fmt.Errorf("C must have no application command")
			}
		} else if !reflect.DeepEqual(n.Commands, workerCommands(n.Name)) {
			return fmt.Errorf("unexpected worker command")
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
func writeJSON(w http.ResponseWriter, code int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(value)
}
func method(w http.ResponseWriter, r *http.Request, want string) bool {
	if r.Method != want {
		w.Header().Set("Allow", want)
		http.Error(w, want+" required", 405)
		return false
	}
	return true
}
func decodeStrict(r *http.Request, value any) error {
	b, err := io.ReadAll(io.LimitReader(r.Body, 8193))
	if err != nil {
		return err
	}
	if len(b) > 8192 {
		return fmt.Errorf("body too large")
	}
	d := json.NewDecoder(bytes.NewReader(b))
	d.DisallowUnknownFields()
	if err := d.Decode(value); err != nil {
		return err
	}
	var extra any
	if d.Decode(&extra) != io.EOF {
		return fmt.Errorf("trailing JSON")
	}
	return nil
}
func (a *api) state() map[string]any {
	state := map[string]any{}
	if a.snapshot != nil {
		state = a.snapshot()
	}
	state["self"] = a.self
	state["joined"] = a.joined.Load()
	state["submitted_requests"] = a.submitted.Load()
	state["completed_requests"] = a.completed.Load()
	state["unresolved_requests"] = a.submitted.Load() - a.completed.Load()
	state["active_http_requests"] = a.active.Load()
	return state
}
func (a *api) handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/clock", func(w http.ResponseWriter, r *http.Request) {
		if !method(w, r, "GET") {
			return
		}
		stamp, err := readClock()
		if err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
		writeJSON(w, 200, stamp)
	})
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		if !method(w, r, "GET") {
			return
		}
		data := a.state()
		b, _ := json.Marshal(a.self)
		var fields map[string]any
		_ = json.Unmarshal(b, &fields)
		for key, value := range fields {
			data[key] = value
		}
		writeJSON(w, 200, data)
	})
	mux.HandleFunc("/state", func(w http.ResponseWriter, r *http.Request) {
		if method(w, r, "GET") {
			writeJSON(w, 200, a.state())
		}
	})
	mux.HandleFunc("/peers", a.peersHandler)
	mux.HandleFunc("/run", a.runHandler)
	return mux
}
func (a *api) peersHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method == "GET" {
		a.mu.Lock()
		peers := append([]nodeInfo(nil), a.peers...)
		a.mu.Unlock()
		writeJSON(w, 200, map[string]any{"initial_peers": peers, "actual_local": a.state()})
		return
	}
	if !method(w, r, "POST") {
		return
	}
	var peers []nodeInfo
	if err := decodeStrict(r, &peers); err != nil {
		http.Error(w, err.Error(), 400)
		return
	}
	if err := validatePeers(a.self, peers); err != nil {
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
		if err := a.install(peers); err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
	}
	a.peers = append([]nodeInfo(nil), peers...)
	a.joined.Store(true)
	writeJSON(w, 200, map[string]any{"self": a.self, "peers": peers})
}
func (a *api) runHandler(w http.ResponseWriter, r *http.Request) {
	if !method(w, r, "POST") {
		return
	}
	if a.self.Name != "A" {
		http.Error(w, "external requests must enter genuine Fn A", 403)
		return
	}
	if !a.joined.Load() {
		http.Error(w, "peers required", 503)
		return
	}
	var work revisiondomainworkload.Config
	if err := decodeStrict(r, &work); err != nil {
		http.Error(w, err.Error(), 400)
		return
	}
	if err := revisiondomainworkload.Validate(work); err != nil {
		http.Error(w, err.Error(), 400)
		return
	}
	if header := r.Header.Get("X-Request-ID"); header != "" && header != work.RequestID {
		http.Error(w, "header/payload request_id mismatch", 400)
		return
	}
	a.mu.Lock()
	duplicate := a.requestIDs[work.RequestID]
	if !duplicate {
		a.requestIDs[work.RequestID] = true
	}
	a.mu.Unlock()
	if duplicate {
		http.Error(w, "request_id already submitted", 409)
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
	reply := runResponse{RequestID: work.RequestID, ClientRequestID: work.RequestID, Workload: work, InputSHA256: hex.EncodeToString(sum[:]), SubmittedNS: stamp.UnixNS, SubmittedMonotonicNS: stamp.MonotonicNS, Status: "submission_error"}
	a.active.Add(1)
	defer a.active.Add(-1)
	ctx, cancel := context.WithTimeout(r.Context(), a.timeout)
	defer cancel()
	code := 503
	pending, err := a.submit(ctx, work.RequestID, body)
	if err != nil {
		reply.Error = err.Error()
	} else {
		a.submitted.Add(1)
		go func() { <-pending.waitData(); a.completed.Add(1) }()
		reply.Before = pending.receiver()
		select {
		case <-pending.waitData():
			out := pending.outcome()
			reply.NativeStatus = out.Status
			reply.Result = out.Result
			reply.Error = out.Error
			reply.Status = "failure"
			if out.Success {
				reply.Status = "success"
				code = 200
			}
			if json.Valid(out.Result) {
				reply.ResultJSON = append(json.RawMessage(nil), out.Result...)
			}
		case <-ctx.Done():
			reply.Status = "timeout"
			reply.Error = ctx.Err().Error()
			code = 504
		}
		reply.After = pending.receiver()
	}
	end, err := readClock()
	if err == nil {
		reply.ReturnedNS = end.UnixNS
		reply.ReturnedMonotonicNS = end.MonotonicNS
	} else {
		reply.ReturnedNS = time.Now().UnixNano()
	}
	reply.LatencyMS = float64(time.Since(start)) / 1e6
	w.Header().Set("X-Tide-Request-ID", work.RequestID)
	writeJSON(w, code, reply)
}
