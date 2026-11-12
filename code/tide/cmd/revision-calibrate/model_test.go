package main

import (
	"math"
	"testing"
)

func fixture() (Trace, []Node) {
	return Trace{ID: "fixture", Duration: 100, OutStart: 30, OutEnd: 60,
			Requests: []Request{{ID: 0, Arrival: 0, Deadline: 20, Estimate: []int64{20}, Actual: []int64{20}, Memory: 50}}},
		[]Node{{Name: "local", Local: true, CPU: 2, Memory: 100, Quota: 2, Device: 1}}
}

func TestDeadlineEqualityAndCapacityTime(t *testing.T) {
	tr, ns := fixture()
	d, m := Simulate(tr, ns, [3]float64{0, 1, 0})
	if d[0].Status != "on_time" || m.WindowOnTime != 1 || m.Accepted != 1 {
		t.Fatalf("deadline equality: %+v %+v", d, m)
	}
	if math.Abs(m.CPUUtil-.2) > 1e-12 || math.Abs(m.MemoryUtil-.1) > 1e-12 || math.Abs(m.Util-.15) > 1e-12 {
		t.Fatalf("wrong capacity-time denominator: %+v", m)
	}
}

func TestOutageBoundariesAndLostWorkStillOccupiesExecutor(t *testing.T) {
	tr, ns := fixture()
	ns[0].Local = false
	tr.Requests = []Request{
		{ID: 0, Arrival: 10, Deadline: 80, Estimate: []int64{40}, Actual: []int64{40}, Memory: 50},
		{ID: 1, Arrival: 30, Deadline: 90, Estimate: []int64{10}, Actual: []int64{10}, Memory: 50},
		{ID: 2, Arrival: 60, Deadline: 90, Estimate: []int64{10}, Actual: []int64{10}, Memory: 50},
	}
	d, m := Simulate(tr, ns, [3]float64{0, 1, 0})
	if d[0].Status != "outage_lost" || d[1].Node != -1 || d[2].Status != "on_time" {
		t.Fatalf("outage boundaries: %+v", d)
	}
	if m.Lost != 1 || m.Rejected != 1 || math.Abs(m.CPUUtil-.5) > 1e-12 || m.Resilience != 0 {
		t.Fatalf("lost work/resource accounting: %+v", m)
	}
	tr.Requests = []Request{{Arrival: 10, Deadline: 80, Estimate: []int64{20}, Actual: []int64{20}, Memory: 50}}
	d, _ = Simulate(tr, ns, [3]float64{0, 1, 0})
	if d[0].Status != "outage_lost" {
		t.Fatal("response at outage start must be lost")
	}
}

func TestPredictionCannotSeeActualServiceOrFutureOutage(t *testing.T) {
	tr, ns := fixture()
	tr.Requests[0].Actual[0] = 40
	d, m := Simulate(tr, ns, [3]float64{0, 1, 0})
	if d[0].PredResponse != 20 || d[0].Response != 40 || m.Late != 1 || m.WindowOnTime != 0 {
		t.Fatalf("oracle prediction or deadline accounting: %+v %+v", d, m)
	}
}

func TestDifferentWeightsChangeCandidate(t *testing.T) {
	tr, ns := fixture()
	ns = append(ns, Node{Name: "b", Local: true, CPU: 8, Memory: 100, Quota: 2, Network: 5, Device: 1})
	tr.Requests[0].Estimate = []int64{10, 10}
	tr.Requests[0].Actual = []int64{10, 10}
	a, _ := Simulate(tr, ns, [3]float64{1, 0, 0})
	b, _ := Simulate(tr, ns, [3]float64{0, 1, 0})
	if a[0].Node != 1 || b[0].Node != 0 {
		t.Fatalf("weights inert: %+v %+v", a, b)
	}
}

func TestGridAndFourObjectiveSelection(t *testing.T) {
	grid := WeightGrid()
	unique := map[[3]float64]bool{}
	for _, w := range grid {
		unique[w] = true
	}
	if len(grid) != 68 || len(unique) != 68 {
		t.Fatalf("grid=%d unique=%d", len(grid), len(unique))
	}
	for k := 0; k < 4; k++ {
		a, b := Metrics{}, Metrics{}
		switch k {
		case 0:
			a.GoodputNorm = 1
		case 1:
			a.Resilience = 1
		case 2:
			a.Util = 1
		case 3:
			b.LatencyCost = 1
		}
		l := [4]float64{}
		l[k] = 1
		if Reward(a, l) <= Reward(b, l) {
			t.Fatalf("objective %d inactive or reversed", k)
		}
	}
	rows := []Run{
		{Split: "calibration", WeightID: 0, Metrics: Metrics{GoodputNorm: .9}},
		{Split: "calibration", WeightID: 1, Metrics: Metrics{GoodputNorm: .8}},
		{Split: "test", WeightID: 0, Metrics: Metrics{GoodputNorm: 0}},
		{Split: "test", WeightID: 1, Metrics: Metrics{GoodputNorm: 1}},
	}
	if Select(rows, [4]float64{1, 0, 0, 0}, grid) != 0 {
		t.Fatal("held-out test leaked into selection")
	}
}

func TestTraceReproducibilityAndInputValidation(t *testing.T) {
	nodes := ModelNodes(false)
	a := GenerateTrace(100, 30, "burst", "calibration", nodes)
	b := GenerateTrace(100, 30, "burst", "calibration", nodes)
	if len(a.Requests) == 0 || len(a.Requests) != len(b.Requests) || a.Requests[0].Arrival != b.Requests[0].Arrival {
		t.Fatal("non-reproducible")
	}
	if err := Validate(a, nodes); err != nil {
		t.Fatal(err)
	}
	a.Requests[0].Actual[0] = 0
	if err := Validate(a, nodes); err == nil {
		t.Fatal("accepted zero service")
	}
}
