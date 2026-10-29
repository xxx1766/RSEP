package stub

import (
	"encoding/json"
	"os"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

type issue5ResourceSample struct {
	UnixNS      int64  `json:"unix_ns"`
	UserCPUUS   int64  `json:"user_cpu_us"`
	SystemCPUUS int64  `json:"system_cpu_us"`
	RSSBytes    uint64 `json:"rss_bytes"`
	MaxRSSBytes uint64 `json:"max_rss_bytes"`
}

func issue5StartResourceSampler(t *testing.T, path string) func() {
	t.Helper()
	if path == "" {
		return func() {}
	}
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0644)
	if err != nil {
		t.Fatal(err)
	}
	stop, done := make(chan struct{}), make(chan struct{})
	go func() {
		defer close(done)
		defer f.Close()
		encoder := json.NewEncoder(f)
		sample := func() {
			var usage syscall.Rusage
			if err := syscall.Getrusage(syscall.RUSAGE_SELF, &usage); err != nil {
				return
			}
			body, err := os.ReadFile("/proc/self/statm")
			if err != nil {
				return
			}
			fields := strings.Fields(string(body))
			if len(fields) < 2 {
				return
			}
			pages, err := strconv.ParseUint(fields[1], 10, 64)
			if err != nil {
				return
			}
			_ = encoder.Encode(issue5ResourceSample{
				UnixNS:      time.Now().UnixNano(),
				UserCPUUS:   usage.Utime.Sec*1e6 + usage.Utime.Usec,
				SystemCPUUS: usage.Stime.Sec*1e6 + usage.Stime.Usec,
				RSSBytes:    pages * uint64(os.Getpagesize()),
				MaxRSSBytes: uint64(usage.Maxrss) * 1024,
			})
		}
		tick := time.NewTicker(200 * time.Millisecond)
		defer tick.Stop()
		sample()
		for {
			select {
			case <-tick.C:
				sample()
			case <-stop:
				sample()
				return
			}
		}
	}()
	return func() { close(stop); <-done }
}
