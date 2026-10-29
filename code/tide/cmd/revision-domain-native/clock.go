package main

type clockSample struct {
	UnixNS       int64  `json:"unix_ns"`
	MonotonicNS  int64  `json:"monotonic_ns"`
	UnixBeforeNS int64  `json:"unix_before_ns"`
	UnixAfterNS  int64  `json:"unix_after_ns"`
	Source       string `json:"clock_source"`
}
