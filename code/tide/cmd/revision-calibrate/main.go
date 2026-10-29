package main

import (
	"compress/gzip"
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

type Preference struct {
	Name   string
	Lambda [4]float64
}
type Selection struct {
	Preference
	WeightID                                                             int
	Weights                                                              [3]float64
	CalibrationReward, TestReward, DefaultTestReward, BestGridTestReward float64
}
type Inputs struct {
	TickMS      float64
	Nodes       map[string][]Node
	Traces      []Trace
	Grid        [][3]float64
	Preferences []Preference
}

func preferences() []Preference {
	return []Preference{
		{"equal", [4]float64{.25, .25, .25, .25}},
		{"prior_draft_ratio", [4]float64{1, 1, .5, 1}},
		{"throughput", [4]float64{.55, .15, .10, .20}},
		{"resilience", [4]float64{.15, .55, .10, .20}},
		{"utilization", [4]float64{.15, .15, .55, .15}},
		{"latency", [4]float64{.15, .15, .10, .60}},
	}
}
func writeJSON(path string, v any) error {
	b, e := json.MarshalIndent(v, "", "  ")
	if e != nil {
		return e
	}
	return os.WriteFile(path, append(b, '\n'), 0644)
}
func hashFile(p string) (string, error) {
	b, e := os.ReadFile(p)
	if e != nil {
		return "", e
	}
	h := sha256.Sum256(b)
	return hex.EncodeToString(h[:]), nil
}
func f(v float64) string { return strconv.FormatFloat(v, 'g', 17, 64) }
func meanReward(rows []Run, id int, split string, l [4]float64) float64 {
	s := 0.0
	n := 0
	for _, r := range rows {
		if r.WeightID == id && r.Split == split {
			s += Reward(r.Metrics, l)
			n++
		}
	}
	if n == 0 {
		panic("empty selection split")
	}
	return s / float64(n)
}

func execute(out string) error {
	entries, e := os.ReadDir(out)
	if e == nil && len(entries) > 0 {
		return fmt.Errorf("output directory must be empty: %s", out)
	}
	if e != nil && !os.IsNotExist(e) {
		return e
	}
	if e = os.MkdirAll(out, 0755); e != nil {
		return e
	}
	started := time.Now().UTC()
	in := Inputs{TickMS: TickMS, Nodes: map[string][]Node{"low": ModelNodes(false), "high": ModelNodes(true)}, Grid: WeightGrid(), Preferences: preferences()}
	for si, split := range []string{"calibration", "test"} {
		for pi, pattern := range []string{"steady", "burst"} {
			for _, rate := range []int{10, 30, 60} {
				for rep := 0; rep < 3; rep++ {
					seed := int64(202610050000 + si*100000 + pi*10000 + rate*100 + rep)
					tr := GenerateTrace(seed, float64(rate), pattern, split, in.Nodes["low"])
					if e = Validate(tr, in.Nodes["low"]); e != nil {
						return e
					}
					in.Traces = append(in.Traces, tr)
				}
			}
		}
	}
	if e = writeJSON(filepath.Join(out, "inputs.json"), in); e != nil {
		return e
	}
	raw, e := os.Create(filepath.Join(out, "decisions.csv.gz"))
	if e != nil {
		return e
	}
	defer raw.Close()
	gz := gzip.NewWriter(raw)
	writer := csv.NewWriter(gz)
	if e = writer.Write([]string{"run_id", "request_id", "node", "predicted_response_ticks", "start_ticks", "finish_ticks", "response_ticks", "score", "status"}); e != nil {
		return e
	}
	summary, e := os.Create(filepath.Join(out, "summary.jsonl"))
	if e != nil {
		return e
	}
	defer summary.Close()
	enc := json.NewEncoder(summary)
	rows := []Run{}
	decisions := 0
	for _, tr := range in.Traces {
		for _, net := range []string{"low", "high"} {
			for wi, w := range in.Grid {
				d, metrics := Simulate(tr, in.Nodes[net], w)
				r := Run{ID: fmt.Sprintf("%s-%s-w%02d", tr.ID, net, wi), TraceID: tr.ID, Split: tr.Split, Network: net, WeightID: wi, Metrics: metrics}
				if e = enc.Encode(r); e != nil {
					return e
				}
				rows = append(rows, r)
				for _, x := range d {
					if e = writer.Write([]string{r.ID, strconv.Itoa(x.RequestID), strconv.Itoa(x.Node), strconv.FormatInt(x.PredResponse, 10), strconv.FormatInt(x.Start, 10), strconv.FormatInt(x.Finish, 10), strconv.FormatInt(x.Response, 10), f(x.Score), x.Status}); e != nil {
						return e
					}
				}
				decisions += len(d)
			}
		}
	}
	writer.Flush()
	if e = writer.Error(); e != nil {
		return e
	}
	if e = gz.Close(); e != nil {
		return e
	}
	if e = raw.Close(); e != nil {
		return e
	}
	if e = summary.Close(); e != nil {
		return e
	}
	selections := []Selection{}
	for _, p := range in.Preferences {
		id := Select(rows, p.Lambda, in.Grid)
		best := -1e100
		for j := range in.Grid {
			best = max(best, meanReward(rows, j, "test", p.Lambda))
		}
		selections = append(selections, Selection{p, id, in.Grid[id], meanReward(rows, id, "calibration", p.Lambda), meanReward(rows, id, "test", p.Lambda), meanReward(rows, 66, "test", p.Lambda), best})
	}
	if e = writeJSON(filepath.Join(out, "selection.json"), selections); e != nil {
		return e
	}
	// Perturb preferences, not metric outcomes. Each new choice still uses
	// calibration rows only, even though held-out metrics are available.
	type Perturbation struct {
		Base       string
		Coordinate int
		Factor     float64
		Selection  Selection
	}
	pert := []Perturbation{}
	for _, p := range in.Preferences {
		for k := 0; k < 4; k++ {
			for _, factor := range []float64{.7, 1.3} {
				l := p.Lambda
				l[k] *= factor
				id := Select(rows, l, in.Grid)
				best := -1e100
				for j := range in.Grid {
					best = max(best, meanReward(rows, j, "test", l))
				}
				pert = append(pert, Perturbation{p.Name, k, factor, Selection{Preference{p.Name, l}, id, in.Grid[id], meanReward(rows, id, "calibration", l), meanReward(rows, id, "test", l), meanReward(rows, 66, "test", l), best}})
			}
		}
	}
	if e = writeJSON(filepath.Join(out, "lambda_sensitivity.json"), pert); e != nil {
		return e
	}
	hashes := map[string]string{}
	for _, name := range []string{"inputs.json", "decisions.csv.gz", "summary.jsonl", "selection.json", "lambda_sensitivity.json"} {
		h, err := hashFile(filepath.Join(out, name))
		if err != nil {
			return err
		}
		hashes[name] = h
	}
	sources := map[string]string{}
	for _, name := range []string{"cmd/revision-calibrate/model.go", "cmd/revision-calibrate/main.go", "cmd/revision-calibrate/model_test.go", "pkg/schedpolicy/score.go", "results-revision-20260928/weights-02/calibration.json"} {
		h, err := hashFile(name)
		if err != nil {
			return err
		}
		sources[name] = h
	}
	m := map[string]any{"scope": "restricted simulation; fixed preferences choose online score weights offline; not native Tide execution", "started_utc": started, "finished_utc": time.Now().UTC(), "command": os.Args, "go": runtime.Version(), "os": runtime.GOOS, "arch": runtime.GOARCH, "run_count": len(rows), "decision_count": decisions, "trace_count": len(in.Traces), "source_sha256": sources, "artifact_sha256": hashes, "selection_rule": "calibration-only mean reward; tie within 1e-12 prefers closest to equal online weights, then grid order", "metric_scope": "U is simulated execution occupancy of CPU and memory tokens; not measured CPU/RSS or fixed stub reservation. R is outage-arrival deadline yield, distinct from structural R_cap.", "notes": "Latency is conditional on returned responses, capped mean deadline ratio for selection (1 if none); counts and uncapped latency are retained. No training/test seed overlap. Workload scale is historical measured calibration; factors, capacities, network budgets, memory tokens and actual-time error are stipulated simulation inputs."}
	if e = writeJSON(filepath.Join(out, "manifest.json"), m); e != nil {
		return e
	}
	fmt.Printf("completed %d runs, %d decisions, %d saved traces\n", len(rows), decisions, len(in.Traces))
	for _, s := range selections {
		fmt.Printf("%s lambda=%v online=%v train=%.6f test=%.6f default_test=%.6f\n", s.Name, s.Lambda, s.Weights, s.CalibrationReward, s.TestReward, s.DefaultTestReward)
	}
	return nil
}

func main() {
	out := flag.String("out", "results-objective-20261005", "new/empty result directory")
	flag.Parse()
	if e := execute(*out); e != nil {
		fmt.Fprintln(os.Stderr, e)
		os.Exit(1)
	}
}
