// revision-model compares a score-based admission heuristic with an exact
// cardinality assignment solver in an explicitly restricted offline model.
package main

import (
	"fmt"
	"math"
	"sort"

	"github.com/ict/tide/pkg/schedpolicy"
)

type Node struct {
	Name           string    `json:"name"`
	NetworkTicks   int       `json:"network_ticks"`
	FreeFraction   float64   `json:"free_fraction"`
	DeviceWeight   float64   `json:"device_weight"`
	CPUQuota       int       `json:"cpu_quota"`
	ServiceFactors []float64 `json:"service_factors_cpu_memory_io"`
}
type Task struct {
	ID             int     `json:"id"`
	Kind           string  `json:"kind"`
	SizeMultiplier float64 `json:"size_multiplier"`
	ServiceTicks   []int   `json:"service_ticks_by_node"`
}
type Instance struct {
	ID           string  `json:"id"`
	Seed         int64   `json:"seed"`
	Pressure     float64 `json:"horizon_multiplier"`
	HorizonTicks int     `json:"common_deadline_ticks"`
	TickMS       float64 `json:"tick_ms"`
	Nodes        []Node  `json:"nodes"`
	Tasks        []Task  `json:"tasks_in_arrival_order"`
}
type SearchStats struct {
	Visited     uint64 `json:"visited_states"`
	BoundPruned uint64 `json:"bound_pruned_states"`
	Leaves      uint64 `json:"leaves"`
}
type Solution struct {
	Assignment []int       `json:"node_index_by_task_minus_one_rejected"`
	Accepted   int         `json:"accepted"`
	Optimal    bool        `json:"proven_optimal"`
	Stats      SearchStats `json:"search"`
}

// Network is a fixed per-node response budget, not serial executor work.
// Thus a node's entire batch needs sum(service) + network <= deadline.
func ValidateAssignment(in Instance, s Solution) error {
	if len(s.Assignment) != len(in.Tasks) {
		return fmt.Errorf("assignment length mismatch")
	}
	loads := make([]int, len(in.Nodes))
	accepted := 0
	for i, j := range s.Assignment {
		if j < -1 || j >= len(in.Nodes) {
			return fmt.Errorf("invalid node %d for task %d", j, i)
		}
		if j >= 0 {
			loads[j] += in.Tasks[i].ServiceTicks[j]
			accepted++
		}
	}
	if accepted != s.Accepted {
		return fmt.Errorf("certificate count %d != objective %d", accepted, s.Accepted)
	}
	for j, load := range loads {
		if load > 0 && load+in.Nodes[j].NetworkTicks > in.HorizonTicks {
			return fmt.Errorf("node %d misses deadline", j)
		}
	}
	return nil
}

// Input order is preserved. Ties use the first feasible node by index.
func Heuristic(in Instance, weights [3]float64, latencyScale float64) Solution {
	result := Solution{Assignment: make([]int, len(in.Tasks))}
	loads := make([]int, len(in.Nodes))
	for i, task := range in.Tasks {
		chosen := -1
		best := math.Inf(-1)
		for j, node := range in.Nodes {
			completion := loads[j] + task.ServiceTicks[j] + node.NetworkTicks
			if completion > in.HorizonTicks {
				continue
			}
			score := schedpolicy.Score(node.FreeFraction, float64(completion)*in.TickMS, node.DeviceWeight, weights, latencyScale)
			if score > best {
				best = score
				chosen = j
			}
		}
		result.Assignment[i] = chosen
		if chosen >= 0 {
			loads[chosen] += task.ServiceTicks[chosen]
			result.Accepted++
		}
	}
	return result
}

// Exact explores assignment or rejection for every task, independently of the
// heuristic. The optimistic bound pools residual capacities and gives each
// remaining task its cheapest currently feasible node cost. This relaxation
// can only overestimate the number of additional admissions.
func Exact(in Instance) Solution {
	n := len(in.Tasks)
	order := make([]int, n)
	for i := range order {
		order[i] = i
	}
	minCost := func(i int) int {
		v := in.Tasks[i].ServiceTicks[0]
		for _, x := range in.Tasks[i].ServiceTicks {
			if x < v {
				v = x
			}
		}
		return v
	}
	sort.SliceStable(order, func(a, b int) bool { return minCost(order[a]) < minCost(order[b]) })
	remaining := make([]int, len(in.Nodes))
	for j, node := range in.Nodes {
		remaining[j] = max(0, in.HorizonTicks-node.NetworkTicks)
	}
	current := make([]int, n)
	for i := range current {
		current[i] = -1
	}
	result := Solution{Assignment: append([]int(nil), current...), Optimal: true}
	var visit func(int, int)
	visit = func(depth, count int) {
		result.Stats.Visited++
		if count > result.Accepted {
			result.Accepted = count
			copy(result.Assignment, current)
		}
		if depth == n {
			result.Stats.Leaves++
			return
		}
		costs := make([]int, 0, n-depth)
		budget := 0
		for _, v := range remaining {
			budget += v
		}
		for _, i := range order[depth:] {
			best := math.MaxInt
			for j, cost := range in.Tasks[i].ServiceTicks {
				if cost <= remaining[j] && cost < best {
					best = cost
				}
			}
			if best != math.MaxInt {
				costs = append(costs, best)
			}
		}
		sort.Ints(costs)
		bound := count
		for _, cost := range costs {
			if cost > budget {
				break
			}
			budget -= cost
			bound++
		}
		if bound <= result.Accepted {
			result.Stats.BoundPruned++
			return
		}
		i := order[depth]
		for j, cost := range in.Tasks[i].ServiceTicks {
			if cost <= remaining[j] {
				current[i] = j
				remaining[j] -= cost
				visit(depth+1, count+1)
				remaining[j] += cost
				current[i] = -1
			}
		}
		visit(depth+1, count)
	}
	visit(0, 0)
	return result
}
