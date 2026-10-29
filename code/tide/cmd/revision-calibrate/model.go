// Restricted offline calibration, not a simulation of Tide's complete runtime.
package main

import (
	"fmt"
	"math"
	"math/rand"
	"sort"

	"github.com/ict/tide/pkg/schedpolicy"
)

const TickMS = .1

type Node struct {
	Name               string
	Local              bool
	CPU, Memory, Quota int
	Network            int64
	Device             float64
}
type Request struct {
	ID                int
	Kind              string
	Arrival, Deadline int64
	Estimate, Actual  []int64
	Memory            int
}
type Trace struct {
	ID, Split, Pattern         string
	Seed                       int64
	Rate                       float64
	Duration, OutStart, OutEnd int64
	Requests                   []Request
}
type Decision struct {
	RequestID, Node                       int
	PredResponse, Start, Finish, Response int64
	Score                                 float64
	Status                                string
}
type Metrics struct {
	Arrivals, Accepted, Rejected, Lost, Late, OnTime, WindowOnTime, DrainedOnTime int
	OutageArrivals, OutageOnTime                                                  int
	GoodputRPS, GoodputNorm, Resilience, CPUUtil, MemoryUtil, Util                float64
	MeanLatencyMS, P95LatencyMS, LatencyCost                                      float64
}
type Run struct {
	ID, TraceID, Split, Network string
	WeightID                    int
	Metrics                     Metrics
}

func Simulate(tr Trace, nodes []Node, w [3]float64) ([]Decision, Metrics) {
	actualEnd, predictedEnd := make([]int64, len(nodes)), make([]int64, len(nodes))
	all := make([]Decision, 0, len(tr.Requests))
	m := Metrics{Arrivals: len(tr.Requests), LatencyCost: 1}
	latencies := []float64{}
	cpuTime, memTime, latencyRatios := 0.0, 0.0, 0.0
	for _, r := range tr.Requests {
		d := Decision{RequestID: r.ID, Node: -1, PredResponse: -1, Start: -1, Finish: -1, Response: -1, Score: -1, Status: "rejected"}
		inOutage := r.Arrival >= tr.OutStart && r.Arrival < tr.OutEnd
		if inOutage {
			m.OutageArrivals++
		}
		best := math.Inf(-1)
		for j, n := range nodes {
			if (!n.Local && inOutage) || r.Memory > n.Memory || n.Quota > n.CPU {
				continue
			}
			// Observably empty queue resets prediction; actual future finishes
			// are never used to estimate the currently busy queue.
			if actualEnd[j] <= r.Arrival {
				predictedEnd[j] = r.Arrival
			}
			response := max(r.Arrival, predictedEnd[j]) + r.Estimate[j] + n.Network
			if response > r.Deadline {
				continue
			}
			score := schedpolicy.Score(float64(n.CPU-n.Quota)/float64(n.CPU), float64(response-r.Arrival)*TickMS, n.Device, w, 100)
			if score > best {
				best = score
				d.Node = j
				d.PredResponse = response
				d.Score = score
			}
		}
		if d.Node < 0 {
			m.Rejected++
			all = append(all, d)
			continue
		}
		m.Accepted++
		j := d.Node
		n := nodes[j]
		predictedEnd[j] = d.PredResponse - n.Network
		d.Start = max(r.Arrival, actualEnd[j])
		d.Finish = d.Start + r.Actual[j]
		actualEnd[j] = d.Finish
		d.Response = d.Finish + n.Network
		occupied := max(int64(0), min(tr.Duration, d.Finish)-d.Start)
		cpuTime += float64(occupied) * float64(n.Quota)
		memTime += float64(occupied) * float64(r.Memory)
		if !n.Local && r.Arrival < tr.OutEnd && d.Response >= tr.OutStart {
			d.Status = "outage_lost"
			m.Lost++
		} else {
			latency := float64(d.Response-r.Arrival) * TickMS
			latencies = append(latencies, latency)
			m.MeanLatencyMS += latency
			latencyRatios += float64(d.Response-r.Arrival) / float64(r.Deadline-r.Arrival)
			if d.Response <= r.Deadline {
				d.Status = "on_time"
				m.OnTime++
				if d.Response < tr.Duration {
					m.WindowOnTime++
				} else {
					m.DrainedOnTime++
				}
				if inOutage {
					m.OutageOnTime++
				}
			} else {
				d.Status = "late"
				m.Late++
			}
		}
		all = append(all, d)
	}
	if m.Arrivals > 0 {
		m.GoodputNorm = float64(m.WindowOnTime) / float64(m.Arrivals)
	}
	m.GoodputRPS = float64(m.WindowOnTime) / (float64(tr.Duration) * TickMS / 1000)
	if m.OutageArrivals > 0 {
		m.Resilience = float64(m.OutageOnTime) / float64(m.OutageArrivals)
	}
	var totalCPU, totalMem int
	for _, n := range nodes {
		totalCPU += n.CPU
		totalMem += n.Memory
	}
	m.CPUUtil = cpuTime / (float64(tr.Duration) * float64(totalCPU))
	m.MemoryUtil = memTime / (float64(tr.Duration) * float64(totalMem))
	m.Util = (m.CPUUtil + m.MemoryUtil) / 2
	if len(latencies) > 0 {
		m.MeanLatencyMS /= float64(len(latencies))
		m.LatencyCost = math.Min(1, latencyRatios/float64(len(latencies)))
		sort.Float64s(latencies)
		m.P95LatencyMS = latencies[int(math.Ceil(.95*float64(len(latencies))))-1]
	}
	return all, m
}

