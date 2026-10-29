package main

import (
	"encoding/json"
	"github.com/ict/tide/pkg/revisionworkload"
	"testing"
)

func TestMeasuredWorkerMatchesKernel(t *testing.T) {
	nodeID = "nj-test"
	tempDir = t.TempDir()
	for _, kind := range []string{"cpu", "memory", "io"} {
		c := revisionworkload.Config{Kind: kind, Bytes: 4096, Iterations: 2, TempDir: tempDir}
		want, err := revisionworkload.Run(c)
		if err != nil {
			t.Fatal(err)
		}
		data, _ := json.Marshal(c)
		got := execute(data)
		if got.Error != "" || got.Result != want {
			t.Fatalf("%s: %+v != %+v", kind, got, want)
		}
		if got.Node != nodeID || got.WorkerPID <= 0 || got.ServiceNs <= 0 || got.WorkerEndNs < got.WorkerStartNs {
			t.Fatalf("invalid measurement %+v", got)
		}
	}
}

func TestBadInputDoesNotReportSuccess(t *testing.T) {
	for _, data := range []string{"{", `{"kind":"invalid","bytes":4096,"iterations":2}`} {
		if got := execute([]byte(data)); got.Error == "" {
			t.Fatalf("accepted invalid input %s", data)
		}
	}
}
