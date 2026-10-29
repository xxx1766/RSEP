package main

import (
	"compress/gzip"
	"crypto/sha256"
	"encoding/csv"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"time"
)

type Config struct {
	Output, Calibration, SourceRoot, Traces string
	Repetitions                             int
	DurationSeconds                         float64
}
type RunSummary struct {
	RunID           string     `json:"run_id"`
	TraceID         string     `json:"trace_id"`
	TraceSHA256     string     `json:"trace_sha256"`
	Seed            int64      `json:"seed"`
	OfferedRateRPS  float64    `json:"offered_rate_rps"`
	Network         string     `json:"network_profile"`
	Weights         [3]float64 `json:"weights_resource_latency_device"`
	DurationSeconds float64    `json:"duration_seconds"`
	Metrics         Summary    `json:"metrics"`
}
type RunConfig struct {
	RunSummary
	Nodes          []Node  `json:"nodes"`
	LatencyScaleMS float64 `json:"latency_scale_ms"`
}

func writeJSON(path string, value any) error {
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, append(data, '\n'), 0644)
}
func hashBytes(data []byte) string { sum := sha256.Sum256(data); return hex.EncodeToString(sum[:]) }
func formatFloat(v float64) string { return strconv.FormatFloat(v, 'g', 17, 64) }
func integer(v int64) string       { return strconv.FormatInt(v, 10) }
func boolean(v bool) string        { return strconv.FormatBool(v) }

