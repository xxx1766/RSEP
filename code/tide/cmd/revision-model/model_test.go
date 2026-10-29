package main

import (
	"encoding/json"
	"math/rand"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

// Enumerate all (nodes+1)^tasks assignments, without solver pruning or memoization.
func bruteForce(in Instance) int {
	count := 1
	for range in.Tasks {
		count *= len(in.Nodes) + 1
	}
	best := 0
	for encoding := 0; encoding < count; encoding++ {
		assignment := make([]int, len(in.Tasks))
		v := encoding
		for i := range assignment {
			assignment[i] = v%(len(in.Nodes)+1) - 1
			v /= len(in.Nodes) + 1
		}
		totals := make([]int, len(in.Nodes))
		accepted := 0
		valid := true
		for i, j := range assignment {
			if j >= 0 {
				totals[j] += in.Tasks[i].ServiceTicks[j]
				accepted++
			}
		}
		for j, load := range totals {
			if load+in.Nodes[j].NetworkTicks > in.HorizonTicks {
				valid = false
			}
		}
		if valid && accepted > best {
			best = accepted
		}
	}
	return best
}

func TestExactRejectsEarlyLongTask(t *testing.T) {
	in := Instance{HorizonTicks: 10, TickMS: 1, Nodes: []Node{{Name: "n", FreeFraction: 1}}, Tasks: []Task{{ServiceTicks: []int{8}}, {ServiceTicks: []int{5}}, {ServiceTicks: []int{5}}}}
	h := Heuristic(in, [3]float64{.3, .5, .2}, 20)
	o := Exact(in)
	if h.Accepted != 1 || o.Accepted != 2 || !o.Optimal {
		t.Fatalf("heuristic=%+v exact=%+v", h, o)
	}
	if err := ValidateAssignment(in, o); err != nil {
		t.Fatal(err)
	}
}

func TestExactMatchesIndependentEnumeration(t *testing.T) {
	rng := rand.New(rand.NewSource(20260928))
	for trial := 0; trial < 150; trial++ {
		nodes := 1 + rng.Intn(3)
		tasks := 1 + rng.Intn(7)
		in := Instance{HorizonTicks: 8 + rng.Intn(20), TickMS: 1, Nodes: make([]Node, nodes), Tasks: make([]Task, tasks)}
		for j := range in.Nodes {
			in.Nodes[j] = Node{Name: "n", NetworkTicks: rng.Intn(4), FreeFraction: 1, DeviceWeight: .5}
		}
		for i := range in.Tasks {
			in.Tasks[i].ServiceTicks = make([]int, nodes)
			for j := range in.Nodes {
				in.Tasks[i].ServiceTicks[j] = 1 + rng.Intn(18)
			}
		}
		got := Exact(in)
		want := bruteForce(in)
		if got.Accepted != want || !got.Optimal {
			t.Fatalf("trial %d got %d want %d instance %+v", trial, got.Accepted, want, in)
		}
		if err := ValidateAssignment(in, got); err != nil {
			t.Fatal(err)
		}
		h := Heuristic(in, [3]float64{.3, .5, .2}, 20)
		if err := ValidateAssignment(in, h); err != nil {
			t.Fatal(err)
		}
		if h.Accepted > got.Accepted {
			t.Fatal("negative objective gap")
		}
	}
}

func TestCertificateRejectsInvalidAssignments(t *testing.T) {
	in := Instance{HorizonTicks: 10, TickMS: 1, Nodes: []Node{{NetworkTicks: 2}}, Tasks: []Task{{ServiceTicks: []int{5}}, {ServiceTicks: []int{5}}}}
	for _, bad := range []Solution{{Assignment: []int{0, 0}, Accepted: 2}, {Assignment: []int{1, -1}, Accepted: 1}, {Assignment: []int{0}, Accepted: 1}, {Assignment: []int{-2, -1}}, {Assignment: []int{0, -1}, Accepted: 2}} {
		if ValidateAssignment(in, bad) == nil {
			t.Fatalf("accepted invalid certificate %+v", bad)
		}
	}
	good := Solution{Assignment: []int{0, -1}, Accepted: 1}
	if err := ValidateAssignment(in, good); err != nil {
		t.Fatal(err)
	}
}

func TestHeuristicDeterministicTieAndNetworkBoundary(t *testing.T) {
	in := Instance{HorizonTicks: 10, TickMS: 1, Nodes: []Node{{NetworkTicks: 2, FreeFraction: 1}, {NetworkTicks: 2, FreeFraction: 1}}, Tasks: []Task{{ServiceTicks: []int{8, 8}}, {ServiceTicks: []int{9, 9}}}}
	got := Heuristic(in, [3]float64{1, 0, 0}, 20)
	if !reflect.DeepEqual(got.Assignment, []int{0, -1}) {
		t.Fatalf("got %+v", got)
	}
}

func TestGeneratedInstancesReproducibleAndValid(t *testing.T) {
	calibration := map[string]Calibration{"cpu": {P95: 21.283347}, "memory": {P95: 78.898112}, "io": {P95: 59.614709}}
	a := Generate(calibration, 20260928, 8, 3, .75)
	b := Generate(calibration, 20260928, 8, 3, .75)
	if !reflect.DeepEqual(a, b) {
		t.Fatal("same seed differs")
	}
	if reflect.DeepEqual(a, Generate(calibration, 20260929, 8, 3, .75)) {
		t.Fatal("different seeds identical")
	}
	if err := ValidateInstance(a); err != nil {
		t.Fatal(err)
	}
	if len(a.Tasks) != 8 || len(a.Nodes) != 3 {
		t.Fatal("incorrect dimensions")
	}
	for _, task := range a.Tasks {
		for _, ticks := range task.ServiceTicks {
			if ticks <= 0 {
				t.Fatal("invalid service")
			}
		}
	}
	a.Tasks[0].ServiceTicks = nil
	if ValidateInstance(a) == nil {
		t.Fatal("missing per-node service accepted")
	}
}

func TestSummaryUsesComputedObjectives(t *testing.T) {
	rows := []Comparison{{Instance: Instance{Tasks: make([]Task, 6), Nodes: make([]Node, 2)}, Heuristic: Solution{Accepted: 3}, Exact: Solution{Accepted: 4}}, {Instance: Instance{Tasks: make([]Task, 6), Nodes: make([]Node, 2)}, Heuristic: Solution{Accepted: 4}, Exact: Solution{Accepted: 4}}}
	got := Summarize(rows)
	if len(got) != 1 || got[0].Instances != 2 || got[0].MeanRatio != .875 || got[0].MeanGap != .5 || got[0].MinRatio != .75 {
		t.Fatalf("summary %+v", got)
	}
}

func TestRunRecordsReplayableCertificates(t *testing.T) {
	dir := t.TempDir()
	cal := filepath.Join(dir, "cal.json")
	if err := os.WriteFile(cal, []byte(`{"cpu":{"service_p95_ms":21.283347},"memory":{"service_p95_ms":78.898112},"io":{"service_p95_ms":59.614709}}`), 0600); err != nil {
		t.Fatal(err)
	}
	out := filepath.Join(dir, "result")
	cfg := Config{Output: out, Calibration: cal, SourceRoot: "../..", Repetitions: 1}
	if err := Run(cfg); err != nil {
		t.Fatal(err)
	}
	var rows []Comparison
	data, err := os.ReadFile(filepath.Join(out, "results.json"))
	if err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(data, &rows); err != nil {
		t.Fatal(err)
	}
	if len(rows) != 24 {
		t.Fatalf("got %d scenarios, want 24", len(rows))
	}
	for _, r := range rows {
		if !r.Exact.Optimal || r.Gap < 0 {
			t.Fatal("invalid optimization evidence")
		}
		if err := ValidateAssignment(r.Instance, r.Exact); err != nil {
			t.Fatal(err)
		}
	}
	cfg.Output = filepath.Join(dir, "replay")
	cfg.Instances = filepath.Join(out, "instances.json")
	if err = Run(cfg); err != nil {
		t.Fatal(err)
	}
	replay, err := os.ReadFile(filepath.Join(cfg.Output, "results.json"))
	if err != nil {
		t.Fatal(err)
	}
	var rr []Comparison
	if err = json.Unmarshal(replay, &rr); err != nil {
		t.Fatal(err)
	}
	for i := range rows {
		if !reflect.DeepEqual(rows[i].Exact, rr[i].Exact) || !reflect.DeepEqual(rows[i].Heuristic, rr[i].Heuristic) {
			t.Fatal("replay mismatch")
		}
	}
}
