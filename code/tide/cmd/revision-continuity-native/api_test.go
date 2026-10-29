package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func testPeers() []nodeInfo {
	return []nodeInfo{
		{Name: "ru-local", Address: "127.0.0.1:32040", Role: "edge", CPUs: 2, Slots: 1, Stub: "local", PID: 1, Policy: "tide", Image: workerImage, Commands: []string{"/worker", "--mode=paix", "--node=ru-local", "--tmpdir=/owned"}},
		{Name: "ru-cloud", Address: "127.0.0.1:32042", Role: "cloud", CPUs: 2, Slots: 1, Stub: "cloud", PID: 2, Policy: "tide", Image: workerImage, Commands: []string{"/worker", "--mode=paix", "--node=ru-cloud", "--tmpdir=/owned"}},
	}
}

func TestPeerWorkerCommandMustMatchFrozenProfile(t *testing.T) {
	p := testPeers()
	a := newAPI(p[0], time.Second, nil, nil, nil, nil)
	p[1].Commands = []string{"/worker", "--mode=http"}
	b, _ := json.Marshal(p)
	if w := call(a, "POST", "/peers", string(b)); w.Code != 400 {
		t.Fatal("different worker accepted")
	}
}
func call(h *api, method, path, body string) *httptest.ResponseRecorder {
	w := httptest.NewRecorder()
	h.handler().ServeHTTP(w, httptest.NewRequest(method, path, bytes.NewBufferString(body)))
	return w
}
func TestPeersOneShotAndStrictInventory(t *testing.T) {
	p := testPeers()
	a := newAPI(p[0], time.Second, nil, nil, nil, nil)
	bad := append([]nodeInfo(nil), p...)
	bad[1].CPUs = 4
	b, _ := json.Marshal(bad)
	if w := call(a, "POST", "/peers", string(b)); w.Code != 400 {
		t.Fatalf("bad budget %d", w.Code)
	}
	b, _ = json.Marshal(p)
	if w := call(a, "POST", "/peers", string(b)); w.Code != 200 {
		t.Fatal(w.Body.String())
	}
	if w := call(a, "POST", "/peers", string(b)); w.Code != 409 {
		t.Fatal("duplicate join accepted")
	}
	if w := call(a, "POST", "/peers", string(b)+" {}"); w.Code != 400 {
		t.Fatal("trailing JSON accepted")
	}
}
func TestGateOwnFlagAndStrictBody(t *testing.T) {
	dir := t.TempDir()
	g, err := newGate(dir, "ru-local")
	if err != nil {
		t.Fatal(err)
	}
	defer g.close()
	a := newAPI(testPeers()[0], time.Second, nil, nil, nil, g)
	for _, s := range []string{`{}`, `{"enabled":null}`, `{"enabled":true,"extra":1}`, `{"enabled":true} {}`} {
		if w := call(a, "POST", "/gate", s); w.Code != 400 {
			t.Fatalf("accepted %s", s)
		}
	}
	if w := call(a, "GET", "/gate", ""); w.Code != 405 {
		t.Fatal("gate GET mutated")
	}
	if w := call(a, "POST", "/gate", `{"enabled":true}`); w.Code != 200 {
		t.Fatal(w.Body.String())
	}
	if _, err := os.Stat(filepath.Join(dir, "gate.enabled")); err != nil {
		t.Fatal(err)
	}
	if !g.reject([]rpcEvent{{From: "ru-cloud", Type: "native", RequestID: "x"}}) || g.reject([]rpcEvent{{From: "ru-local", Type: "self"}}) {
		t.Fatal("gate source filtering wrong")
	}
	if w := call(a, "POST", "/gate", `{"enabled":false}`); w.Code != 200 {
		t.Fatal(w.Body.String())
	}
	if _, err := os.Stat(filepath.Join(dir, "gate.enabled")); !os.IsNotExist(err) {
		t.Fatal("own flag retained")
	}
	if g.reject([]rpcEvent{{From: "ru-cloud"}}) {
		t.Fatal("released gate rejects")
	}
	data, _ := os.ReadFile(filepath.Join(dir, "gate-events.jsonl"))
	if !bytes.Contains(data, []byte(`"request_id":"x"`)) {
		t.Fatal("rejection evidence missing")
	}
}

type testPending struct {
	done   chan struct{}
	result nativeResult
}

func (p *testPending) waitData() <-chan struct{} { return p.done }
func (p *testPending) outcome() nativeResult     { return p.result }
func (p *testPending) receiver() destination     { return destination{Cloud: true, ID: "ru-cloud"} }
func TestRunRetainsNativeResultAndClientID(t *testing.T) {
	peers := testPeers()
	p := &testPending{done: make(chan struct{}), result: nativeResult{Status: 3, Success: true, Result: []byte(`{"checksum":42,"node":"ru-cloud"}`)}}
	close(p.done)
	a := newAPI(peers[0], time.Second, func(context.Context, string, []byte) (pendingRequest, error) { return p, nil }, nil, nil, nil)
	b, _ := json.Marshal(peers)
	call(a, "POST", "/peers", string(b))
	req := httptest.NewRequest("POST", "/run", bytes.NewBufferString(`{"kind":"cpu","bytes":65536,"iterations":100}`))
	req.Header.Set("X-Request-ID", "client-7")
	w := httptest.NewRecorder()
	a.handler().ServeHTTP(w, req)
	var r runResponse
	_ = json.Unmarshal(w.Body.Bytes(), &r)
	if w.Code != 200 || r.ClientRequestID != "client-7" || r.NativeStatus != 3 || r.Status != "success" || !bytes.Equal(r.Result, p.result.Result) || r.After.ID != "ru-cloud" {
		t.Fatalf("lost envelope %s", w.Body.String())
	}
	var timestamps map[string]any
	_ = json.Unmarshal(w.Body.Bytes(), &timestamps)
	if timestamps["submitted_monotonic_ns"] == nil || timestamps["returned_monotonic_ns"] == nil {
		t.Fatal("origin monotonic cohort timestamps missing")
	}
	if w := call(a, "POST", "/run", `{"kind":"io","bytes":8,"iterations":1}`); w.Code != 400 {
		t.Fatal("unfrozen kernel accepted")
	}
}
func TestTimeoutPreservesUnresolvedBackend(t *testing.T) {
	peers := testPeers()
	p := &testPending{done: make(chan struct{})}
	a := newAPI(peers[0], time.Millisecond, func(context.Context, string, []byte) (pendingRequest, error) { return p, nil }, nil, nil, nil)
	b, _ := json.Marshal(peers)
	call(a, "POST", "/peers", string(b))
	w := call(a, "POST", "/run", `{"kind":"memory","bytes":16777216,"iterations":4}`)
	if w.Code != 504 {
		t.Fatal(w.Body.String())
	}
	if a.submitted.Load()-a.completed.Load() != 1 {
		t.Fatal("backend timeout erased")
	}
	close(p.done)
}

func TestClockEndpointSuppliesMonotonicBracket(t *testing.T) {
	a := newAPI(testPeers()[0], time.Second, nil, nil, nil, nil)
	w := call(a, "GET", "/clock", "")
	var got map[string]any
	_ = json.Unmarshal(w.Body.Bytes(), &got)
	if w.Code != 200 || got["monotonic_ns"] == nil || got["unix_ns"] == nil || got["unix_before_ns"] == nil || got["unix_after_ns"] == nil {
		t.Fatalf("clock missing %s", w.Body.String())
	}
	if w := call(a, "POST", "/clock", ""); w.Code != 405 {
		t.Fatal("clock POST accepted")
	}
}
