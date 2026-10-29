package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"image"
	_ "image/jpeg"
	"io"
	"net"
	"net/http"
	"reflect"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

const campaignID = "historical-threefig-20261019"
const maxPayloadBytes = 1 << 20

type nodeInfo struct {
	Campaign string   `json:"campaign"`
	Name     string   `json:"name"`
	Address  string   `json:"address"`
	Role     string   `json:"role"`
	Kind     string   `json:"kind"`
	CPUs     int      `json:"cpus"`
	Stub     string   `json:"stub"`
	StubMode string   `json:"stub_mode"`
	PID      int      `json:"pid"`
	Image    string   `json:"image"`
	Commands []string `json:"commands"`
}

func validateCPUBudget(cpus int) error {
	if cpus < 2 || cpus > 12 || cpus%2 != 0 {
		return fmt.Errorf("CPU budget must be an even number from 2 through 12")
	}
	return nil
}

func validatePeers(self nodeInfo, peers []nodeInfo) error {
	if len(peers) != 3 {
		return fmt.Errorf("exactly three peers required")
	}
	names, addresses, kinds := map[string]bool{}, map[string]bool{}, map[string]bool{}
	foundSelf := false
	for _, p := range peers {
		if p.Campaign != campaignID || !strings.HasPrefix(p.Name, "threefig-") || names[p.Name] || addresses[p.Address] || kinds[p.Kind] {
			return fmt.Errorf("invalid or duplicate campaign peer")
		}
		if _, _, err := net.SplitHostPort(p.Address); err != nil {
			return fmt.Errorf("invalid peer address: %w", err)
		}
		if p.Image != self.Image || !reflect.DeepEqual(p.Commands, self.Commands) {
			return fmt.Errorf("all nodes must use the identical image and commands")
		}
		if err := validateCPUBudget(p.CPUs); err != nil {
			return fmt.Errorf("peer %s: %w", p.Name, err)
		}
		switch p.Kind {
		case "origin":
			if p.Role != "edge" || p.Stub == "" || p.StubMode != "entry" {
				return fmt.Errorf("origin requires EDGE persistent entry")
			}
		case "edge":
			if p.Role != "edge" || p.Stub != "" || p.StubMode != "" {
				return fmt.Errorf("execution edge requires EDGE and zero initial function stubs")
			}
		case "cloud":
			if p.Role != "cloud" || p.Stub == "" || p.StubMode != "fn" {
				return fmt.Errorf("cloud requires CLOUD native function")
			}
		default:
			return fmt.Errorf("unknown peer kind")
		}
		if p.Name == self.Name {
			if p.Address != self.Address || p.Kind != self.Kind || p.Role != self.Role || p.CPUs != self.CPUs || p.Stub != self.Stub || p.StubMode != self.StubMode {
				return fmt.Errorf("self manifest differs from running node")
			}
			foundSelf = true
		}
		names[p.Name], addresses[p.Address], kinds[p.Kind] = true, true, true
	}
	if !foundSelf {
		return fmt.Errorf("self missing from peers")
	}
	return nil
}
func readJPEG(r io.Reader) ([]byte, error) {
	b, err := io.ReadAll(io.LimitReader(r, maxPayloadBytes+1))
	if err != nil {
		return nil, err
	}
	if len(b) == 0 || len(b) > maxPayloadBytes {
		return nil, fmt.Errorf("JPEG payload must contain 1..%d bytes", maxPayloadBytes)
	}
	c, format, err := image.DecodeConfig(bytes.NewReader(b))
	if err != nil || format != "jpeg" || c.Width < 1 || c.Height < 1 {
		return nil, fmt.Errorf("valid JPEG header required")
	}
	return b, nil
}

type destination struct {
	ToCloud    bool   `json:"to_cloud"`
	ReceiverID string `json:"receiver_id"`
	Node       string `json:"node"`
	Role       string `json:"role"`
	Known      bool   `json:"known"`
}
type stubWitness struct {
	StubID    string `json:"stub_id"`
	Node      string `json:"node"`
	Role      string `json:"role"`
	Mode      string `json:"mode"`
	Status    string `json:"status"`
	CreatedNS int64  `json:"created_unix_ns"`
	StoppedNS int64  `json:"stopped_unix_ns"`
	UpdatedNS int64  `json:"updated_unix_ns"`
	EstWaitNS uint64 `json:"est_wait_ns"`
}
type witnessLedger struct {
	mu    sync.RWMutex
	stubs map[string]stubWitness
	peers map[string]nodeInfo
}

