package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/ict/tide/pkg/dev"
)

func testPeers(initial string) []nodeInfo {
	p := []nodeInfo{
		{Name: "A", Address: "127.0.0.1:34060", Role: "edge", CPUs: 2, Slots: 1, Stub: "stub-a", PID: 1, Initial: initial, Image: workerImage, Commands: workerCommands("A")},
		{Name: "B", Address: "127.0.0.2:34060", Role: "edge", CPUs: 2, PID: 2, Initial: initial, Image: workerImage, Commands: workerCommands("B")},
		{Name: "C", Address: "127.0.0.3:34060", Role: "cloud", CPUs: 0, PID: 3, Initial: initial, Image: workerImage},
	}
	if initial == "warm" {
		p[1].Stub = "stub-b"
		p[1].Slots = 1
	}
	return p
}

func call(a *api, method, path, body string) *httptest.ResponseRecorder {
	w := httptest.NewRecorder()
	a.handler().ServeHTTP(w, httptest.NewRequest(method, path, bytes.NewBufferString(body)))
	return w
}

func TestThreeNodeInventoryColdBAndZeroCapacityControl(t *testing.T) {
	for _, initial := range []string{"warm", "cold"} {
		p := testPeers(initial)
		a := newAPI(p[1], time.Second, nil, nil, func() map[string]any { return map[string]any{"executors": []string{}} })
		b, _ := json.Marshal(p)
		if w := call(a, "POST", "/peers", string(b)); w.Code != 200 {
			t.Fatal(w.Body.String())
		}
		if w := call(a, "GET", "/health", ""); w.Code != 200 {
			t.Fatal("cold health incorrectly requires stub")
		}
		if w := call(a, "GET", "/peers", ""); w.Code != 200 {
			t.Fatal("inventory GET unavailable")
		}
		if w := call(a, "POST", "/run", `{"kind":"cpu","bytes":65536,"iterations":1000,"request_id":"x"}`); w.Code != 403 {
			t.Fatal("submission origin is not restricted to Fn A")
		}
		p[2].CPUs = 2
		if validatePeers(a.self, p) == nil {
			t.Fatal("C application capacity silently enabled")
		}
	}
}

type testPending struct {
	done  chan struct{}
	value nativeResult
}

func (p *testPending) waitData() <-chan struct{} { return p.done }
func (p *testPending) outcome() nativeResult     { return p.value }
func (p *testPending) receiver() destination     { return destination{ID: "actual-b-stub"} }

func TestNativePreservesPayloadIDAndFailureResult(t *testing.T) {
	p := testPeers("cold")
	pending := &testPending{done: make(chan struct{}), value: nativeResult{Status: 4, Result: []byte(`{"request_id":"req-1","error":"failed"}`), Error: "native failure"}}
	close(pending.done)
	var submittedID string
	a := newAPI(p[0], time.Second, func(_ context.Context, id string, b []byte) (pendingRequest, error) {
		submittedID = id
		var c map[string]any
		_ = json.Unmarshal(b, &c)
		if c["request_id"] != "req-1" {
			t.Error("payload ID changed")
		}
		return pending, nil
	}, nil, nil)
	b, _ := json.Marshal(p)
	call(a, "POST", "/peers", string(b))
	w := call(a, "POST", "/run", `{"kind":"cpu","bytes":65536,"iterations":1000,"request_id":"req-1"}`)
	var result runResponse
	if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil {
		t.Fatal(err)
	}
	if w.Code != 503 || result.RequestID != "req-1" || submittedID != "req-1" || result.Status != "failure" || !bytes.Equal(result.Result, pending.value.Result) || !bytes.Equal(result.ResultJSON, pending.value.Result) {
		t.Fatalf("native failure evidence lost: %s", w.Body.String())
	}
	if w := call(a, "POST", "/run", `{"kind":"cpu","bytes":65536,"iterations":100,"request_id":"bad"}`); w.Code != 400 {
		t.Fatal("wrong fixed work accepted")
	}
}

func TestProfileAcceptsPositiveDeadlineAndNativeTwoCPUAllocation(t *testing.T) {
	c := dev.DefaultConfig()
	c.QoSMS = 500
	c.TTLMS = 86400000
	c.C4Functions = map[string]bool{"cpu": true}
	c.NodeExecutionMS = map[string]int{"A": 200, "B": 200, "C": 200}
	if err := validateNativeConfig(c); err != nil {
		t.Fatal(err)
	}
	c.QoSMS = 750
	if err := validateNativeConfig(c); err != nil {
		t.Fatal("deadline unnecessarily hardcoded")
	}
	c.NodeExecutionMS["B"] = 0
	if validateNativeConfig(c) == nil {
		t.Fatal("uncalibrated B accepted")
	}
	if dev.GetReqResource().CpuNum != 2 || dev.GetStubResource().CpuNum != 2 {
		t.Fatal("native processor allocation no longer bounds one active two-CPU routine on a two-CPU edge")
	}
}
