package main

import "testing"

func TestAllocationIntegralUsesObservedTransitionsAndIdleWindows(t *testing.T) {
	samples := []resourceSample{
		{NS: 0, TotalCPU: 2, RemainingCPU: 2, Source: "heartbeat"},
		{NS: 10, TotalCPU: 2, RemainingCPU: 0, Source: "heartbeat"},
		{NS: 40, TotalCPU: 2, RemainingCPU: 0, Idle: true, Source: "phase"},
		{NS: 70, TotalCPU: 2, RemainingCPU: 2, Idle: true, Source: "heartbeat"},
		{NS: 100, TotalCPU: 2, RemainingCPU: 2, Idle: true, Source: "end"},
	}
	got, err := integrateAllocation(samples)
	if err != nil {
		t.Fatal(err)
	}
	if got.ReservedCPUNS != 120 || got.IdleReservedCPUNS != 60 || got.IdleNS != 60 || got.ObservedNS != 100 {
		t.Fatalf("wrong allocation accounting: %+v", got)
	}
	if got.CapacityChanges != 2 || got.MaxHeartbeatGapNS != 60 {
		t.Fatalf("wrong observed transition/resolution accounting: %+v", got)
	}
}

func TestAllocationIntegralRejectsInvalidObservations(t *testing.T) {
	for _, samples := range [][]resourceSample{
		nil,
		{{NS: 0, TotalCPU: 2, RemainingCPU: 2}},
		{{NS: 10, TotalCPU: 2}, {NS: 9, TotalCPU: 2}},
		{{NS: 0, TotalCPU: 2}, {NS: 9, TotalCPU: 2, RemainingCPU: 3}},
		{{NS: 0, TotalCPU: 2}, {NS: 9, TotalCPU: 2, RemainingCPU: -1}},
		{{NS: 0, TotalCPU: 2}, {NS: 9, TotalCPU: 4}},
	} {
		if _, err := integrateAllocation(samples); err == nil {
			t.Fatalf("accepted invalid observations: %+v", samples)
		}
	}
}

func TestEqualTimestampTransitionDoesNotInventAllocationTime(t *testing.T) {
	got, err := integrateAllocation([]resourceSample{
		{NS: 0, TotalCPU: 2, RemainingCPU: 2},
		{NS: 0, TotalCPU: 2, RemainingCPU: 0, Idle: true},
		{NS: 20, TotalCPU: 2, RemainingCPU: 0, Idle: true},
	})
	if err != nil || got.IdleReservedCPUNS != 40 {
		t.Fatalf("same-time transition: %+v, %v", got, err)
	}
}
