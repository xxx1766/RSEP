package main

import (
	"context"
	"errors"
	"fmt"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/revisionworkload"
)

func configured(t *testing.T) {
	t.Helper()
	old := dev.CurrentConfig()
	t.Cleanup(func() { _ = dev.SetConfig(old) })
	c := dev.DefaultConfig()
	c.QoSByFunctionMS = map[string]int{"cpu-qos100": 100, "cpu-qos500": 500}
	if err := dev.SetConfig(c); err != nil {
		t.Fatal(err)
	}
}

func TestMixedClassesKeepCPUWorkAndAdmissionIdentity(t *testing.T) {
	configured(t)
	calls := 0
	h := mixedHandler(func(ctx context.Context, id, function string, data []byte) ([]byte, int, error) {
		calls++
		work, err := parseMixedRequest(data)
		if err != nil {
			t.Fatal(err)
		}
		if function != fmt.Sprintf("cpu-qos%d", work.DeadlineMS) || id != "test-1" {
			t.Fatal(function, id)
		}
		got, err := revisionworkload.Run(work.Config)
		want, _ := revisionworkload.Run(revisionworkload.Config{Kind: "cpu", Bytes: 65536, Iterations: 100})
		if err != nil || got != want {
			t.Fatalf("class changed kernel: %v %v", got, err)
		}
		return []byte(`{"ok":true}`), 200, nil
	}, func() string { return "test-1" })
	for _, deadline := range []int{100, 500} {
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest("POST", "/run", strings.NewReader(fmt.Sprintf(`{"kind":"cpu","bytes":65536,"iterations":100,"deadline_ms":%d}`, deadline))))
		if w.Code != 200 || w.Header().Get("X-Tide-Function") != fmt.Sprintf("cpu-qos%d", deadline) || w.Header().Get("X-Tide-Effective-Deadline-Ms") != fmt.Sprint(deadline) {
			t.Fatalf("%d: %d %v", deadline, w.Code, w.Header())
		}
	}
	if calls != 2 {
		t.Fatal(calls)
	}
}

func TestMixedRejectsUnsupportedMissingAndInvalidWork(t *testing.T) {
	configured(t)
	h := mixedHandler(func(context.Context, string, string, []byte) ([]byte, int, error) {
		t.Fatal("invalid admitted")
		return nil, 0, nil
	}, func() string { return "test" })
	for _, body := range []string{
		`{"kind":"cpu","bytes":65536,"iterations":100}`,
		`{"kind":"cpu","bytes":65536,"iterations":100,"deadline_ms":200}`,
		`{"kind":"memory","bytes":65536,"iterations":100,"deadline_ms":100}`,
		`{"kind":"cpu","bytes":8,"iterations":100,"deadline_ms":100}`,
		`{"kind":"cpu","bytes":65536,"iterations":1,"deadline_ms":100}`,
		`{"kind":"cpu","bytes":65536,"iterations":100,"deadline_ms":null}`,
	} {
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest("POST", "/run", strings.NewReader(body)))
		if w.Code != 400 {
			t.Fatalf("accepted %s: %d", body, w.Code)
		}
	}
}

func TestMissingOrWrongMapRejectsBeforeSubmissionWithAuditHeaders(t *testing.T) {
	configured(t)
	for _, classMap := range []map[string]int{nil, {"cpu-qos100": 101, "cpu-qos500": 500}, {"cpu-qos500": 500}} {
		c := dev.CurrentConfig()
		c.QoSMS = 100
		c.QoSByFunctionMS = classMap
		if err := dev.SetConfig(c); err != nil {
			t.Fatal(err)
		}
		if validateClassConfig(c) == nil {
			t.Fatalf("accepted map %v", classMap)
		}
		h := mixedHandler(func(context.Context, string, string, []byte) ([]byte, int, error) {
			t.Fatal("bad map admitted")
			return nil, 0, nil
		}, func() string { return "test" })
		w := httptest.NewRecorder()
		h.ServeHTTP(w, httptest.NewRequest("POST", "/run", strings.NewReader(`{"kind":"cpu","bytes":65536,"iterations":100,"deadline_ms":100}`)))
		if w.Code != 503 || w.Header().Get("X-Tide-Function") != "cpu-qos100" || w.Header().Get("X-Tide-Effective-Deadline-Ms") == "" {
			t.Fatalf("%d %v", w.Code, w.Header())
		}
	}
}

func TestNativeFailureRetainsClassHeadersWithoutConfigMutation(t *testing.T) {
	configured(t)
	before := dev.GetToleranceTimeFor("cpu-qos100")
	h := mixedHandler(func(ctx context.Context, id, fn string, data []byte) ([]byte, int, error) {
		deadline, ok := ctx.Deadline()
		if !ok || time.Until(deadline) > 10*time.Second {
			t.Fatal("lost native timeout")
		}
		return nil, 504, errors.New("external native result wait expired")
	}, func() string { return "failure-id" })
	w := httptest.NewRecorder()
	h.ServeHTTP(w, httptest.NewRequest("POST", "/run", strings.NewReader(`{"kind":"cpu","bytes":65536,"iterations":100,"deadline_ms":500}`)))
	if w.Code != 504 || w.Header().Get("X-Tide-Function") != "cpu-qos500" || w.Header().Get("X-Tide-Effective-Deadline-Ms") != "500" || w.Header().Get("X-Tide-Request-ID") != "failure-id" {
		t.Fatalf("%d %v", w.Code, w.Header())
	}
	if dev.GetToleranceTimeFor("cpu-qos100") != before {
		t.Fatal("request mutated global config")
	}
	if w.Header().Get("Content-Type") == "" {
		t.Fatal("missing content type")
	}
}
