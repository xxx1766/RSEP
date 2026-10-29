//go:build linux

package revisiondomainworkload

import "golang.org/x/sys/unix"

func monotonicNS() (int64, error) {
	var ts unix.Timespec
	if err := unix.ClockGettime(unix.CLOCK_MONOTONIC, &ts); err != nil {
		return 0, err
	}
	return ts.Nano(), nil
}