func Run(cfg Config) error {
	if cfg.Repetitions < 1 || cfg.DurationSeconds <= 0 || math.IsNaN(cfg.DurationSeconds) || math.IsInf(cfg.DurationSeconds, 0) {
		return fmt.Errorf("invalid repetitions or duration")
	}
	data, err := os.ReadFile(cfg.Calibration)
	if err != nil {
		return err
	}
	var calibration map[string]struct {
		P95 float64 `json:"service_p95_ms"`
	}
	if err = json.Unmarshal(data, &calibration); err != nil {
		return err
	}
	baselines := map[string]float64{}
	for _, kind := range []string{"cpu", "memory", "io"} {
		value := calibration[kind].P95
		if value <= 0 || math.IsNaN(value) || math.IsInf(value, 0) {
			return fmt.Errorf("invalid calibration %s", kind)
		}
		baselines[kind] = value
	}
	calHash := hashBytes(data)
	traces := []Trace{}
	if cfg.Traces != "" {
		data, err = os.ReadFile(cfg.Traces)
		if err != nil {
			return err
		}
		if err = json.Unmarshal(data, &traces); err != nil {
			return err
		}
	} else {
		for _, rate := range []float64{10, 20, 40} {
			for repetition := 0; repetition < cfg.Repetitions; repetition++ {
				seed := int64(202609280000) + int64(rate)*100 + int64(repetition)
				traces = append(traces, GenerateTrace(baselines, seed, rate, cfg.DurationSeconds))
			}
		}
	}
	if len(traces) == 0 {
		return fmt.Errorf("no traces")
	}
	traceHashes := map[string]string{}
	for _, trace := range traces {
		if err = ValidateTrace(trace, 3); err != nil {
			return fmt.Errorf("trace %s: %w", trace.ID, err)
		}
		if trace.TickMS != .1 {
			return fmt.Errorf("trace %s: model network budgets require 0.1 ms ticks", trace.ID)
		}
		if _, exists := traceHashes[trace.ID]; exists {
			return fmt.Errorf("duplicate trace ID %s", trace.ID)
		}
		encoded, e := json.Marshal(trace)
		if e != nil {
			return e
		}
		traceHashes[trace.ID] = hashBytes(encoded)
	}
	sourceHashes := map[string]string{}
	paths, e := filepath.Glob(filepath.Join(cfg.SourceRoot, "cmd/revision-sensitivity/*.go"))
	if e != nil {
		return e
	}
	paths = append(paths, filepath.Join(cfg.SourceRoot, "pkg/schedpolicy/score.go"))
	for _, path := range paths {
		data, e := os.ReadFile(path)
		if e != nil {
			return e
		}
		name, e := filepath.Rel(cfg.SourceRoot, path)
		if e != nil {
			return e
		}
		sourceHashes[name] = hashBytes(data)
	}
	if entries, e := os.ReadDir(cfg.Output); e == nil && len(entries) > 0 {
		return fmt.Errorf("output directory is not empty: %s", cfg.Output)
	} else if e != nil && !os.IsNotExist(e) {
		return e
	}
	if err = os.MkdirAll(cfg.Output, 0755); err != nil {
		return err
	}
	if err = writeJSON(filepath.Join(cfg.Output, "traces.json"), traces); err != nil {
		return err
	}
	if err = writeJSON(filepath.Join(cfg.Output, "calibration.json"), calibration); err != nil {
		return err
	}
	rawFile, err := os.Create(filepath.Join(cfg.Output, "requests.csv.gz"))
	if err != nil {
		return err
	}
	defer rawFile.Close()
	compressed := gzip.NewWriter(rawFile)
	defer compressed.Close()
	raw := csv.NewWriter(compressed)
	if err = raw.Write([]string{"run_id", "trace_id", "request_id", "kind", "arrival_ticks", "decision_ticks", "deadline_ticks", "node_index", "service_ticks", "executor_start_ticks", "executor_finish_ticks", "response_ticks", "latency_ms", "accepted", "completed_within_window", "score", "status"}); err != nil {
		return err
	}
	configs, err := os.Create(filepath.Join(cfg.Output, "runs.jsonl"))
	if err != nil {
		return err
	}
	defer configs.Close()
	configEncoder := json.NewEncoder(configs)
	summaries := []RunSummary{}
	totalDecisions := 0
	for _, trace := range traces {
		for _, highNetwork := range []bool{false, true} {
			network := "low"
			if highNetwork {
				network = "high"
			}
			nodes := ModelNodes(highNetwork)
			for _, weights := range WeightGrid() {
				runID := fmt.Sprintf("%s-net%s-w%d_%d_%d", trace.ID, network, int(math.Round(weights[0]*10)), int(math.Round(weights[1]*10)), int(math.Round(weights[2]*10)))
				decisions, metrics := Simulate(trace, nodes, weights, 100)
				summary := RunSummary{RunID: runID, TraceID: trace.ID, TraceSHA256: traceHashes[trace.ID], Seed: trace.Seed, OfferedRateRPS: trace.OfferedRateRPS, Network: network, Weights: weights, DurationSeconds: float64(trace.DurationTicks) * trace.TickMS / 1000, Metrics: metrics}
				summaries = append(summaries, summary)
				if err = configEncoder.Encode(RunConfig{RunSummary: summary, Nodes: nodes, LatencyScaleMS: 100}); err != nil {
					return err
				}
				for i, decision := range decisions {
					request := trace.Requests[i]
					service, latency := "", ""
					if decision.Accepted {
						service = integer(request.ServiceTicks[decision.Node])
						latency = formatFloat(float64(decision.ResponseTicks-request.ArrivalTicks) * trace.TickMS)
					}
					record := []string{runID, trace.ID, strconv.Itoa(request.ID), request.Kind, integer(request.ArrivalTicks), integer(request.DecisionTicks), integer(request.DeadlineTicks), strconv.Itoa(decision.Node), service, integer(decision.StartTicks), integer(decision.FinishTicks), integer(decision.ResponseTicks), latency, boolean(decision.Accepted), boolean(decision.WithinWindow), formatFloat(decision.Score), decision.Status}
					if err = raw.Write(record); err != nil {
						return err
					}
					totalDecisions++
				}
			}
		}
	}
	raw.Flush()
	if err = raw.Error(); err != nil {
		return err
	}
	if err = compressed.Close(); err != nil {
		return err
	}
	if err = rawFile.Close(); err != nil {
		return err
	}
	if err = configs.Close(); err != nil {
		return err
	}
	if err = writeJSON(filepath.Join(cfg.Output, "summary.json"), summaries); err != nil {
		return err
	}
	summaryFile, err := os.Create(filepath.Join(cfg.Output, "summary.csv"))
	if err != nil {
		return err
	}
	defer summaryFile.Close()
	table := csv.NewWriter(summaryFile)
	if err = table.Write([]string{"run_id", "trace_id", "trace_sha256", "seed", "offered_rate_rps", "network_profile", "weight_resource", "weight_latency", "weight_device", "duration_seconds", "arrivals", "accepted", "rejected", "completed_within_window", "drained_after_window", "cohort_yield", "window_goodput_rps", "actual_arrival_rps", "mean_accepted_latency_ms", "p95_accepted_latency_ms"}); err != nil {
		return err
	}
	for _, s := range summaries {
		m := s.Metrics
		if err = table.Write([]string{s.RunID, s.TraceID, s.TraceSHA256, integer(s.Seed), formatFloat(s.OfferedRateRPS), s.Network, formatFloat(s.Weights[0]), formatFloat(s.Weights[1]), formatFloat(s.Weights[2]), formatFloat(s.DurationSeconds), strconv.Itoa(m.Arrivals), strconv.Itoa(m.Accepted), strconv.Itoa(m.Rejected), strconv.Itoa(m.CompletedWithinWindow), strconv.Itoa(m.DrainedAfterWindow), formatFloat(m.CohortYield), formatFloat(m.WindowGoodputRPS), formatFloat(m.ActualArrivalRPS), formatFloat(m.MeanAcceptedLatencyMS), formatFloat(m.P95AcceptedLatencyMS)}); err != nil {
			return err
		}
	}
	table.Flush()
	if err = table.Error(); err != nil {
		return err
	}
	if err = summaryFile.Close(); err != nil {
		return err
	}
	artifactHashes := map[string]string{}
	for _, name := range []string{"traces.json", "calibration.json", "requests.csv.gz", "runs.jsonl", "summary.json", "summary.csv"} {
		data, e := os.ReadFile(filepath.Join(cfg.Output, name))
		if e != nil {
			return e
		}
		artifactHashes[name] = hashBytes(data)
	}
	manifest := map[string]any{
		"evidence_type": "restricted online simulation; workload service scales from measured warm p95; no runtime task execution",
		"created_utc":   time.Now().UTC().Format(time.RFC3339), "argv": os.Args, "go_version": runtime.Version(), "os": runtime.GOOS, "arch": runtime.GOARCH,
		"source_sha256": sourceHashes, "artifact_sha256": artifactHashes, "calibration_path": cfg.Calibration, "calibration_sha256": calHash,
		"trace_count": len(traces), "run_count": len(summaries), "request_decision_count": totalDecisions, "weight_grid": WeightGrid(), "offered_rates_rps": []int{10, 20, 40},
		"seed_rule":                      "202609280000 + offered_rate_rps*100 + repetition; repetitions start at zero",
		"requested_repetitions_per_rate": cfg.Repetitions, "requested_duration_seconds": cfg.DurationSeconds, "replayed_traces_path": cfg.Traces,
		"latency_scale_ms": 100, "qos_ms": 200, "tick_ms": .1, "network_low_ms": []int{2, 12, 35}, "network_high_ms": []int{10, 40, 100},
		"origin":      "entry-only origin; every node is a scored remote candidate; existing stubs each have one serial executor with fixed 2 CPU quota",
		"feasibility": "max(decision time, node FIFO service finish) + own service + node network response budget <= absolute request deadline",
		"metrics":     "window goodput = responses completed in [0,T) / T; cohort yield = accepted / arrivals in [0,T); accepted includes separately reported drain at or after T; rejected requests remain in denominator",
		"estimation":  "ideal known deterministic service equals predicted service; network budgets are stipulated; no simulated failures after admission",
		"limitations": "selection sensitivity only, not full runtime or learning; fixed free CPU features and quotas, synthetic node factors/size variation/network, no contention dynamics, no TTL, no capacity optimization, no online parameter learning",
	}
	return writeJSON(filepath.Join(cfg.Output, "manifest.json"), manifest)
}

func main() {
	cfg := Config{}
	flag.StringVar(&cfg.Output, "out", "results-revision-20260928/weights-02", "new output directory")
	flag.StringVar(&cfg.Calibration, "calibration", "results-revision-20260928/calibration-01/summary.json", "measured warm workload calibration")
	flag.StringVar(&cfg.SourceRoot, "source-root", ".", "Tide source root for provenance")
	flag.StringVar(&cfg.Traces, "traces", "", "replay saved traces.json")
	flag.IntVar(&cfg.Repetitions, "repetitions", 5, "input seeds per offered load")
	flag.Float64Var(&cfg.DurationSeconds, "duration-seconds", 20, "Poisson arrival window duration")
	flag.Parse()
	if err := Run(cfg); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	fmt.Println("Saved restricted online weight sensitivity to", cfg.Output)
}
