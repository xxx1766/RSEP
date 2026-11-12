package main

import (
	"crypto/sha256"
	"encoding/csv"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"time"
)

type Config struct {
	Output, Calibration, SourceRoot, Instances string
	Repetitions                                int
}

func writeJSON(path string, value any) error {
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, append(data, '\n'), 0644)
}
func Run(cfg Config) error {
	if cfg.Repetitions < 1 {
		return fmt.Errorf("repetitions must be positive")
	}
	calibrationBytes, err := os.ReadFile(cfg.Calibration)
	if err != nil {
		return err
	}
	var calibration map[string]Calibration
	if err = json.Unmarshal(calibrationBytes, &calibration); err != nil {
		return err
	}
	for _, kind := range []string{"cpu", "memory", "io"} {
		if !(calibration[kind].P95 > 0) {
			return fmt.Errorf("missing positive calibration for %s", kind)
		}
	}
	instances := []Instance{}
	if cfg.Instances != "" {
		data, e := os.ReadFile(cfg.Instances)
		if e != nil {
			return e
		}
		if e = json.Unmarshal(data, &instances); e != nil {
			return e
		}
	} else {
		for _, n := range []int{6, 8, 10, 12} {
			for _, m := range []int{2, 3} {
				for p, pressure := range []float64{.55, .75, .95} {
					for rep := 0; rep < cfg.Repetitions; rep++ {
						seed := int64(202609280000 + n*10000 + m*1000 + p*100 + rep)
						instances = append(instances, Generate(calibration, seed, n, m, pressure))
					}
				}
			}
		}
	}
	weights := [3]float64{.3, .5, .2}
	const scale = 100.0
	rows := make([]Comparison, 0, len(instances))
	for _, in := range instances {
		if err = ValidateInstance(in); err != nil {
			return fmt.Errorf("%s: %w", in.ID, err)
		}
		start := time.Now()
		h := Heuristic(in, weights, scale)
		hn := time.Since(start).Nanoseconds()
		start = time.Now()
		o := Exact(in)
		on := time.Since(start).Nanoseconds()
		if err = ValidateAssignment(in, h); err != nil {
			return fmt.Errorf("heuristic %s: %w", in.ID, err)
		}
		if err = ValidateAssignment(in, o); err != nil {
			return fmt.Errorf("exact %s: %w", in.ID, err)
		}
		if !o.Optimal || h.Accepted > o.Accepted {
			return fmt.Errorf("uncertified optimum for %s", in.ID)
		}
		ratio := 1.0
		if o.Accepted > 0 {
			ratio = float64(h.Accepted) / float64(o.Accepted)
		}
		rows = append(rows, Comparison{Instance: in, Weights: weights, LatencyScaleMS: scale, Heuristic: h, Exact: o, Gap: o.Accepted - h.Accepted, Ratio: ratio, HeuristicNS: hn, ExactNS: on})
	}
	hashes := map[string]string{}
	sourceFiles, e := filepath.Glob(filepath.Join(cfg.SourceRoot, "cmd/revision-model/*.go"))
	if e != nil {
		return e
	}
	sourceFiles = append(sourceFiles, filepath.Join(cfg.SourceRoot, "pkg/schedpolicy/score.go"))
	for _, path := range sourceFiles {
		data, e := os.ReadFile(path)
		if e != nil {
			return e
		}
		sum := sha256.Sum256(data)
		name, e := filepath.Rel(cfg.SourceRoot, path)
		if e != nil {
			return e
		}
		hashes[name] = hex.EncodeToString(sum[:])
	}
	calHash := sha256.Sum256(calibrationBytes)
	meta := map[string]any{
		"evidence_type": "simulated restricted offline instances; service scales calibrated from measured warm workload p95",
		"created_utc":   time.Now().UTC().Format(time.RFC3339), "go_version": runtime.Version(), "os": runtime.GOOS, "arch": runtime.GOARCH,
		"argv": os.Args, "source_sha256": hashes, "calibration_path": cfg.Calibration, "calibration_sha256": hex.EncodeToString(calHash[:]),
		"weights": weights, "latency_scale_ms": scale, "instance_count": len(instances), "repetitions_per_n_m_pressure": cfg.Repetitions,
		"seed_rule":   "202609280000 + tasks*10000 + nodes*1000 + pressure_index*100 + repetition (zero based)",
		"task_counts": []int{6, 8, 10, 12}, "node_counts": []int{2, 3}, "horizon_multipliers": []float64{.55, .75, .95},
		"objective":   "maximize count accepted; every task has unit importance; rejected tasks contribute zero",
		"constraints": "all arrivals zero; common deadline; fixed 2 CPU quota per serial executor; one node per accepted task; sum service ticks on node + fixed network ticks <= deadline",
		"score_scope": "shared pkg/schedpolicy.Score; free_fraction is fixed at 1 (CPU quota not optimized); ties choose lowest node index; input order preserved",
		"solver":      "complete assignment/rejection branch and bound; pooled residual capacity admissible upper bound; no timeout or target gap",
		"certificate": "node assignment per task; service sum plus network checked for every used node; proven_optimal means exhaustive search with sound pruning completed",
		"limitations": "no general Problem 1 optimum, approximation bound, runtime outcome, online arrival, dynamic quota, per-task deadline, or network/contention measurement; node service factors and task sizes are stipulated synthetic parameters",
	}
	if entries, e := os.ReadDir(cfg.Output); e == nil && len(entries) > 0 {
		return fmt.Errorf("output directory already contains files: %s", cfg.Output)
	} else if e != nil && !os.IsNotExist(e) {
		return e
	}
	if err = os.MkdirAll(cfg.Output, 0755); err != nil {
		return err
	}
	for _, item := range []struct {
		name  string
		value any
	}{{"instances.json", instances}, {"results.json", rows}, {"summary.json", Summarize(rows)}, {"manifest.json", meta}, {"calibration.json", calibration}} {
		if err = writeJSON(filepath.Join(cfg.Output, item.name), item.value); err != nil {
			return err
		}
	}
	file, err := os.Create(filepath.Join(cfg.Output, "results.csv"))
	if err != nil {
		return err
	}
	writer := csv.NewWriter(file)
	if err = writer.Write([]string{"instance", "seed", "tasks", "nodes", "horizon_multiplier", "deadline_ms", "heuristic_accepted", "optimum_accepted", "objective_gap", "heuristic_over_optimum", "proven_optimal", "exact_states", "exact_pruned", "heuristic_compute_ns", "exact_compute_ns"}); err != nil {
		file.Close()
		return err
	}
	for _, r := range rows {
		fields := []string{r.Instance.ID, strconv.FormatInt(r.Instance.Seed, 10), strconv.Itoa(len(r.Instance.Tasks)), strconv.Itoa(len(r.Instance.Nodes)), fmt.Sprint(r.Instance.Pressure), fmt.Sprint(float64(r.Instance.HorizonTicks) * r.Instance.TickMS), strconv.Itoa(r.Heuristic.Accepted), strconv.Itoa(r.Exact.Accepted), strconv.Itoa(r.Gap), strconv.FormatFloat(r.Ratio, 'g', 17, 64), strconv.FormatBool(r.Exact.Optimal), strconv.FormatUint(r.Exact.Stats.Visited, 10), strconv.FormatUint(r.Exact.Stats.BoundPruned, 10), strconv.FormatInt(r.HeuristicNS, 10), strconv.FormatInt(r.ExactNS, 10)}
		if err = writer.Write(fields); err != nil {
			file.Close()
			return err
		}
	}
	writer.Flush()
	if err = writer.Error(); err != nil {
		file.Close()
		return err
	}
	return file.Close()
}
func main() {
	cfg := Config{}
	flag.StringVar(&cfg.Output, "out", "results-revision-20260928/model-01", "new output directory")
	flag.StringVar(&cfg.Calibration, "calibration", "results-revision-20260928/calibration-01/summary.json", "measured warm service p95 input")
	flag.StringVar(&cfg.SourceRoot, "source-root", ".", "Tide module source root for provenance")
	flag.StringVar(&cfg.Instances, "instances", "", "replay saved instances.json instead of generating inputs")
	flag.IntVar(&cfg.Repetitions, "repetitions", 5, "fixed-seed repetitions per task count, node count and horizon multiplier")
	flag.Parse()
	if err := Run(cfg); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	fmt.Println("Saved restricted-model evidence to", cfg.Output)
}
