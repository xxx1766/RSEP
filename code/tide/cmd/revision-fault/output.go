package main

import (
	"crypto/sha256"
	"encoding/csv"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"time"
)

type PhaseSummary struct {
	Phase                   string  `json:"phase"`
	StartNS                 int64   `json:"start_ns"`
	EndNS                   int64   `json:"end_ns"`
	Arrivals                int     `json:"arrivals"`
	LocalRoutes             int     `json:"local_routes"`
	RemoteRoutes            int     `json:"remote_routes"`
	AdmissionRejected       int     `json:"admission_rejected"`
	Successes               int     `json:"cohort_successes"`
	OnTime                  int     `json:"cohort_ontime"`
	Failures                int     `json:"cohort_failures"`
	WindowOnTimeCompletions int     `json:"window_ontime_completions"`
	DrainedAfterWindow      int     `json:"cohort_successes_drained_after_full_window"`
	CohortYield             float64 `json:"cohort_ontime_yield"`
	WindowGoodputRPS        float64 `json:"window_ontime_goodput_rps"`
}

func Summarize(rows []Record, events []FaultEvent, end int64) []PhaseSummary {
	summaries := make([]PhaseSummary, len(events))
	indexes := map[string]int{}
	for i, event := range events {
		last := end
		if i+1 < len(events) {
			last = events[i+1].AtNS
		}
		summaries[i] = PhaseSummary{Phase: event.Phase, StartNS: event.AtNS, EndNS: last}
		indexes[event.Phase] = i
	}
	for _, record := range rows {
		phase := &summaries[indexes[phaseAt(record.SubmitNS, events)]]
		phase.Arrivals++
		if record.Node == 0 {
			phase.LocalRoutes++
		} else if record.Node == 1 {
			phase.RemoteRoutes++
		} else {
			phase.AdmissionRejected++
		}
		if record.Status == 200 {
			phase.Successes++
			if record.ReplyNS >= end {
				phase.DrainedAfterWindow++
			}
		} else {
			phase.Failures++
		}
		if record.OnTime {
			phase.OnTime++
			if record.ReplyNS < end {
				summaries[indexes[phaseAt(record.ReplyNS, events)]].WindowOnTimeCompletions++
			}
		}
	}
	for i := range summaries {
		s := &summaries[i]
		if s.Arrivals > 0 {
			s.CohortYield = float64(s.OnTime) / float64(s.Arrivals)
		}
		s.WindowGoodputRPS = float64(s.WindowOnTimeCompletions) / (float64(s.EndNS-s.StartNS) / 1e9)
	}
	return summaries
}
func writeJSON(path string, value any) error {
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, append(data, '\n'), 0644)
}
func fileHash(path string) (string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:]), nil
}
func WriteResults(out string, cfg RunConfig, rows []Record, events []FaultEvent, start time.Time) error {
	for _, item := range []struct {
		name  string
		value any
	}{{"trace.json", cfg.Trace}, {"events.json", events}, {"config.json", cfg}, {"summary.json", Summarize(rows, events, int64(3*cfg.Phase))}} {
		if err := writeJSON(filepath.Join(out, item.name), item.value); err != nil {
			return err
		}
	}
	file, err := os.Create(filepath.Join(out, "raw.csv"))
	if err != nil {
		return err
	}
	defer file.Close()
	w := csv.NewWriter(file)
	if err = w.Write([]string{"req_id", "request_id", "planned_submit_ns", "t_submit_ns", "t_dispatch_ns", "t_reply_ns", "route", "status", "worker_service_ns", "worker_start_unix_ns", "worker_finish_unix_ns", "phase_submit", "phase_reply", "on_time", "success_within_full_window", "latency_ms", "fault_cancel_target", "error"}); err != nil {
		return err
	}
	affected := map[string]bool{}
	for _, event := range events {
		for _, id := range event.InflightIDs {
			affected[id] = true
		}
	}
	i64 := func(x int64) string { return strconv.FormatInt(x, 10) }
	for _, r := range rows {
		route := "rejected"
		if r.Node == 0 {
			route = "local"
		} else if r.Node == 1 {
			route = "remote"
		}
		if err = w.Write([]string{strconv.Itoa(r.ID), r.RequestID, i64(r.PlannedNS), i64(r.SubmitNS), i64(r.DispatchNS), i64(r.ReplyNS), route, strconv.Itoa(r.Status), i64(r.ServiceNS), i64(r.WorkerStartUnixNS), i64(r.WorkerFinishUnixNS), r.SubmitPhase, r.ReplyPhase, strconv.FormatBool(r.OnTime), strconv.FormatBool(r.WithinWindow), strconv.FormatFloat(float64(r.ReplyNS-r.SubmitNS)/1e6, 'g', 17, 64), strconv.FormatBool(affected[r.RequestID]), r.Error}); err != nil {
			return err
		}
	}
	w.Flush()
	if err = w.Error(); err != nil {
		return err
	}
	if err = file.Close(); err != nil {
		return err
	}
	binary, err := os.Executable()
	if err != nil {
		return err
	}
	binaryHash, err := fileHash(binary)
	if err != nil {
		return err
	}
	meta := map[string]any{"status": "completed", "evidence_type": "measured real-kernel executable HTTP scheduling/fault adapter; NOT native Tide distributed recovery", "measurement_start_unix_ns": start.UnixNano(), "measurement_end_ns": int64(3 * cfg.Phase), "binary_sha256": binaryHash, "argv": os.Args, "go_version": runtime.Version(), "os": runtime.GOOS, "arch": runtime.GOARCH, "pid": os.Getpid(), "request_count": len(rows), "local_eligible": cfg.LocalEligible, "worker_concurrency_per_node": 1, "worker_cpu_budget": "external launcher records and verifies worker affinity; controller does not impose CPU quota", "fault_model": "only run-owned remote HTTP gate: cancel active upstream response waits and reject new remote HTTP calls with503; no host networking changes", "retry_policy": "none: remote kernel may finish after response cancellation, recorded separately by worker; server retains serial slot until actual completion", "phase_semantics": "actual gate transition timestamps; phase cohort based on actual submission; completion goodput based on reply timestamps and actual phase duration", "local_eligibility_ablation": "enabled: feasible local-first admission; disabled: local node ineligible, an entry-only origin; localworker remains allocated but unused", "scope_limits": "two processes on one host, no physical partition, no native gRPC/event return or distributed retry/exactly-once recovery, no universal C4 completion guarantee", "prediction": "pending count times fixed measured-calibration estimate; zero local and5ms remote network prediction; actual HTTP/service latency measured"}
	return writeJSON(filepath.Join(out, "run.json"), meta)
}
