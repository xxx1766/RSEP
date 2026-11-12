//go:build !linux

package main

import "time"

var testClockStart = time.Now()

func readClock() (clockSample, error) {
	before := time.Now().UnixNano()
	mono := time.Since(testClockStart).Nanoseconds()
	after := time.Now().UnixNano()
	return clockSample{UnixNS: before + (after-before)/2, MonotonicNS: mono, UnixBeforeNS: before, UnixAfterNS: after, Source: "portable-test-process-elapsed"}, nil
}
