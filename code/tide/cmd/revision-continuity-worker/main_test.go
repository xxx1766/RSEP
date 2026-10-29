package main

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/ict/tide/pkg/revisioncontinuity"
)

func TestHTTPProgressAndHealthWhileRunIsActive(t *testing.T) {
	worker = revisioncontinuity.NewRunner("test-node", t.TempDir())
	server := httptest.NewServer(newMux())
	defer server.Close()
	done := make(chan []byte, 1)
	go func() {
		resp, err := http.Post(server.URL+"/run", "application/json", strings.NewReader(`{"kind":"cpu","bytes":65536,"iterations":100,"continuity":{"run_id":"http_live","steps":5,"interval_ms":50}}`))
		if err != nil {
			done <- nil
			return
		}
		defer resp.Body.Close()
		b, _ := io.ReadAll(resp.Body)
		done <- b
	}()
	deadline := time.Now().Add(time.Second)
	for {
		resp, err := http.Get(server.URL + "/progress?run_id=http_live")
		if err != nil {
			t.Fatal(err)
		}
		b, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		if bytes.Contains(b, []byte(`"event":"result"`)) {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("progress not observable")
		}
		time.Sleep(time.Millisecond)
	}
	select {
	case <-done:
		t.Fatal("run ended before observations")
	default:
	}
	resp, err := http.Get(server.URL + "/health")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	var health revisioncontinuity.Reply
	if err := json.NewDecoder(resp.Body).Decode(&health); err != nil {
		t.Fatal(err)
	}
	if health.WorkerInstance == "" || health.Node != "test-node" || health.WorkerPID <= 0 {
		t.Fatalf("invalid identity %+v", health)
	}
	b := <-done
	var reply revisioncontinuity.Reply
	if err := json.Unmarshal(b, &reply); err != nil {
		t.Fatalf("reply %q: %v", b, err)
	}
	if reply.StepsValid != 5 || reply.Error != "" || len(b) >= 8192 {
		t.Fatalf("invalid reply %s", b)
	}
}

func TestHTTPShortCPUAndBadRequests(t *testing.T) {
	worker = revisioncontinuity.NewRunner("test-node", t.TempDir())
	mux := newMux()
	for _, test := range []struct {
		method, path, body string
		status             int
	}{
		{"POST", "/run", `{"kind":"cpu","bytes":65536,"iterations":100}`, 200},
		{"POST", "/run", `{`, 400},
		{"POST", "/run", strings.Repeat("x", 8193), 400},
		{"GET", "/run", "", 405},
		{"GET", "/progress?run_id=../escape", "", 400},
		{"GET", "/progress?run_id=missing", "", 404},
		{"POST", "/progress?run_id=x", "", 405},
	} {
		req := httptest.NewRequest(test.method, test.path, strings.NewReader(test.body))
		rec := httptest.NewRecorder()
		mux.ServeHTTP(rec, req)
		if rec.Code != test.status {
			t.Errorf("%s %s: status=%d body=%s", test.method, test.path, rec.Code, rec.Body.String())
		}
		if test.status == 200 {
			var got revisioncontinuity.Reply
			if err := json.Unmarshal(rec.Body.Bytes(), &got); err != nil {
				t.Fatal(err)
			}
			if got.Result.Checksum != revisioncontinuity.GoldenChecksum || got.Error != "" {
				t.Fatalf("wrong short result %+v", got)
			}
		}
	}
}
