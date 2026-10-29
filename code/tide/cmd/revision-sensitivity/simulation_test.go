package main

import (
	"math"
	"reflect"
	"testing"
)

func TestWeightFlipSelectsDifferentFeasibleNode(t *testing.T) {
	trace := Trace{DurationTicks: 1000, TickMS: 1, Requests: []Request{{ArrivalTicks: 0, DeadlineTicks: 100, ServiceTicks: []int64{10, 30}}}}
	nodes := []Node{{FreeFraction: .5, DeviceWeight: .65}, {FreeFraction: .75, DeviceWeight: 1}}
	latency, _ := Simulate(trace, nodes, [3]float64{0, 1, 0}, 100)
	resource, _ := Simulate(trace, nodes, [3]float64{1, 0, 0}, 100)
	if latency[0].Node != 0 || resource[0].Node != 1 {
		t.Fatalf("latency=%+v resource=%+v", latency, resource)
	}
}

func TestFIFOFeasibilityAndWindowAccounting(t *testing.T) {
	trace := Trace{DurationTicks: 10, TickMS: 1, Requests: []Request{
		{ArrivalTicks: 0, DeadlineTicks: 12, ServiceTicks: []int64{5}},
		{ArrivalTicks: 0, DeadlineTicks: 12, ServiceTicks: []int64{5}},
		{ArrivalTicks: 0, DeadlineTicks: 12, ServiceTicks: []int64{5}},
	}}
	decisions, summary := Simulate(trace, []Node{{NetworkTicks: 2, FreeFraction: 1}}, [3]float64{0, 1, 0}, 100)
	if decisions[0].ResponseTicks != 7 || decisions[1].StartTicks != 5 || decisions[1].ResponseTicks != 12 || decisions[2].Accepted {
		t.Fatalf("decisions %+v", decisions)
	}
	if summary.Arrivals != 3 || summary.Accepted != 2 || summary.Rejected != 1 || summary.CompletedWithinWindow != 1 || summary.DrainedAfterWindow != 1 {
		t.Fatalf("counts %+v", summary)
	}
	if math.Abs(summary.CohortYield-2.0/3) > 1e-12 || summary.WindowGoodputRPS != 100 {
		t.Fatalf("rates %+v", summary)
	}
	if summary.Accepted != summary.CompletedWithinWindow+summary.DrainedAfterWindow {
		t.Fatal("drain invariant")
	}
}

func TestExpiredRequestRejectedBeforeScoring(t *testing.T) {
	trace := Trace{DurationTicks: 100, TickMS: 1, Requests: []Request{{ArrivalTicks: 0, DecisionTicks: 11, DeadlineTicks: 10, ServiceTicks: []int64{1}}}}
	d, s := Simulate(trace, []Node{{FreeFraction: 1}}, [3]float64{0, 1, 0}, 100)
	if d[0].Accepted || d[0].Node != -1 || d[0].Status != "expired" || s.Rejected != 1 {
		t.Fatalf("decision %+v summary %+v", d, s)
	}
}

func TestResponseAtWindowEndIsDrainedButMeetsDeadline(t *testing.T) {
	trace := Trace{DurationTicks: 10, TickMS: 1, Requests: []Request{{ArrivalTicks: 0, DeadlineTicks: 10, ServiceTicks: []int64{8}}}}
	decisions, summary := Simulate(trace, []Node{{NetworkTicks: 2, FreeFraction: 1}}, [3]float64{0, 1, 0}, 100)
	if !decisions[0].Accepted || decisions[0].ResponseTicks != 10 || decisions[0].WithinWindow {
		t.Fatalf("endpoint response must meet its deadline but fall outside [0,T): %+v", decisions[0])
	}
	if summary.Accepted != 1 || summary.CompletedWithinWindow != 0 || summary.DrainedAfterWindow != 1 || summary.CohortYield != 1 || summary.WindowGoodputRPS != 0 {
		t.Fatalf("endpoint accounting: %+v", summary)
	}
}

func TestSeededTraceReplayAndFullSimplex(t *testing.T) {
	cal := map[string]float64{"cpu": 21.283347, "memory": 78.898112, "io": 59.614709}
	a := GenerateTrace(cal, 43, 20, 10)
	b := GenerateTrace(cal, 43, 20, 10)
	if !reflect.DeepEqual(a, b) {
		t.Fatal("same seed differs")
	}
	if reflect.DeepEqual(a, GenerateTrace(cal, 44, 20, 10)) {
		t.Fatal("distinct seeds equal")
	}
	nodes := ModelNodes(false)
	d, s := Simulate(a, nodes, [3]float64{.3, .5, .2}, 100)
	dd, ss := Simulate(b, nodes, [3]float64{.3, .5, .2}, 100)
	if !reflect.DeepEqual(d, dd) || !reflect.DeepEqual(s, ss) {
		t.Fatal("replay differs")
	}
	if s.Arrivals != len(a.Requests) || s.Arrivals != s.Accepted+s.Rejected {
		t.Fatal("cohort invariant")
	}
	grid := WeightGrid()
	if len(grid) != 66 {
		t.Fatalf("grid count %d", len(grid))
	}
	seen := map[[3]int]bool{}
	for _, w := range grid {
		key := [3]int{int(math.Round(w[0] * 10)), int(math.Round(w[1] * 10)), int(math.Round(w[2] * 10))}
		if seen[key] || key[0]+key[1]+key[2] != 10 {
			t.Fatal("invalid simplex")
		}
		seen[key] = true
	}
}

func TestInvalidTraceRejected(t *testing.T) {
	trace := Trace{DurationTicks: 10, TickMS: 1, Requests: []Request{{ArrivalTicks: 0, DeadlineTicks: 10, ServiceTicks: []int64{1}}}}
	if err := ValidateTrace(trace, 1); err != nil {
		t.Fatal(err)
	}
	trace.Requests[0].DecisionTicks = -1
	if ValidateTrace(trace, 1) == nil {
		t.Fatal("decision before arrival accepted")
	}
	trace.Requests[0].DecisionTicks = 0
	trace.Requests[0].ServiceTicks = nil
	if ValidateTrace(trace, 1) == nil {
		t.Fatal("invalid service vector accepted")
	}
}