func newWitnessLedger() *witnessLedger {
	return &witnessLedger{stubs: map[string]stubWitness{}, peers: map[string]nodeInfo{}}
}
func (l *witnessLedger) setPeers(peers []nodeInfo) {
	l.mu.Lock()
	defer l.mu.Unlock()
	for _, p := range peers {
		l.peers[p.Name] = p
	}
}
func (l *witnessLedger) addStub(s stubWitness) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.stubs[s.StubID] = s
}
func (l *witnessLedger) stopStub(id string, now int64) {
	l.mu.Lock()
	defer l.mu.Unlock()
	s, ok := l.stubs[id]
	if !ok {
		return
	}
	s.Status = "stopped"
	s.StoppedNS = now
	l.stubs[id] = s
}
func (l *witnessLedger) syncStub(id string, updated int64, wait uint64) {
	l.mu.Lock()
	defer l.mu.Unlock()
	s, ok := l.stubs[id]
	if !ok {
		return
	}
	s.UpdatedNS = updated
	s.EstWaitNS = wait
	l.stubs[id] = s
}
func (l *witnessLedger) destination(cloud bool, id string) destination {
	d := destination{ToCloud: cloud, ReceiverID: id}
	l.mu.RLock()
	defer l.mu.RUnlock()
	if cloud {
		if p, ok := l.peers[id]; ok && p.Role == "cloud" {
			d.Node, d.Role, d.Known = p.Name, p.Role, true
		}
	} else if s, ok := l.stubs[id]; ok {
		d.Node, d.Role, d.Known = s.Node, s.Role, true
	}
	return d
}
func (l *witnessLedger) snapshot() []stubWitness {
	l.mu.RLock()
	defer l.mu.RUnlock()
	result := make([]stubWitness, 0, len(l.stubs))
	for _, s := range l.stubs {
		result = append(result, s)
	}
	sort.Slice(result, func(i, j int) bool { return result[i].StubID < result[j].StubID })
	return result
}

type nodeCapacity struct {
	Node          string  `json:"node"`
	Role          string  `json:"role"`
	TotalCPUs     int     `json:"total_cpus"`
	RemainingCPUs int     `json:"remaining_cpus"`
	ReservedCPUs  int     `json:"reserved_cpus"`
	UpdatedNS     int64   `json:"updated_unix_ns"`
	AgeMS         float64 `json:"age_ms"`
	Valid         bool    `json:"valid"`
}
type processorObservation struct {
	Node                 string `json:"node"`
	TotalCPUs            int    `json:"total_cpus"`
	ReservedCPUs         int    `json:"reserved_cpus"`
	CommittedCPUs        int    `json:"committed_cpus"`
	BorrowedCPUs         int    `json:"borrowed_cpus"`
	PhysicalOccupiedCPUs int    `json:"physical_occupied_cpus"`
	ObservedNS           int64  `json:"observed_unix_ns"`
}
type stateResponse struct {
	LocalProcessors *processorObservation `json:"local_processors"`
	ObservedNS      int64                 `json:"observed_unix_ns"`
	Node            string                `json:"node"`
	Campaign        string                `json:"campaign"`
	Nodes           []nodeCapacity        `json:"nodes"`
	Stubs           []stubWitness         `json:"stubs"`
	Image           string                `json:"image"`
	Commands        []string              `json:"commands"`
	QoSMS           int                   `json:"qos_ms"`
	TTLMS           int                   `json:"ttl_ms"`
	Joined          bool                  `json:"joined"`
	ActiveHTTP      int64                 `json:"active_http_requests"`
	Submitted       int64                 `json:"submitted_requests"`
	Completed       int64                 `json:"completed_requests"`
	Unresolved      int64                 `json:"unresolved_requests"`
}
type nativeResult struct {
	Status  int
	Success bool
	Result  []byte
	Error   string
}
type pendingRequest interface {
	waitData() <-chan struct{}
	receiver() destination
	outcome() nativeResult
}
type submitFn func(context.Context, string, []byte) (pendingRequest, error)
type runWitness struct {
	Image            string   `json:"image"`
	Commands         []string `json:"commands"`
	EntryStub        string   `json:"entry_stub"`
	OutputValidation string   `json:"output_validation"`
}
type runResponse struct {
	RequestID       string          `json:"request_id"`
	ClientRequestID string          `json:"client_request_id"`
	InputSHA256     string          `json:"input_sha256"`
	InputBytes      int             `json:"input_bytes"`
	Function        string          `json:"function"`
	SubmittedNS     int64           `json:"submitted_unix_ns"`
	ReturnedNS      int64           `json:"returned_unix_ns"`
	LatencyMS       float64         `json:"latency_ms"`
	Status          string          `json:"status"`
	NativeStatus    int             `json:"native_status"`
	Result          []byte          `json:"result_base64"`
	ResultJSON      json.RawMessage `json:"result_json,omitempty"`
	Error           string          `json:"error"`
	Before          destination     `json:"receiver_before_wait"`
	After           destination     `json:"receiver_after_wait"`
	Witness         runWitness      `json:"witness"`
}
type api struct {
	self                         nodeInfo
	deadline                     time.Duration
	submit                       submitFn
	install                      func([]nodeInfo) error
	state                        func() stateResponse
	joined                       atomic.Bool
	sequence                     atomic.Uint64
	active, submitted, completed atomic.Int64
	peersMu                      sync.Mutex
	peers                        []nodeInfo
}

