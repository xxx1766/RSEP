package main

import (
	"net/http/httptest"
	"reflect"
	"testing"
	"time"

	"github.com/ict/tide/pkg/revisionworkload"
)

func TestSavedOfferedTraceDeterministic(t *testing.T) {
	a := GenerateTrace(42, 30, 300*time.Millisecond)
	if len(a) != 9 || !reflect.DeepEqual(a, GenerateTrace(42, 30, 300*time.Millisecond)) || reflect.DeepEqual(a, GenerateTrace(43, 30, 300*time.Millisecond)) {
		t.Fatal("invalid recorded trace generation")
	}
	for i := 1; i < len(a); i++ {
		if a[i] <= a[i-1] {
			t.Fatal("nonmonotone arrivals")
		}
	}
}

func TestBoundedRunRetainsFaultFailuresAndRecovery(t *testing.T) {
	local := httptest.NewServer(NewWorker(nil))
	defer local.Close()
	remote := httptest.NewServer(NewWorker(nil))
	defer remote.Close()
	config := RunConfig{LocalURL: local.URL, RemoteURL: remote.URL, Phase: 100 * time.Millisecond, Timeout: time.Second, QoS: 100 * time.Millisecond, Estimate: time.Millisecond, Work: revisionworkload.Config{Kind: "cpu", Bytes: 128, Iterations: 2}, Trace: GenerateTrace(42, 100, 300*time.Millisecond)}
	rows, events, _, err := Run(config)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 30 || len(events) != 3 {
		t.Fatalf("rows %d events %d", len(rows), len(events))
	}
	successes, failures := map[string]int{}, map[string]int{}
	for _, r := range rows {
		if r.Node != 1 {
			t.Fatal("disabled local eligibility routed local")
		}
		if r.Status == 200 {
			successes[r.SubmitPhase]++
		} else {
			failures[r.SubmitPhase]++
		}
	}
	if successes["connected"] == 0 || failures["disconnected"] == 0 || successes["recovered"] == 0 {
		t.Fatalf("successes %v failures %v", successes, failures)
	}
	config.LocalEligible = true
	rows, _, _, err = Run(config)
	if err != nil {
		t.Fatal(err)
	}
	for _, r := range rows {
		if r.Node != 0 || r.Status != 200 {
			t.Fatalf("preserved local request %+v", r)
		}
	}
}
