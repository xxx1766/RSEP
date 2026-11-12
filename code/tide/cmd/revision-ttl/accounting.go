package main

import "fmt"

type resourceSample struct {
	NS           int64  `json:"t_ns"`
	TotalCPU     int    `json:"total_cpu"`
	RemainingCPU int    `json:"remaining_cpu"`
	Idle         bool   `json:"post_burst_idle"`
	AliveStubs   int    `json:"alive_stubs"`
	Source       string `json:"source"`
}

type allocationStats struct {
	ObservedNS        int64 `json:"observed_ns"`
	ReservedCPUNS     int64 `json:"reserved_cpu_ns"`
	IdleNS            int64 `json:"post_burst_idle_ns"`
	IdleReservedCPUNS int64 `json:"idle_reserved_cpu_ns"`
	CapacityChanges   int   `json:"observed_capacity_changes"`
	MaxHeartbeatGapNS int64 `json:"max_observed_heartbeat_gap_ns"`
}

func integrateAllocation(samples []resourceSample) (allocationStats, error) {
	var out allocationStats
	if len(samples) < 2 {
		return out, fmt.Errorf("at least two resource observations required")
	}
	lastHeartbeat := int64(-1)
	for i, s := range samples {
		if s.NS < 0 || s.TotalCPU != 2 || s.RemainingCPU < 0 || s.RemainingCPU > s.TotalCPU {
			return out, fmt.Errorf("invalid resource observation at index %d", i)
		}
		if i > 0 {
			previous := samples[i-1]
			dt := s.NS - previous.NS
			if dt < 0 {
				return out, fmt.Errorf("resource observation timestamps decreased")
			}
			allocated := int64(previous.TotalCPU-previous.RemainingCPU) * dt
			out.ObservedNS += dt
			out.ReservedCPUNS += allocated
			if previous.Idle {
				out.IdleNS += dt
				out.IdleReservedCPUNS += allocated
			}
			if previous.RemainingCPU != s.RemainingCPU {
				out.CapacityChanges++
			}
		}
		if s.Source == "heartbeat" {
			if lastHeartbeat >= 0 && s.NS-lastHeartbeat > out.MaxHeartbeatGapNS {
				out.MaxHeartbeatGapNS = s.NS - lastHeartbeat
			}
			lastHeartbeat = s.NS
		}
	}
	return out, nil
}
