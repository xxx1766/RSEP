//go:build !linux

package revisiondomainworkload

import "time"

var portableBoot = time.Now()

func monotonicNS() (int64, error) { return time.Since(portableBoot).Nanoseconds(), nil }
