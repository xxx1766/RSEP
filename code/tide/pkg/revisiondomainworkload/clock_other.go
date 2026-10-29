//go:build !linux

package revisiondomainworkload

import "time"

var portableBoot = time.Now()

// Portable contract tests only; real Linux workers use CLOCK_MONOTONIC.
func monotonicNS() (int64, error) { return time.Since(portableBoot).Nanoseconds(), nil }
