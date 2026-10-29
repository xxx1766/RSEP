package main

import (
	"context"
	"encoding/json"
	"testing"
	"time"
)

func TestLongPayloadPreservedForRealWorker(t *testing.T) {
	p := &testPending{done: make(chan struct{}), result: nativeResult{Success: true, Result: []byte(`{}`)}}
	close(p.done)
	var got []byte
	a := newAPI(testPeers()[0], time.Second, func(_ context.Context, _ string, b []byte) (pendingRequest, error) { got = b; return p, nil }, nil, nil, nil)
	b, _ := json.Marshal(testPeers())
	call(a, "POST", "/peers", string(b))
	w := call(a, "POST", "/run", `{"kind":"cpu","bytes":65536,"iterations":100,"continuity":{"run_id":"test-task","steps":150,"interval_ms":1000}}`)
	if w.Code != 200 {
		t.Fatalf("long payload rejected: %s", w.Body.String())
	}
	var body map[string]json.RawMessage
	json.Unmarshal(got, &body)
	if len(body["continuity"]) == 0 {
		t.Fatal("long job silently converted to short job")
	}
}
