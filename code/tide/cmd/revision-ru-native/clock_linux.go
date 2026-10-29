//go:build linux

package main

import (
	"golang.org/x/sys/unix"
	"time"
)

func readClock() (clockSample, error) {
	before := time.Now().UnixNano()
	var ts unix.Timespec
	if err := unix.ClockGettime(unix.CLOCK_MONOTONIC, &ts); err != nil {
		return clockSample{}, err
	}
	after := time.Now().UnixNano()
	return clockSample{UnixNS: before + (after-before)/2, MonotonicNS: ts.Nano(), UnixBeforeNS: before, UnixAfterNS: after, Source: "CLOCK_MONOTONIC"}, nil
}