func Reward(m Metrics, l [4]float64) float64 {
	sum := 0.0
	for _, v := range l {
		if math.IsNaN(v) || math.IsInf(v, 0) || v < 0 {
			panic("invalid lambda")
		}
		sum += v
	}
	if sum <= 0 || math.IsInf(sum, 0) {
		panic("invalid lambda sum")
	}
	return (l[0]*m.GoodputNorm + l[1]*m.Resilience + l[2]*m.Util - l[3]*m.LatencyCost) / sum
}

func WeightGrid() [][3]float64 {
	g := [][3]float64{}
	for a := 0; a <= 10; a++ {
		for b := 0; b <= 10-a; b++ {
			g = append(g, [3]float64{float64(a) / 10, float64(b) / 10, float64(10-a-b) / 10})
		}
	}
	return append(g, [3]float64{1.0 / 3, 1.0 / 3, 1.0 / 3}, [3]float64{.45, .35, .20})
}

func Select(rows []Run, l [4]float64, grid [][3]float64) int {
	sums, counts := make([]float64, len(grid)), make([]int, len(grid))
	for _, r := range rows {
		if r.Split == "calibration" {
			sums[r.WeightID] += Reward(r.Metrics, l)
			counts[r.WeightID]++
		}
	}
	best := -1
	value := math.Inf(-1)
	distance := func(w [3]float64) float64 {
		s := 0.0
		for _, x := range w {
			s += (x - 1.0/3) * (x - 1.0/3)
		}
		return s
	}
	for i := range grid {
		if counts[i] == 0 {
			continue
		}
		v := sums[i] / float64(counts[i])
		if v > value+1e-12 || (math.Abs(v-value) <= 1e-12 && (best < 0 || distance(grid[i]) < distance(grid[best])-1e-12)) {
			best = i
			value = v
		}
	}
	return best
}

func ModelNodes(high bool) []Node {
	n := []Node{{"local0", true, 4, 1024, 2, 20, .65}, {"local1", true, 6, 1536, 2, 120, .8}, {"remote2", false, 8, 2048, 2, 350, 1}}
	if high {
		n[0].Network = 100
		n[1].Network = 400
		n[2].Network = 1000
	}
	return n
}

// Service scales are recorded p95 values from weights-02/calibration.json.
// Node factors, memory tokens and multiplicative errors are model inputs.
func GenerateTrace(seed int64, rate float64, pattern, split string, nodes []Node) Trace {
	tr := Trace{ID: fmt.Sprintf("%s-%s-r%g-s%d", split, pattern, rate, seed), Split: split, Pattern: pattern, Seed: seed, Rate: rate, Duration: 300000, OutStart: 100000, OutEnd: 200000}
	rng := rand.New(rand.NewSource(seed))
	base := []float64{21.283347, 78.898112, 59.614709}
	factors := [][3]float64{{1.3, .9, 1.15}, {.9, 1.2, .85}, {.7, .85, 1.1}}
	names := []string{"cpu", "memory", "io"}
	memory := []int{64, 256, 32}
	for segment := 0; segment < 6; segment++ {
		mult := 1.0
		if pattern == "burst" {
			if segment%2 == 0 {
				mult = .5
			} else {
				mult = 1.5
			}
		}
		a := float64(segment * 50000)
		for {
			a += rng.ExpFloat64() * 10000 / (rate * mult)
			arrival := int64(math.Round(a))
			if arrival >= int64((segment+1)*50000) {
				break
			}
			kind := rng.Intn(3)
			size := float64(75+rng.Intn(51)) / 100
			errFactor := .8 + .4*rng.Float64()
			r := Request{ID: len(tr.Requests), Kind: names[kind], Arrival: arrival, Deadline: arrival + 2000, Memory: memory[kind]}
			for j := range nodes {
				e := int64(math.Ceil(base[kind] * factors[j][kind] * size / TickMS))
				r.Estimate = append(r.Estimate, e)
				r.Actual = append(r.Actual, int64(math.Ceil(float64(e)*errFactor)))
			}
			tr.Requests = append(tr.Requests, r)
		}
	}
	return tr
}

func Validate(tr Trace, nodes []Node) error {
	if tr.Duration <= 0 || tr.OutStart < 0 || tr.OutEnd <= tr.OutStart || tr.OutEnd > tr.Duration || len(nodes) == 0 {
		return fmt.Errorf("invalid window or nodes")
	}
	for _, n := range nodes {
		if n.CPU <= 0 || n.Memory <= 0 || n.Quota <= 0 || n.Quota > n.CPU || n.Network < 0 || math.IsNaN(n.Device) || math.IsInf(n.Device, 0) || n.Device < 0 || n.Device > 1 {
			return fmt.Errorf("invalid node")
		}
	}
	previous := int64(-1)
	for i, r := range tr.Requests {
		if r.ID != i || r.Arrival < 0 || r.Arrival < previous || r.Arrival >= tr.Duration || r.Deadline <= r.Arrival || r.Memory <= 0 || len(r.Estimate) != len(nodes) || len(r.Actual) != len(nodes) {
			return fmt.Errorf("invalid request %d", i)
		}
		for j := range nodes {
			if r.Estimate[j] <= 0 || r.Actual[j] <= 0 {
				return fmt.Errorf("invalid service")
			}
		}
		previous = r.Arrival
	}
	return nil
}
