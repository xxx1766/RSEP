package main

import (
	"fmt"
	"testing"
)

func TestInitialExecutorCount(t *testing.T) {
	for _, tc := range []struct {
		budget, ccOn, ccOff int
	}{
		{2, 1, 2}, {4, 2, 4}, {6, 3, 6}, {8, 4, 8},
		{10, 5, 10}, {12, 6, 12}, {14, 7, 14}, {16, 8, 16},
	} {
		budget := tc.budget
		for _, cc := range []bool{false, true} {
			t.Run(fmt.Sprintf("cpu%d/cc%t", budget, cc), func(t *testing.T) {
				want := tc.ccOff
				if cc {
					want = tc.ccOn
				}
				got, err := initialExecutorCount(budget, cc)
				if err != nil {
					t.Fatalf("initialExecutorCount(%d, %t): %v", budget, cc, err)
				}
				if got != want {
					t.Fatalf("initialExecutorCount(%d, %t) = %d, want %d", budget, cc, got, want)
				}
			})
		}
	}
}

func TestInitialExecutorCountRejectsInvalidBudget(t *testing.T) {
	for _, budget := range []int{-12, -2, -1, 0, 1, 3, 7, 11, 17, 18} {
		for _, cc := range []bool{false, true} {
			t.Run(fmt.Sprintf("cpu%d/cc%t", budget, cc), func(t *testing.T) {
				got, err := initialExecutorCount(budget, cc)
				if err == nil {
					t.Fatalf("initialExecutorCount(%d, %t) = %d without error; want invalid-budget error", budget, cc, got)
				}
				if got != 0 {
					t.Fatalf("invalid budget returned %d executors; want 0", got)
				}
			})
		}
	}
}
