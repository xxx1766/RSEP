package main

import (
	"testing"
	"time"
)

func TestMixedGapsCrossTheSweptTTLs(t *testing.T) {
	for _, tc := range []struct {
		ttl  int
		want []string
	}{
		{100, []string{"released", "released", "released"}},
		{500, []string{"retained", "released", "released"}},
		{2000, []string{"retained", "retained", "released"}},
	} {
		gaps, err := parseIdleGaps("250,1000,2500", tc.ttl)
		if err != nil {
			t.Fatal(err)
		}
		for i, gap := range gaps {
			if got := gapExpectation(gap, time.Duration(tc.ttl)*time.Millisecond); got != tc.want[i] {
				t.Fatalf("TTL=%d gap=%s: got %s want %s", tc.ttl, gap, got, tc.want[i])
			}
		}
	}
}

func TestGapValidationAndTimingMargin(t *testing.T) {
	for _, raw := range []string{"", "250,1000", "250,1000,2500,3000", "0,1000,2500", "x,1000,2500", "250,1000,2000"} {
		if _, err := parseIdleGaps(raw, 2000); err == nil {
			t.Fatalf("accepted invalid gaps %q", raw)
		}
	}
	for _, ms := range []int{450, 500, 550} {
		if got := gapExpectation(time.Duration(ms)*time.Millisecond, 500*time.Millisecond); got != "boundary" {
			t.Fatalf("near-boundary gap %d got %s", ms, got)
		}
	}
}
