// Package revisioncontinuity runs fixed-count CPU work with durable observations.
package revisioncontinuity

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sync"
	"time"

	"github.com/ict/tide/pkg/revisionworkload"
)

// GoldenChecksum was calculated independently using Python hashlib.sha256 for
// the revisionworkload CPU initialization and 100 iterations over 65536 bytes.
const GoldenChecksum uint64 = 8843627163163507496
const BytesPerStep int64 = 65536 * 100

type Options struct {
	RunID      string `json:"run_id"`
	Steps      int    `json:"steps"`
	IntervalMS int    `json:"interval_ms"`
	RemoteURL  string `json:"remote_url,omitempty"`
}

type Config struct {
	revisionworkload.Config
	Continuity *Options `json:"continuity,omitempty"`
}

type Reply struct {
	WorkerInstance string                  `json:"worker_instance"`
	Node           string                  `json:"node"`
	WorkerPID      int                     `json:"worker_pid"`
	WorkerBootNS   int64                   `json:"worker_boot_ns"`
	WorkerStartNs  int64                   `json:"worker_start_ns"`
	WorkerEndNs    int64                   `json:"worker_end_ns"`
	Result         revisionworkload.Result `json:"result"`
	ServiceNs      int64                   `json:"service_ns"`
	Error          string                  `json:"error,omitempty"`
	RunID          string                  `json:"run_id,omitempty"`
	StepsExpected  int                     `json:"steps_expected,omitempty"`
	StepsValid     int                     `json:"steps_valid"`
	StepsErrors    int                     `json:"steps_errors"`
}

// MonotonicNS is elapsed monotonic time since this worker instance booted. It is
// meaningful within worker_instance, while UnixNS allows cross-node alignment.
type Record struct {
	Sequence       int    `json:"sequence"`
	Event          string `json:"event"`
	RunID          string `json:"run_id"`
	Step           int    `json:"step"`
	MonotonicNS    int64  `json:"monotonic_ns"`
	UnixNS         int64  `json:"unix_ns"`
	WorkerPID      int    `json:"worker_pid"`
	WorkerBootNS   int64  `json:"worker_boot_ns"`
	WorkerInstance string `json:"worker_instance"`
	Node           string `json:"node"`
	Checksum       uint64 `json:"checksum"`
	BytesProcessed int64  `json:"bytes_processed"`
	RemoteChecksum uint64 `json:"remote_checksum,omitempty"`
	Valid          bool   `json:"valid"`
	Error          string `json:"error,omitempty"`
	StepsExpected  int    `json:"steps_expected"`
	StepsValid     int    `json:"steps_valid"`
	StepsErrors    int    `json:"steps_errors"`
}

type Runner struct {
	node, dir, instance string
	pid                 int
	boot                time.Time
	serial              sync.Mutex
	client              *http.Client
}

func NewRunner(node, dir string) *Runner {
	boot := time.Now()
	pid := os.Getpid()
	return &Runner{node: node, dir: dir, pid: pid, boot: boot, instance: fmt.Sprintf("%s-%d-%d", node, pid, boot.UnixNano()), client: &http.Client{Timeout: 500 * time.Millisecond}}
}

func (r *Runner) Identity() Reply {
	return Reply{WorkerInstance: r.instance, Node: r.node, WorkerPID: r.pid, WorkerBootNS: r.boot.UnixNano()}
}

