package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"time"

	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/revisionworkload"
)

type mixedRequest struct {
	revisionworkload.Config
	DeadlineMS int `json:"deadline_ms"`
}

func parseMixedRequest(data []byte) (mixedRequest, error) {
	var work mixedRequest
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&work); err != nil {
		return work, err
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return work, fmt.Errorf("exactly one workload object required")
	}
	if work.Kind != "cpu" || work.Bytes != 65536 || work.Iterations != 100 || (work.DeadlineMS != 100 && work.DeadlineMS != 500) {
		return work, fmt.Errorf("mixed experiment requires cpu/65536/100 with deadline_ms 100 or 500")
	}
	return work, nil
}

func validateClassConfig(c dev.Config) error {
	for _, deadline := range []int{100, 500} {
		name := fmt.Sprintf("cpu-qos%d", deadline)
		if value, ok := c.QoSByFunctionMS[name]; !ok || value != deadline {
			return fmt.Errorf("qos_by_function_ms must explicitly configure %s=%d", name, deadline)
		}
	}
	return nil
}

type nativeSubmit func(context.Context, string, string, []byte) ([]byte, int, error)

func mixedHandler(submit nativeSubmit, nextID func() string) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" {
			http.Error(w, "POST required", 405)
			return
		}
		data, err := io.ReadAll(io.LimitReader(r.Body, 8193))
		if err != nil || len(data) > 8192 {
			http.Error(w, "invalid payload", 400)
			return
		}
		work, err := parseMixedRequest(data)
		if err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		id := nextID()
		function := fmt.Sprintf("cpu-qos%d", work.DeadlineMS)
		effective := dev.GetToleranceTimeFor(function)
		w.Header().Set("X-Tide-Request-ID", id)
		w.Header().Set("X-Tide-Function", function)
		w.Header().Set("X-Tide-Deadline-Ms", strconv.Itoa(work.DeadlineMS))
		w.Header().Set("X-Tide-Effective-Deadline-Ms", strconv.FormatFloat(float64(effective)/float64(time.Millisecond), 'f', -1, 64))
		if err := validateClassConfig(dev.CurrentConfig()); err != nil {
			http.Error(w, err.Error(), 503)
			return
		}
		if effective != int(time.Duration(work.DeadlineMS)*time.Millisecond) {
			http.Error(w, "effective native deadline differs from declared class", 503)
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
		defer cancel()
		result, status, err := submit(ctx, id, function, data)
		w.Header().Set("Content-Type", "application/json")
		if err != nil {
			w.WriteHeader(status)
			_ = json.NewEncoder(w).Encode(map[string]any{"error": err.Error(), "request_id": id, "function": function, "deadline_ms": work.DeadlineMS, "effective_deadline_ms": float64(effective) / float64(time.Millisecond)})
			return
		}
		w.WriteHeader(status)
		_, _ = w.Write(result)
	})
}
