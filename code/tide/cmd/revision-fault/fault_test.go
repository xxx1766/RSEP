package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/ict/tide/pkg/revisionworkload"
)

func TestLocalEligibilityAndDeadlineFeasibility(t *testing.T) {
	nodes := []Candidate{{FinishNS: 10, EstimateNS: 5}, {FinishNS: 0, EstimateNS: 5, NetworkNS: 2}}
	if got := Choose(nodes, true, 0, 20); got != 0 {
		t.Fatalf("local fast path=%d", got)
	}
	if got := Choose(nodes, false, 0, 20); got != 1 {
		t.Fatalf("entry-only=%d", got)
	}
	if got := Choose(nodes, true, 0, 10); got != 1 {
		t.Fatalf("local infeasible=%d", got)
	}
	if got := Choose(nodes, true, 0, 4); got != -1 {
		t.Fatalf("both infeasible=%d", got)
	}
}

func TestGateCancelsInflightBlocksNewAndRecovers(t *testing.T) {
	started := make(chan struct{}, 1)
	remote := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("X-Request-ID") == "inflight" {
			io.Copy(io.Discard, r.Body)
			started <- struct{}{}
			<-r.Context().Done()
			return
		}
		w.Write([]byte("real remote response"))
	}))
	defer remote.Close()
	gate := NewGate(remote.URL)
	proxy := httptest.NewServer(gate)
	defer proxy.Close()
	done := make(chan int, 1)
	go func() {
		req, _ := http.NewRequest("POST", proxy.URL, bytes.NewReader([]byte("work")))
		req.Header.Set("X-Request-ID", "inflight")
		res, err := http.DefaultClient.Do(req)
		if err != nil {
			done <- 0
			return
		}
		defer res.Body.Close()
		done <- res.StatusCode
	}()
	select {
	case <-started:
	case <-time.After(time.Second):
		t.Fatal("remote not reached")
	}
	ids := gate.SetConnected(false)
	if len(ids) != 1 || ids[0] != "inflight" {
		t.Fatalf("fault active IDs %v", ids)
	}
	select {
	case status := <-done:
		if status != 503 {
			t.Fatalf("inflight status %d", status)
		}
	case <-time.After(time.Second):
		t.Fatal("inflight not canceled")
	}
	res, err := http.Post(proxy.URL, "application/json", bytes.NewReader([]byte("work")))
	if err != nil {
		t.Fatal(err)
	}
	res.Body.Close()
	if res.StatusCode != 503 {
		t.Fatalf("disconnected status %d", res.StatusCode)
	}
	gate.SetConnected(true)
	res, err = http.Post(proxy.URL, "application/json", bytes.NewReader([]byte("work")))
	if err != nil {
		t.Fatal(err)
	}
	res.Body.Close()
	if res.StatusCode != 200 {
		t.Fatalf("recovery status %d", res.StatusCode)
	}
}

func TestWorkerExecutesActualKernelAndReturnsIdentity(t *testing.T) {
	worker := httptest.NewServer(NewWorker(nil))
	defer worker.Close()
	work := revisionworkload.Config{Kind: "cpu", Bytes: 128, Iterations: 2}
	data, _ := json.Marshal(work)
	req, _ := http.NewRequestWithContext(context.Background(), "POST", worker.URL+"/run", bytes.NewReader(data))
	req.Header.Set("X-Request-ID", "test-1")
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()
	var reply WorkerReply
	if err = json.NewDecoder(res.Body).Decode(&reply); err != nil {
		t.Fatal(err)
	}
	want, err := revisionworkload.Run(work)
	if err != nil {
		t.Fatal(err)
	}
	if reply.Result != want || reply.ServiceNS <= 0 || reply.RequestID != "test-1" {
		t.Fatalf("reply %+v want %+v", reply, want)
	}
}

func TestRealWorkerKeepsSerialSlotAfterClientCancellation(t *testing.T) {
	var logs bytes.Buffer
	handler := NewWorker(&logs)
	server := httptest.NewServer(handler)
	defer server.Close()
	work := revisionworkload.Config{Kind: "cpu", Bytes: 65536, Iterations: 10000}
	data, _ := json.Marshal(work)
	ctx, cancel := context.WithCancel(context.Background())
	req, _ := http.NewRequestWithContext(ctx, "POST", server.URL+"/run", bytes.NewReader(data))
	req.Header.Set("X-Request-ID", "cancelled")
	done := make(chan struct{})
	go func() {
		res, _ := http.DefaultClient.Do(req)
		if res != nil {
			res.Body.Close()
		}
		close(done)
	}()
	deadline := time.Now().Add(time.Second)
	for {
		handler.mu.Lock()
		active := handler.active
		handler.mu.Unlock()
		if active == 1 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("kernel did not start")
		}
		time.Sleep(time.Millisecond)
	}
	cancel()
	<-done
	small, _ := json.Marshal(revisionworkload.Config{Kind: "cpu", Bytes: 128, Iterations: 2})
	req, _ = http.NewRequest("POST", server.URL+"/run", bytes.NewReader(small))
	req.Header.Set("X-Request-ID", "second")
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	res.Body.Close()
	handler.mu.Lock()
	data = append([]byte(nil), logs.Bytes()...)
	handler.mu.Unlock()
	decoder := json.NewDecoder(bytes.NewReader(data))
	var first, second WorkerReply
	if err = decoder.Decode(&first); err != nil {
		t.Fatal(err)
	}
	if err = decoder.Decode(&second); err != nil {
		t.Fatal(err)
	}
	if first.RequestID != "cancelled" || !first.ClientCancelled || second.RequestID != "second" || second.StartUnixNS < first.FinishUnixNS {
		t.Fatalf("first %+v second %+v", first, second)
	}
}
