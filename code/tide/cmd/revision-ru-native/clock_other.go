//go:build !linux

package main

import "time"

// Portable API contract tests only. The measurement command is Linux-only.
var testClockStart = time.Now()

func readClock() (clockSample, error) {
	before := time.Now().UnixNano()
	mono := time.Since(testClockStart).Nanoseconds()
	after := time.Now().UnixNano()
	return clockSample{UnixNS: before + (after-before)/2, MonotonicNS: mono, UnixBeforeNS: before, UnixAfterNS: after, Source: "portable-test-process-elapsed"}, nil
}
