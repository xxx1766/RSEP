package revisiondomainworkload

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/ict/tide/pkg/revisionworkload"
)

const GoldenChecksum uint64 = 3147045699343202425
const BytesProcessed int64 = 65536000

type Config struct {
	Kind       string `json:"kind"`
	Bytes      int    `json:"bytes"`
	Iterations int    `json:"iterations"`
	RequestID  string `json:"request_id"`
}

type Reply struct {
	RequestID              string                  `json:"request_id"`
	WorkerInstance         string                  `json:"worker_instance"`
	Node                   string                  `json:"node"`
	WorkerPID              int                     `json:"worker_pid"`
	WorkerBootNS           int64                   `json:"worker_boot_ns"`
	HostBootID             string                  `json:"host_boot_id,omitempty"`
	WorkerStartNS          int64                   `json:"worker_start_ns"`
	WorkerEndNS            int64                   `json:"worker_end_ns"`
	WorkerStartMonotonicNS int64                   `json:"worker_start_monotonic_ns"`
	WorkerEndMonotonicNS   int64                   `json:"worker_end_monotonic_ns"`
	Result                 revisionworkload.Result `json:"result"`
	Error                  string                  `json:"error,omitempty"`
}

type Record struct {
	Sequence            int64  `json:"sequence"`
	Event               string `json:"event"`
	ObservedUnixNS      int64  `json:"observed_unix_ns"`
	ObservedMonotonicNS int64  `json:"observed_monotonic_ns"`
	Reply
}

type Worker struct {
	mu       sync.Mutex
	identity Reply
	journal  *os.File
	sequence int64
	ready    bool
}

var safeID = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$`)

func Validate(c Config) error {
	if c.Kind != "cpu" || c.Bytes != 65536 || c.Iterations != 1000 {
		return fmt.Errorf("requires cpu bytes=65536 iterations=1000")
	}
	if !safeID.MatchString(c.RequestID) {
		return fmt.Errorf("request_id must be 1..128 safe ASCII characters")
	}
	return nil
}

func New(node, dir string) (*Worker, error) {
	if err := os.MkdirAll(dir, 0755); err != nil {
		return nil, err
	}
	f, err := os.OpenFile(filepath.Join(dir, "requests.jsonl"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
	if err != nil {
		return nil, err
	}
	boot := time.Now().UnixNano()
	pid := os.Getpid()
	hostBoot, _ := os.ReadFile("/proc/sys/kernel/random/boot_id")
	return &Worker{journal: f, identity: Reply{WorkerInstance: fmt.Sprintf("%s-%d-%d", node, pid, boot), Node: node, WorkerPID: pid, WorkerBootNS: boot, HostBootID: strings.TrimSpace(string(hostBoot))}}, nil
}

func (w *Worker) Identity() Reply { return w.identity }

func (w *Worker) Ready() error {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.ready {
		return nil
	}
	if err := w.append("instance_ready", w.identity); err != nil {
		return err
	}
	w.ready = true
	return nil
}

func (w *Worker) Close() error { w.mu.Lock(); defer w.mu.Unlock(); return w.journal.Close() }

func (w *Worker) append(event string, reply Reply) error {
	mono, err := monotonicNS()
	if err != nil {
		return err
	}
	w.sequence++
	record := Record{Sequence: w.sequence, Event: event, ObservedUnixNS: time.Now().UnixNano(), ObservedMonotonicNS: mono, Reply: reply}
	if err := json.NewEncoder(w.journal).Encode(record); err != nil {
		return err
	}
	return w.journal.Sync()
}

func (w *Worker) Run(c Config) Reply {
	w.mu.Lock()
	defer w.mu.Unlock()
	r := w.identity
	r.RequestID = c.RequestID
	if err := Validate(c); err != nil {
		r.Error = err.Error()
		return r
	}
	mono, err := monotonicNS()
	if err != nil {
		r.Error = err.Error()
		return r
	}
	r.WorkerStartMonotonicNS = mono
	r.WorkerStartNS = time.Now().UnixNano()
	if err := w.append("start", r); err != nil {
		r.Error = "journal start: " + err.Error()
		return r
	}
	r.Result, err = revisionworkload.Run(revisionworkload.Config{Kind: c.Kind, Bytes: c.Bytes, Iterations: c.Iterations})
	if err == nil && (r.Result.Checksum != GoldenChecksum || r.Result.BytesProcessed != BytesProcessed) {
		err = fmt.Errorf("independent golden checksum/work-count mismatch")
	}
	if err != nil {
		r.Error = err.Error()
	}
	r.WorkerEndNS = time.Now().UnixNano()
	r.WorkerEndMonotonicNS, err = monotonicNS()
	if err != nil {
		r.Error = err.Error()
	}
	if err := w.append("result", r); err != nil {
		r.Error = "journal result: " + err.Error()
	}
	return r
}
