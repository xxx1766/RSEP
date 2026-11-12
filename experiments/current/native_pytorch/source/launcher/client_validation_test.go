package main

import (
	"encoding/csv"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestLegacyClientReadsAndValidatesCompletedBody(t *testing.T) {
	t.Setenv("TIDE_LEGACY_HOST_ROOT", "/dev/shm/owned")
	for _, valid := range []bool{true, false} {
		server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if valid {
				fmt.Fprintf(w, `{"success":true,"request_id":%q,"inferences_completed":10}`, r.Header.Get("X-Request-ID"))
			} else {
				fmt.Fprint(w, `{"success":false}`)
			}
		}))
		cfg := &config{phase: "measured", rps: 100, duration: 10 * time.Millisecond, outfile: filepath.Join(t.TempDir(), "raw.csv"), deadline: time.Second, endpoint: server.URL}
		if err := runClient(cfg); err != nil {
			t.Fatal(err)
		}
		f, _ := os.Open(cfg.outfile)
		rows, _ := csv.NewReader(f).ReadAll()
		f.Close()
		server.Close()
		want := "200"
		if !valid {
			want = "502"
		}
		if len(rows) != 2 || rows[1][4] != want {
			t.Fatalf("valid=%t rows=%v", valid, rows)
		}
	}
}

func TestClientTimeoutIsRetainedAsAnOutcome(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { time.Sleep(50 * time.Millisecond); w.WriteHeader(200) }))
	defer server.Close()
	cfg := &config{phase: "measured", rps: 100, duration: 20 * time.Millisecond, outfile: filepath.Join(t.TempDir(), "raw.csv"), deadline: 5 * time.Millisecond, endpoint: server.URL}
	if err := runClient(cfg); err != nil {
		t.Fatal(err)
	}
	f, _ := os.Open(cfg.outfile)
	rows, _ := csv.NewReader(f).ReadAll()
	f.Close()
	if len(rows) != 3 {
		t.Fatalf("lost offered requests: %v", rows)
	}
	for _, row := range rows[1:] {
		if row[4] != "0" {
			t.Fatalf("lost timeout: %v", row)
		}
	}
}