var safeID = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$`)

func validate(c Config) error {
	o := c.Continuity
	if !safeID.MatchString(o.RunID) {
		return fmt.Errorf("invalid run_id")
	}
	if o.Steps < 1 || o.Steps > 3600 || o.IntervalMS < 10 || o.IntervalMS > 60000 {
		return fmt.Errorf("steps must be 1..3600 and interval_ms 10..60000")
	}
	if c.Kind != "cpu" || c.Bytes != 65536 || c.Iterations != 100 {
		return fmt.Errorf("continuity requires cpu bytes=65536 iterations=100")
	}
	if o.RemoteURL != "" {
		u, err := url.Parse(o.RemoteURL)
		if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" || u.User != nil {
			return fmt.Errorf("remote_url must be an absolute HTTP(S) URL without credentials")
		}
	}
	return nil
}

// Run serializes work but not Progress or Identity. No task deadline or HTTP
// request context truncates an accepted fixed-count run.
func (r *Runner) Run(c Config) (reply Reply) {
	r.serial.Lock()
	defer r.serial.Unlock()
	start := time.Now()
	reply = r.Identity()
	reply.WorkerStartNs = start.UnixNano()
	defer func() { reply.ServiceNs = time.Since(start).Nanoseconds(); reply.WorkerEndNs = time.Now().UnixNano() }()
	if c.Continuity == nil {
		c.Config.TempDir = r.dir
		result, err := revisionworkload.Run(c.Config)
		reply.Result = result
		if err != nil {
			reply.Error = err.Error()
		}
		return
	}
	if err := validate(c); err != nil {
		reply.Error = err.Error()
		return
	}
	o := c.Continuity
	reply.RunID = o.RunID
	reply.StepsExpected = o.Steps
	f, err := os.OpenFile(filepath.Join(r.dir, o.RunID+".jsonl"), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		reply.Error = err.Error()
		return
	}
	defer f.Close()
	sequence := 0
	appendRecord := func(event string, step int, result revisionworkload.Result, remote uint64, valid bool, message string) error {
		sequence++
		now := time.Now()
		rec := Record{Sequence: sequence, Event: event, RunID: o.RunID, Step: step, MonotonicNS: now.Sub(r.boot).Nanoseconds(), UnixNS: now.UnixNano(), WorkerPID: r.pid, WorkerBootNS: r.boot.UnixNano(), WorkerInstance: r.instance, Node: r.node, Checksum: result.Checksum, BytesProcessed: result.BytesProcessed, RemoteChecksum: remote, Valid: valid, Error: message, StepsExpected: o.Steps, StepsValid: reply.StepsValid, StepsErrors: reply.StepsErrors}
		if err := json.NewEncoder(f).Encode(rec); err != nil {
			return err
		}
		return f.Sync()
	}
	write := func(event string, step int, result revisionworkload.Result, remote uint64, valid bool, message string) bool {
		if err := appendRecord(event, step, result, remote, valid, message); err != nil {
			reply.Error = "progress write failed: " + err.Error()
			return false
		}
		return true
	}
	if !write("start", 0, revisionworkload.Result{}, 0, false, "") {
		return
	}
	firstTick := time.Now()
	for step := 1; step <= o.Steps; step++ {
		if wait := time.Until(firstTick.Add(time.Duration(step-1) * time.Duration(o.IntervalMS) * time.Millisecond)); wait > 0 {
			time.Sleep(wait)
		}
		if !write("tick", step, revisionworkload.Result{}, 0, false, "") {
			return
		}
		result, err := revisionworkload.Run(c.Config)
		reply.Result.Checksum += result.Checksum
		reply.Result.BytesProcessed += result.BytesProcessed
		if err == nil && (result.Checksum != GoldenChecksum || result.BytesProcessed != BytesPerStep) {
			err = fmt.Errorf("local checksum or work-count mismatch")
		}
		var remote uint64
		if err == nil && o.RemoteURL != "" {
			remote, err = r.remoteStep(o.RemoteURL)
		}
		if err != nil {
			reply.StepsErrors++
			if !write("error", step, result, remote, false, err.Error()) {
				return
			}
		} else {
			reply.StepsValid++
			if !write("result", step, result, remote, true, "") {
				return
			}
		}
	}
	if reply.StepsErrors > 0 {
		reply.Error = fmt.Sprintf("%d of %d steps unsuccessful", reply.StepsErrors, o.Steps)
	}
	write("end", o.Steps, reply.Result, 0, reply.StepsErrors == 0, reply.Error)
	return
}

func (r *Runner) remoteStep(endpoint string) (uint64, error) {
	body := []byte(`{"kind":"cpu","bytes":65536,"iterations":100}`)
	response, err := r.client.Post(endpoint, "application/json", bytes.NewReader(body))
	if err != nil {
		return 0, fmt.Errorf("remote request: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return 0, fmt.Errorf("remote HTTP status %d", response.StatusCode)
	}
	var reply struct {
		Result revisionworkload.Result `json:"result"`
		Error  string                  `json:"error"`
	}
	if err := json.NewDecoder(io.LimitReader(response.Body, 8192)).Decode(&reply); err != nil {
		return 0, fmt.Errorf("remote response: %w", err)
	}
	if reply.Error != "" {
		return reply.Result.Checksum, fmt.Errorf("remote error: %.256s", reply.Error)
	}
	if reply.Result.Checksum != GoldenChecksum || reply.Result.BytesProcessed != BytesPerStep {
		return reply.Result.Checksum, fmt.Errorf("remote checksum or work-count mismatch")
	}
	return reply.Result.Checksum, nil
}

// Progress returns only complete records, including during an active run.
func (r *Runner) Progress(id string) ([]byte, error) {
	if !safeID.MatchString(id) {
		return nil, fmt.Errorf("invalid run_id")
	}
	b, err := os.ReadFile(filepath.Join(r.dir, id+".jsonl"))
	if err != nil {
		return nil, err
	}
	last := bytes.LastIndexByte(b, '\n')
	if last < 0 {
		return []byte{}, nil
	}
	return b[:last+1], nil
}
