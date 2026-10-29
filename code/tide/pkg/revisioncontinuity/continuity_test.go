package revisioncontinuity

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/ict/tide/pkg/revisionworkload"
)

func testConfig(id string, steps int) Config {
	return Config{Config: revisionworkload.Config{Kind: "cpu", Bytes: 65536, Iterations: 100}, Continuity: &Options{RunID: id, Steps: steps, IntervalMS: 10}}
}

func records(t *testing.T, b []byte) []Record {
	t.Helper()
	var out []Record
	for _, line := range bytes.Split(bytes.TrimSpace(b), []byte("\n")) {
		var rec Record
		if err := json.Unmarshal(line, &rec); err != nil {
			t.Fatal(err)
		}
		out = append(out, rec)
	}
	return out
}

func TestRealFixedWorkAndOrderedProgress(t *testing.T) {
	r := NewRunner("test-node", t.TempDir())
	got := r.Run(testConfig("real_run", 3))
	const golden uint64 = 8843627163163507496 // Independently computed with Python hashlib.
	wantChecksum := golden
	wantChecksum += golden
	wantChecksum += golden
	if got.Error != "" || got.StepsExpected != 3 || got.StepsValid != 3 || got.StepsErrors != 0 || got.Result.Checksum != wantChecksum || got.Result.BytesProcessed != 3*65536*100 {
		t.Fatalf("wrong fixed-work result: %+v", got)
	}
	b, err := r.Progress("real_run")
	if err != nil {
		t.Fatal(err)
	}
	recs := records(t, b)
	wantEvents := []string{"start", "tick", "result", "tick", "result", "tick", "result", "end"}
	if len(recs) != len(wantEvents) {
		t.Fatalf("records: %s", b)
	}
	for i, rec := range recs {
		if rec.Event != wantEvents[i] || rec.Sequence != i+1 || rec.WorkerPID != os.Getpid() || rec.WorkerBootNS <= 0 || rec.WorkerInstance != got.WorkerInstance || rec.UnixNS <= 0 || (i > 0 && rec.MonotonicNS < recs[i-1].MonotonicNS) {
			t.Fatalf("invalid record %d: %+v", i, rec)
		}
		if rec.Event == "result" && (!rec.Valid || rec.Checksum != golden) {
			t.Fatalf("invalid result: %+v", rec)
		}
	}
	if got.WorkerEndNs <= got.WorkerStartNs || got.ServiceNs <= 0 || got.Node != "test-node" {
		t.Fatalf("missing identity: %+v", got)
	}
	if again := r.Run(testConfig("real_run", 1)); again.Error == "" {
		t.Fatal("reused progress identity")
	}
}

func TestRemoteFailureContinuesAndRecoveryIsValidated(t *testing.T) {
	calls := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		calls++
		var c revisionworkload.Config
		if err := json.NewDecoder(req.Body).Decode(&c); err != nil {
			t.Error(err)
		}
		if c.Kind != "cpu" || c.Bytes != 65536 || c.Iterations != 100 {
			t.Errorf("wrong remote work: %+v", c)
		}
		if calls == 1 {
			http.Error(w, "outage", 503)
			return
		}
		res, err := revisionworkload.Run(c)
		if err != nil {
			t.Error(err)
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"result": res})
	}))
	defer server.Close()
	r := NewRunner("test", t.TempDir())
	c := testConfig("dependency", 3)
	c.Continuity.RemoteURL = server.URL + "/run"
	got := r.Run(c)
	if calls != 3 || got.StepsErrors != 1 || got.StepsValid != 2 || got.Error == "" {
		t.Fatalf("failure hidden or loop stopped: calls=%d reply=%+v", calls, got)
	}
	b, _ := r.Progress("dependency")
	recs := records(t, b)
	if recs[2].Event != "error" || recs[2].Valid || recs[2].Checksum == 0 || recs[4].Event != "result" || !recs[4].Valid || recs[len(recs)-1].Event != "end" {
		t.Fatalf("wrong dependency records: %s", b)
	}
}

func TestRemoteWrongChecksumAndTimeoutAreUnsuccessful(t *testing.T) {
	for _, mode := range []string{"checksum", "timeout"} {
		t.Run(mode, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
				if mode == "timeout" {
					time.Sleep(750 * time.Millisecond)
				}
				fmt.Fprint(w, `{"result":{"checksum":1,"bytes_processed":6553600}}`)
			}))
			defer server.Close()
			r := NewRunner("test", t.TempDir())
			c := testConfig(mode, 1)
			c.Continuity.RemoteURL = server.URL
			start := time.Now()
			got := r.Run(c)
			if got.StepsValid != 0 || got.StepsErrors != 1 || got.Error == "" || time.Since(start) > 700*time.Millisecond {
				t.Fatalf("dependency accepted or unbounded: %+v", got)
			}
		})
	}
}

func TestRunIDsAndWorkBounds(t *testing.T) {
	dir := t.TempDir()
	r := NewRunner("test", dir)
	for _, id := range []string{"", "../escape", "a/b", ".hidden", "a.b", "a b"} {
		if got := r.Run(testConfig(id, 1)); got.Error == "" {
			t.Fatalf("accepted unsafe id %q", id)
		}
		if _, err := r.Progress(id); err == nil {
			t.Fatalf("accepted unsafe progress id %q", id)
		}
	}
	for i, mutate := range []func(*Config){func(c *Config) { c.Continuity.Steps = 0 }, func(c *Config) { c.Continuity.Steps = 3601 }, func(c *Config) { c.Continuity.IntervalMS = 9 }, func(c *Config) { c.Continuity.IntervalMS = 60001 }, func(c *Config) { c.Bytes = 32 }, func(c *Config) { c.Continuity.RemoteURL = "file:///tmp/x" }} {
		c := testConfig(fmt.Sprintf("bad%d", i), 1)
		mutate(&c)
		if got := r.Run(c); got.Error == "" {
			t.Fatalf("accepted invalid config %+v", c)
		}
	}
	files, _ := os.ReadDir(dir)
	if len(files) != 0 {
		t.Fatalf("invalid configs wrote files: %v", files)
	}
}

func TestProgressIsVisibleDuringRunAndOmitsPartialRecord(t *testing.T) {
	dir := t.TempDir()
	r := NewRunner("test", dir)
	c := testConfig("live", 4)
	c.Continuity.IntervalMS = 40
	done := make(chan Reply, 1)
	go func() { done <- r.Run(c) }()
	deadline := time.Now().Add(time.Second)
	for {
		b, _ := r.Progress("live")
		if bytes.Contains(b, []byte(`"event":"result"`)) {
			select {
			case <-done:
				t.Fatal("progress blocked until completion")
			default:
			}
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("progress not visible")
		}
		time.Sleep(time.Millisecond)
	}
	if got := <-done; got.Error != "" {
		t.Fatal(got.Error)
	}
	if err := os.WriteFile(filepath.Join(dir, "partial.jsonl"), []byte("{\"event\":\"start\"}\n{\"event\":"), 0600); err != nil {
		t.Fatal(err)
	}
	b, err := r.Progress("partial")
	if err != nil || string(b) != "{\"event\":\"start\"}\n" {
		t.Fatalf("partial record exposed %q %v", b, err)
	}
}
