package main

import (
	"fmt"
	"math"
	"math/rand"
	"sort"
)

type Calibration struct {
	P95 float64 `json:"service_p95_ms"`
}
type Comparison struct {
	Instance       Instance   `json:"instance"`
	Weights        [3]float64 `json:"weights"`
	LatencyScaleMS float64    `json:"latency_scale_ms"`
	Heuristic      Solution   `json:"heuristic"`
	Exact          Solution   `json:"exact"`
	Gap            int        `json:"objective_gap"`
	Ratio          float64    `json:"heuristic_over_optimum"`
	HeuristicNS    int64      `json:"heuristic_compute_ns"`
	ExactNS        int64      `json:"exact_compute_ns"`
}
type Summary struct {
	Tasks          int     `json:"tasks"`
	Nodes          int     `json:"nodes"`
	Instances      int     `json:"instances"`
	MeanRatio      float64 `json:"mean_heuristic_over_optimum"`
	MinRatio       float64 `json:"min_heuristic_over_optimum"`
	MeanGap        float64 `json:"mean_objective_gap"`
	MaxGap         int     `json:"max_objective_gap"`
	OptimalMatches int     `json:"heuristic_matches_optimum"`
}

func Generate(c map[string]Calibration, seed int64, n, m int, pressure float64) Instance {
	rng := rand.New(rand.NewSource(seed))
	in := Instance{ID: fmt.Sprintf("n%d-m%d-p%.2f-seed%d", n, m, pressure, seed), Seed: seed, Pressure: pressure, TickMS: .1}
	in.Nodes = []Node{
		{Name: "node0", CPUQuota: 2, NetworkTicks: 20, FreeFraction: 1, DeviceWeight: .65, ServiceFactors: []float64{1.3, .9, 1.15}},
		{Name: "node1", CPUQuota: 2, NetworkTicks: 120, FreeFraction: 1, DeviceWeight: .8, ServiceFactors: []float64{.9, 1.2, .85}},
		{Name: "node2", CPUQuota: 2, NetworkTicks: 350, FreeFraction: 1, DeviceWeight: 1, ServiceFactors: []float64{.7, .85, 1.1}},
	}[:m]
	kinds := []string{"cpu", "memory", "io"}
	baselineSum := 0.0
	for i := 0; i < n; i++ {
		kind := rng.Intn(3)
		multiplier := float64(75+rng.Intn(51)) / 100
		task := Task{ID: i, Kind: kinds[kind], SizeMultiplier: multiplier, ServiceTicks: make([]int, m)}
		baseline := c[task.Kind].P95 * multiplier
		baselineSum += baseline
		for j, node := range in.Nodes {
			task.ServiceTicks[j] = int(math.Ceil(baseline * node.ServiceFactors[kind] / in.TickMS))
		}
		in.Tasks = append(in.Tasks, task)
	}
	// Load pressure changes only the common deadline, not selected outcomes.
	in.HorizonTicks = int(math.Ceil(pressure * baselineSum / float64(m) / in.TickMS))
	return in
}

func ValidateInstance(in Instance) error {
	if in.HorizonTicks <= 0 || in.TickMS <= 0 || math.IsNaN(in.TickMS) || math.IsInf(in.TickMS, 0) || len(in.Nodes) == 0 {
		return fmt.Errorf("invalid horizon, tick, or node count")
	}
	for _, node := range in.Nodes {
		if node.NetworkTicks < 0 || node.FreeFraction < 0 || node.FreeFraction > 1 || math.IsNaN(node.FreeFraction) || node.DeviceWeight < 0 || node.DeviceWeight > 1 || math.IsNaN(node.DeviceWeight) {
			return fmt.Errorf("invalid node parameters")
		}
	}
	for _, task := range in.Tasks {
		if len(task.ServiceTicks) != len(in.Nodes) {
			return fmt.Errorf("task service vector mismatch")
		}
		for _, v := range task.ServiceTicks {
			if v <= 0 {
				return fmt.Errorf("nonpositive service duration")
			}
		}
	}
	return nil
}

func Summarize(rows []Comparison) []Summary {
	groups := map[[2]int]*Summary{}
	for _, row := range rows {
		key := [2]int{len(row.Instance.Tasks), len(row.Instance.Nodes)}
		s := groups[key]
		if s == nil {
			s = &Summary{Tasks: key[0], Nodes: key[1], MinRatio: 1}
			groups[key] = s
		}
		ratio := 1.0
		if row.Exact.Accepted > 0 {
			ratio = float64(row.Heuristic.Accepted) / float64(row.Exact.Accepted)
		}
		gap := row.Exact.Accepted - row.Heuristic.Accepted
		s.Instances++
		s.MeanRatio += ratio
		s.MeanGap += float64(gap)
		s.MinRatio = math.Min(s.MinRatio, ratio)
		s.MaxGap = max(s.MaxGap, gap)
		if gap == 0 {
			s.OptimalMatches++
		}
	}
	out := make([]Summary, 0, len(groups))
	for _, s := range groups {
		s.MeanRatio /= float64(s.Instances)
		s.MeanGap /= float64(s.Instances)
		out = append(out, *s)
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Tasks == out[j].Tasks {
			return out[i].Nodes < out[j].Nodes
		}
		return out[i].Tasks < out[j].Tasks
	})
	return out
}
