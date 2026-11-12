package main

import (
	"fmt"
	"math"
	"math/rand"
	"sort"

	"github.com/ict/tide/pkg/schedpolicy"
)

type Request struct {
	ID             int     `json:"id"`
	Kind           string  `json:"kind"`
	SizeMultiplier float64 `json:"size_multiplier"`
	ArrivalTicks   int64   `json:"arrival_ticks"`
	DecisionTicks  int64   `json:"decision_ticks"`
	DeadlineTicks  int64   `json:"deadline_ticks"`
	ServiceTicks   []int64 `json:"service_ticks_by_node"`
}
type Trace struct {
	ID             string    `json:"id"`
	Seed           int64     `json:"seed"`
	OfferedRateRPS float64   `json:"offered_poisson_rate_rps"`
	DurationTicks  int64     `json:"duration_ticks"`
	TickMS         float64   `json:"tick_ms"`
	Requests       []Request `json:"requests"`
}
type Node struct {
	Name           string    `json:"name"`
	TotalCPU       int       `json:"total_cpu"`
	ReservedCPU    int       `json:"existing_executor_cpu_quota"`
	NetworkTicks   int64     `json:"network_response_ticks"`
	FreeFraction   float64   `json:"fixed_free_cpu_fraction"`
	DeviceWeight   float64   `json:"device_weight"`
	ServiceFactors []float64 `json:"service_factors_cpu_memory_io"`
}
type Decision struct {
	RequestID     int
	Node          int
	StartTicks    int64
	FinishTicks   int64
	ResponseTicks int64
	Score         float64
	Accepted      bool
	WithinWindow  bool
	Status        string
}
type Summary struct {
	Arrivals              int     `json:"arrivals"`
	Accepted              int     `json:"accepted"`
	Rejected              int     `json:"rejected"`
	CompletedWithinWindow int     `json:"completed_within_window"`
	DrainedAfterWindow    int     `json:"drained_after_window"`
	CohortYield           float64 `json:"cohort_yield"`
	WindowGoodputRPS      float64 `json:"window_goodput_rps"`
	ActualArrivalRPS      float64 `json:"actual_arrival_rps"`
	MeanAcceptedLatencyMS float64 `json:"mean_accepted_latency_ms"`
	P95AcceptedLatencyMS  float64 `json:"p95_accepted_latency_ms"`
}

func Simulate(trace Trace, nodes []Node, weights [3]float64, scale float64) ([]Decision, Summary) {
	finish := make([]int64, len(nodes))
	decisions := make([]Decision, 0, len(trace.Requests))
	summary := Summary{Arrivals: len(trace.Requests)}
	latencies := []float64{}
	for _, request := range trace.Requests {
		decision := Decision{RequestID: request.ID, Node: -1, StartTicks: -1, FinishTicks: -1, ResponseTicks: -1, Score: -1, Status: "infeasible"}
		if request.DecisionTicks >= request.DeadlineTicks {
			decision.Status = "expired"
		} else {
			best := math.Inf(-1)
			for j, node := range nodes {
				start := max(request.DecisionTicks, finish[j])
				serviceFinish := start + request.ServiceTicks[j]
				response := serviceFinish + node.NetworkTicks
				if response > request.DeadlineTicks {
					continue
				}
				latency := float64(response-request.ArrivalTicks) * trace.TickMS
				score := schedpolicy.Score(node.FreeFraction, latency, node.DeviceWeight, weights, scale)
				if score > best {
					best = score
					decision.Node = j
					decision.StartTicks = start
					decision.FinishTicks = serviceFinish
					decision.ResponseTicks = response
					decision.Score = score
				}
			}
			if decision.Node >= 0 {
				finish[decision.Node] = decision.FinishTicks
				decision.Accepted = true
				decision.Status = "completed_on_time"
				decision.WithinWindow = decision.ResponseTicks < trace.DurationTicks
				summary.Accepted++
				if decision.WithinWindow {
					summary.CompletedWithinWindow++
				} else {
					summary.DrainedAfterWindow++
				}
				latency := float64(decision.ResponseTicks-request.ArrivalTicks) * trace.TickMS
				latencies = append(latencies, latency)
				summary.MeanAcceptedLatencyMS += latency
			}
		}
		if !decision.Accepted {
			summary.Rejected++
		}
		decisions = append(decisions, decision)
	}
	durationSeconds := float64(trace.DurationTicks) * trace.TickMS / 1000
	summary.WindowGoodputRPS = float64(summary.CompletedWithinWindow) / durationSeconds
	summary.ActualArrivalRPS = float64(summary.Arrivals) / durationSeconds
	if summary.Arrivals > 0 {
		summary.CohortYield = float64(summary.Accepted) / float64(summary.Arrivals)
	}
	if summary.Accepted > 0 {
		summary.MeanAcceptedLatencyMS /= float64(summary.Accepted)
		sort.Float64s(latencies)
		summary.P95AcceptedLatencyMS = latencies[int(math.Ceil(.95*float64(len(latencies))))-1]
	}
	return decisions, summary
}

