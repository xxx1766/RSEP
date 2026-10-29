package dev

import "time"

// GetEstExecTime — estimated per-routine execution time on this node.
// The RSEP scheduler (pkg/stub/sched) uses this to decide whether the local
// queue can meet QoS within TQoS; if the estimate is too high, CC's queue
// predictions are pessimistic (premature offload); too low, they're optimistic
// (routines accepted and then miss QoS). Measure per-workload before runs.
//
// 2026-04-21 (1× detection, light workload, archived in results-light/):
//   - httpbase (EI=off):    median ~49 ms, p95 ~54 ms
//   - resptestv2 (EI=on):   median ~56 ms, p95 ~60 ms
//
// 2026-05-10 (10× detection per request, paper-matched workload):
//   - smoke test C3 RPS=4: avg 426 ms, p50 424 ms, p95 450 ms (1 run, 80 req)
//   - ORT session sharing + warm cache means 10× inference is sub-linear
//     (preprocess once + 10×ONNX run + ~10ms serialization). Settled on
//     430 ms median.
//
// Paper §VI.A used 250 ms as the assumed value on i7-8565U @ 1.80 GHz; we
// measure 430 ms here on Xeon @ 2.20 GHz. CC schedules with this estimate, so
// under/over-estimation directly shifts the saturation knee.
func GetEstExecTime(nid string) int {
	configMu.RLock()
	defer configMu.RUnlock()
	v := config.ExecutionMS
	if n, ok := config.NodeExecutionMS[nid]; ok {
		v = n
	}
	return int(time.Duration(v) * time.Millisecond)
}

// GetToleranceTime — QoS deadline used when computing whether a routine can
// fit locally. Keep it ~1.5× exec time so the scheduler has some slack.
func GetToleranceTime() int {
	return GetToleranceTimeFor("")
}
