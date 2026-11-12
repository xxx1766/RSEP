package main

import (
	"strings"
	"testing"
)

func TestExperimentRequestCPUCount(t *testing.T) {
	for _, tc := range []struct {
		demand int
		cc     bool
		want   int
	}{{1, true, 6}, {2, true, 3}, {2, false, 6}} {
		got, err := initialExecutorCountForRequest(6, tc.demand, tc.cc)
		if err != nil || got != tc.want {
			t.Fatalf("demand=%d cc=%t got=%d err=%v", tc.demand, tc.cc, got, err)
		}
	}
	for _, demand := range []int{0, 3, -1} {
		if _, err := initialExecutorCountForRequest(6, demand, true); err == nil {
			t.Fatalf("accepted demand %d", demand)
		}
	}
}

func TestConfiguredWorkloadThreads(t *testing.T) {
	for _, threads := range []string{"1", "2"} {
		args, vol, err := configuredWorkload("/dev/shm/owned", "10", threads)
		joined := strings.Join(args, " ")
		if err != nil || vol != "/dev/shm/owned:/legacy" || !strings.Contains(joined, "TIDE_TORCH_THREADS="+threads) || !strings.Contains(joined, "TIDE_OPENCV_THREADS=1") {
			t.Fatalf("%v %s %v", args, vol, err)
		}
	}
	if _, _, err := configuredWorkload("/dev/shm/owned", "10", "4"); err == nil {
		t.Fatal("accepted unplanned thread count")
	}
}
