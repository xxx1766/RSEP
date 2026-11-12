package dev

import "time"

func GetEstExecTime(nid string) int {
	configMu.RLock()
	defer configMu.RUnlock()
	v := config.ExecutionMS
	if n, ok := config.NodeExecutionMS[nid]; ok {
		v = n
	}
	return int(time.Duration(v) * time.Millisecond)
}

func GetToleranceTime() int {
	return GetToleranceTimeFor("")
}