func ModelNodes(high bool) []Node {
	nodes := []Node{
		{Name: "node0", TotalCPU: 4, ReservedCPU: 2, NetworkTicks: 20, FreeFraction: .5, DeviceWeight: .65, ServiceFactors: []float64{1.3, .9, 1.15}},
		{Name: "node1", TotalCPU: 6, ReservedCPU: 2, NetworkTicks: 120, FreeFraction: 2.0 / 3, DeviceWeight: .8, ServiceFactors: []float64{.9, 1.2, .85}},
		{Name: "node2", TotalCPU: 8, ReservedCPU: 2, NetworkTicks: 350, FreeFraction: .75, DeviceWeight: 1, ServiceFactors: []float64{.7, .85, 1.1}},
	}
	if high {
		nodes[0].NetworkTicks = 100
		nodes[1].NetworkTicks = 400
		nodes[2].NetworkTicks = 1000
	}
	return nodes
}

func GenerateTrace(calibration map[string]float64, seed int64, rate, duration float64) Trace {
	trace := Trace{ID: fmt.Sprintf("rate%g-seed%d", rate, seed), Seed: seed, OfferedRateRPS: rate, DurationTicks: int64(math.Round(duration * 10000)), TickMS: .1, Requests: []Request{}}
	rng := rand.New(rand.NewSource(seed))
	nodes := ModelNodes(false)
	kinds := []string{"cpu", "memory", "io"}
	arrivalSeconds := 0.0
	for {
		arrivalSeconds += rng.ExpFloat64() / rate
		arrival := int64(math.Round(arrivalSeconds * 10000))
		if arrival >= trace.DurationTicks {
			break
		}
		kind := rng.Intn(3)
		size := float64(75+rng.Intn(51)) / 100
		request := Request{ID: len(trace.Requests), Kind: kinds[kind], SizeMultiplier: size, ArrivalTicks: arrival, DecisionTicks: arrival, DeadlineTicks: arrival + 2000, ServiceTicks: make([]int64, len(nodes))}
		for j, node := range nodes {
			request.ServiceTicks[j] = int64(math.Ceil(calibration[request.Kind] * size * node.ServiceFactors[kind] / trace.TickMS))
		}
		trace.Requests = append(trace.Requests, request)
	}
	return trace
}

func WeightGrid() [][3]float64 {
	weights := make([][3]float64, 0, 66)
	for resource := 0; resource <= 10; resource++ {
		for latency := 0; latency <= 10-resource; latency++ {
			weights = append(weights, [3]float64{float64(resource) / 10, float64(latency) / 10, float64(10-resource-latency) / 10})
		}
	}
	return weights
}

func ValidateTrace(trace Trace, nodes int) error {
	if trace.DurationTicks <= 0 || trace.TickMS <= 0 || math.IsNaN(trace.TickMS) || math.IsInf(trace.TickMS, 0) {
		return fmt.Errorf("invalid duration or tick size")
	}
	previousArrival, previousDecision := int64(-1), int64(-1)
	for _, request := range trace.Requests {
		if request.ArrivalTicks < 0 || request.ArrivalTicks >= trace.DurationTicks || request.ArrivalTicks < previousArrival || request.DecisionTicks < request.ArrivalTicks || request.DecisionTicks < previousDecision {
			return fmt.Errorf("invalid arrival or decision order")
		}
		if request.DeadlineTicks < request.ArrivalTicks || len(request.ServiceTicks) != nodes {
			return fmt.Errorf("invalid deadline or service vector")
		}
		for _, service := range request.ServiceTicks {
			if service <= 0 {
				return fmt.Errorf("nonpositive service time")
			}
		}
		previousArrival, previousDecision = request.ArrivalTicks, request.DecisionTicks
	}
	return nil
}
