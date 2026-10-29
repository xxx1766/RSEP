package main

import (
	"encoding/json"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/ict/tide/pkg/revisiondomainworkload"
)

func TestHTTPExactWorkAndWorkerHealth(t *testing.T) {
	var err error
	worker, err = revisiondomainworkload.New("A", t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer worker.Close()
	mux := newMux()
	for _, test := range []struct {
		method, path, body string
		code               int
	}{
		{"GET", "/health", "", 200},
		{"POST", "/run", `{"kind":"cpu","bytes":65536,"iterations":1000,"request_id":"edge-1"}`, 200},
		{"POST", "/run", `{"kind":"cpu","bytes":65536,"iterations":100,"request_id":"edge-2"}`, 400},
		{"POST", "/run", `{"kind":"cpu","bytes":65536,"iterations":1000,"request_id":"edge-3","extra":1}`, 400},
		{"POST", "/run", `{} {}`, 400},
		{"GET", "/run", "", 405},
	} {
		w := httptest.NewRecorder()
		mux.ServeHTTP(w, httptest.NewRequest(test.method, test.path, strings.NewReader(test.body)))
		if w.Code != test.code {
			t.Fatalf("%s %s: %d %s", test.method, test.path, w.Code, w.Body.String())
		}
		if test.path == "/run" && test.code == 200 {
			var reply revisiondomainworkload.Reply
			if err := json.Unmarshal(w.Body.Bytes(), &reply); err != nil {
				t.Fatal(err)
			}
			if reply.RequestID != "edge-1" || reply.Result.Checksum != revisiondomainworkload.GoldenChecksum {
				t.Fatalf("wrong actual result %+v", reply)
			}
		}
	}
}

func TestHealthDoesNotWaitForActiveKernel(t *testing.T) {
	dir := t.TempDir()
	var err error
	worker, err = revisiondomainworkload.New("A", dir)
	if err != nil {
		t.Fatal(err)
	}
	defer worker.Close()
	mux := newMux()
	done := make(chan struct{})
	go func() {
		defer close(done)
		mux.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest("POST", "/run", strings.NewReader(`{"kind":"cpu","bytes":65536,"iterations":1000,"request_id":"active"}`)))
	}()
	deadline := time.Now().Add(time.Second)
	for {
		data, _ := os.ReadFile(filepath.Join(dir, "requests.jsonl"))
		if strings.Contains(string(data), `"event":"start"`) {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("request never started")
		}
		time.Sleep(time.Millisecond)
	}
	health := httptest.NewRecorder()
	mux.ServeHTTP(health, httptest.NewRequest("GET", "/health", nil))
	if health.Code != 200 {
		t.Fatal(health.Body.String())
	}
	select {
	case <-done:
		t.Fatal("health waited for the active real kernel")
	default:
	}
	<-done
}
