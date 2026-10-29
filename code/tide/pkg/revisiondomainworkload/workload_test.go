package revisiondomainworkload

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestRealKernelGoldenAndDurableJournal(t *testing.T) {
	dir := t.TempDir()
	w, err := New("A", dir)
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()
	c := Config{Kind: "cpu", Bytes: 65536, Iterations: 1000, RequestID: "request-1"}
	r := w.Run(c)
	if r.Error != "" || r.RequestID != c.RequestID || r.Result.Checksum != 3147045699343202425 || r.Result.BytesProcessed != 65536000 {
		t.Fatalf("wrong real work %+v", r)
	}
	if r.WorkerInstance == "" || r.WorkerPID != os.Getpid() || r.WorkerBootNS <= 0 || r.WorkerStartNS <= 0 || r.WorkerEndNS < r.WorkerStartNS || r.WorkerStartMonotonicNS <= 0 || r.WorkerEndMonotonicNS <= r.WorkerStartMonotonicNS {
		t.Fatalf("missing actual timestamps %+v", r)
	}
	f, err := os.Open(filepath.Join(dir, "requests.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	scan := bufio.NewScanner(f)
	var records []Record
	for scan.Scan() {
		var rec Record
		if err := json.Unmarshal(scan.Bytes(), &rec); err != nil {
			t.Fatal(err)
		}
		records = append(records, rec)
	}
	if err := scan.Err(); err != nil {
		t.Fatal(err)
	}
	if len(records) != 2 || records[0].Event != "start" || records[1].Event != "result" || records[0].Sequence != 1 || records[1].Sequence != 2 || records[1].Reply != r {
		t.Fatalf("journal not visible before return: %+v", records)
	}
	if w.Identity().WorkerInstance != r.WorkerInstance {
		t.Fatal("identity changed")
	}
}

func TestFrozenWorkAndSafeRequestID(t *testing.T) {
	for _, c := range []Config{{Kind: "cpu", Bytes: 65536, Iterations: 1000, RequestID: ""}, {Kind: "cpu", Bytes: 65536, Iterations: 100, RequestID: "id"}, {Kind: "memory", Bytes: 65536, Iterations: 1000, RequestID: "id"}, {Kind: "cpu", Bytes: 65536, Iterations: 1000, RequestID: "bad\nid"}} {
		if Validate(c) == nil {
			t.Fatalf("accepted invalid request %+v", c)
		}
	}
	w, err := New("A", t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()
	if got := w.Run(Config{}); got.Error == "" {
		t.Fatal("invalid request reported success")
	}
}

func TestReadyIsDistinctDurableAndExactlyOnce(t *testing.T) {
	dir := t.TempDir()
	w, err := New("B", dir)
	if err != nil {
		t.Fatal(err)
	}
	defer w.Close()
	for i := 0; i < 2; i++ {
		if err := w.Ready(); err != nil {
			t.Fatal(err)
		}
	}
	data, err := os.ReadFile(filepath.Join(dir, "requests.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	var record Record
	if err := json.Unmarshal(data, &record); err != nil {
		t.Fatal(err)
	}
	if record.Event != "instance_ready" || record.Sequence != 1 || record.RequestID != "" || record.WorkerInstance != w.Identity().WorkerInstance || record.ObservedUnixNS < record.WorkerBootNS || record.ObservedMonotonicNS <= 0 || record.WorkerStartNS != 0 {
		t.Fatalf("invalid readiness: %+v", record)
	}
}
