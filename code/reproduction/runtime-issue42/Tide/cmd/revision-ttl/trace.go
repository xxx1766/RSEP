package main

import (
	"fmt"
	"strconv"
	"strings"
	"time"
)

const observationMargin = 50 * time.Millisecond

func parseIdleGaps(raw string, ttlMS int) ([]time.Duration, error) {
	parts := strings.Split(raw, ",")
	if len(parts) != 3 {
		return nil, fmt.Errorf("--idle-gaps-ms requires exactly three post-burst gaps")
	}
	gaps := make([]time.Duration, len(parts))
	for i, part := range parts {
		n, err := strconv.Atoi(strings.TrimSpace(part))
		if err != nil || n <= 0 || n > 86400000 {
			return nil, fmt.Errorf("invalid idle gap %q", part)
		}
		gaps[i] = time.Duration(n) * time.Millisecond
	}
	if gaps[len(gaps)-1] <= time.Duration(ttlMS)*time.Millisecond+observationMargin {
		return nil, fmt.Errorf("final idle gap must exceed TTL by more than 50ms for observed release")
	}
	return gaps, nil
}

func gapExpectation(gap, ttl time.Duration) string {
	if gap > ttl+observationMargin {
		return "released"
	}
	if gap < ttl-observationMargin {
		return "retained"
	}
	return "boundary"
}