func newAPI(self nodeInfo, deadline time.Duration, submit submitFn, install func([]nodeInfo) error, state func() stateResponse) *api {
	return &api{self: self, deadline: deadline, submit: submit, install: install, state: state}
}
func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}
func requireMethod(w http.ResponseWriter, r *http.Request, method string) bool {
	if r.Method == method {
		return true
	}
	w.Header().Set("Allow", method)
	http.Error(w, method+" required", http.StatusMethodNotAllowed)
	return false
}
func (a *api) handler() http.Handler {
	m := http.NewServeMux()
	m.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		if requireMethod(w, r, "GET") {
			writeJSON(w, 200, a.self)
		}
	})
	m.HandleFunc("/config", func(w http.ResponseWriter, r *http.Request) {
		if requireMethod(w, r, "GET") {
			s := a.snapshot()
			writeJSON(w, 200, map[string]any{"campaign": campaignID, "qos_ms": s.QoSMS, "ttl_ms": s.TTLMS, "image": a.self.Image, "commands": a.self.Commands, "rsep_test": false})
		}
	})
	m.HandleFunc("/state", func(w http.ResponseWriter, r *http.Request) {
		if requireMethod(w, r, "GET") {
			writeJSON(w, 200, a.snapshot())
		}
	})
	m.HandleFunc("/peers", a.peersHandler)
	m.HandleFunc("/run", a.runHandler)
	return m
}
func (a *api) snapshot() stateResponse {
	var s stateResponse
	if a.state != nil {
		s = a.state()
	}
	s.ObservedNS = time.Now().UnixNano()
	s.Node = a.self.Name
	s.Campaign = campaignID
	s.Image = a.self.Image
	s.Commands = a.self.Commands
	s.Joined = a.joined.Load()
	s.ActiveHTTP = a.active.Load()
	s.Completed = a.completed.Load()
	s.Submitted = a.submitted.Load()
	s.Unresolved = s.Submitted - s.Completed
	return s
}
func (a *api) peersHandler(w http.ResponseWriter, r *http.Request) {
	if !requireMethod(w, r, "POST") {
		return
	}
	var peers []nodeInfo
	d := json.NewDecoder(http.MaxBytesReader(w, r.Body, 32768))
	d.DisallowUnknownFields()
	if err := d.Decode(&peers); err != nil {
		http.Error(w, err.Error(), 400)
		return
	}
	var extra any
	if err := d.Decode(&extra); err != io.EOF {
		http.Error(w, "one peer JSON value required", 400)
		return
	}
	if err := validatePeers(a.self, peers); err != nil {
		http.Error(w, err.Error(), 400)
		return
	}
	a.peersMu.Lock()
	defer a.peersMu.Unlock()
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
	if !requireMethod(w, r, "POST") {
		return
	}
	if a.self.Kind != "origin" {
		http.Error(w, "run requires origin entry", 409)
		return
	}
	if !a.joined.Load() {
		http.Error(w, "peers must be installed before traffic", 503)
		return
	}
	payload, err := readJPEG(r.Body)
	if err != nil {
		http.Error(w, err.Error(), 400)
		return
	}
	a.active.Add(1)
	defer a.active.Add(-1)
	started := time.Now()
	sum := sha256.Sum256(payload)
	response := runResponse{RequestID: fmt.Sprintf("%s-%d-%d", a.self.Name, a.self.PID, a.sequence.Add(1)), ClientRequestID: r.Header.Get("X-Request-ID"), InputSHA256: hex.EncodeToString(sum[:]), InputBytes: len(payload), Function: "proce_img", SubmittedNS: started.UnixNano(), Status: "submission_error", Witness: runWitness{Image: a.self.Image, Commands: a.self.Commands, EntryStub: a.self.Stub, OutputValidation: "native_status_only"}}
	w.Header().Set("X-Tide-Request-ID", response.RequestID)
	ctx, cancel := context.WithTimeout(r.Context(), a.deadline)
	defer cancel()
	pending, err := a.submit(ctx, response.RequestID, payload)
	code := 503
	if err != nil {
		response.Error = err.Error()
	} else {
		a.submitted.Add(1)
		// Observe late backend completion even when the HTTP caller has timed out.
		go func() { <-pending.waitData(); a.completed.Add(1) }()
		response.Before = pending.receiver()
		select {
		case <-pending.waitData():
			outcome := pending.outcome()
			response.NativeStatus = outcome.Status
			response.Result = outcome.Result
			response.Error = outcome.Error
			response.Status = "failure"
			if outcome.Success {
				response.Status = "success"
				code = 200
			}
			if len(response.Result) > 0 {
				response.Witness.OutputValidation = "worker_witness_unverified"
				if json.Valid(response.Result) {
					response.ResultJSON = append(json.RawMessage(nil), response.Result...)
				}
			}
		case <-ctx.Done():
			response.Status = "timeout"
			response.Error = ctx.Err().Error()
			code = 504
		}
		response.After = pending.receiver()
	}
	response.ReturnedNS = time.Now().UnixNano()
	response.LatencyMS = float64(time.Since(started)) / float64(time.Millisecond)
	writeJSON(w, code, response)
}
