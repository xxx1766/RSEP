package main

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

func TestHTTPRunKeepsOfferedRequestsAndServiceTimes(t *testing.T) {
	var active, maxActive atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := active.Add(1)
		defer active.Add(-1)
		for old := maxActive.Load(); n > old; old = maxActive.Load() {
			if maxActive.CompareAndSwap(old, n) {
				break
			}
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"service_ns": 1234, "result": map[string]any{"bytes_processed": 16}})
	}))
	defer server.Close()
	rows, _, err := runHTTP(runConfig{Requests: 6, RPS: 200, Timeout: time.Second, QoSMS: 500, URL: server.URL, Work: []byte(`{"kind":"cpu","bytes":8,"iterations":2}`)})
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 6 {
		t.Fatalf("got%d rows", len(rows))
	}
	for _, r := range rows {
		if r.Status != 200 || r.Service != 1234 || r.Reply < r.Submit {
			t.Fatalf("bad row %+v", r)
		}
	}
	if maxActive.Load() != 1 {
		t.Fatal("serial FCFS baseline concurrency changed")
	}
}

func TestQueuedRequestsKeepTimeoutOutcomes(t *testing.T) {
	var calls atomic.Int32
	release := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		_, _ = io.Copy(io.Discard, r.Body)
		select {
		case <-r.Context().Done():
		case <-release:
		}
	}))
	defer server.Close()
	defer close(release)
	rows, _, err := runHTTP(runConfig{Requests: 4, RPS: 10000, Timeout: 20 * time.Millisecond, QoSMS: 10, URL: server.URL, Work: []byte(`{"kind":"cpu","bytes":8,"iterations":2}`)})
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 4 {
		t.Fatal("lost queued requests")
	}
	for _, r := range rows {
		if r.Status != 504 && r.Status != 503 {
			t.Fatalf("want timeout or quarantined-channel failure %+v", r)
		}
	}
	if calls.Load() == 0 {
		t.Fatal("fixture never exercised a real HTTP request")
	}
}

func TestTimedOutKernelCannotOverlapNextKernel(t *testing.T) {
	var active, maxActive atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.Copy(io.Discard, r.Body)
		n := active.Add(1)
		defer active.Add(-1)
		for old := maxActive.Load(); n > old; old = maxActive.Load() {
			if maxActive.CompareAndSwap(old, n) {
				break
			}
		}
		// Like the real kernel, this work continues even if the caller times out.
		time.Sleep(80 * time.Millisecond)
		_ = json.NewEncoder(w).Encode(map[string]any{"service_ns": 80000000, "result": map[string]any{"bytes_processed": 16}})
	}))
	defer server.Close()
	rows, _, err := runHTTP(runConfig{Requests: 8, RPS: 50, Timeout: 100 * time.Millisecond, QoSMS: 50, URL: server.URL, Work: []byte(`{"kind":"cpu","bytes":8,"iterations":2}`)})
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 8 {
		t.Fatal("lost outcomes")
	}
	if maxActive.Load() != 1 {
		t.Fatalf("timed out work overlapped another kernel: max=%d", maxActive.Load())
	}
}
