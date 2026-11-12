package serve

import (
	"strings"
	"testing"

	"golang.org/x/sys/unix"
)

func TestServeRejectsUnavailableCPUsBeforeStartup(t *testing.T) {
	oldCPU := cpuNum
	t.Cleanup(func() { cpuNum = oldCPU })
	cpuNum = 0
	if err := ServeCmd.PreRunE(ServeCmd, nil); err == nil || !strings.Contains(err.Error(), "--cpu-num") {
		t.Fatalf("invalid CPU count should fail before startup: %v", err)
	}
}

func TestServePreservesExplicitCPUCountAndValidatesLogLevel(t *testing.T) {
	var affinity unix.CPUSet
	if err := unix.SchedGetaffinity(0, &affinity); err != nil {
		t.Fatal(err)
	}
	if !affinity.IsSet(0) {
		t.Skip("current scheduler requires logical CPU 0")
	}
	oldCPU, oldLevel := cpuNum, logLevel
	t.Cleanup(func() { cpuNum, logLevel = oldCPU, oldLevel })
	cpuNum, logLevel = 1, "warn"
	if err := ServeCmd.PreRunE(ServeCmd, nil); err != nil {
		t.Fatal(err)
	}
	if cpuNum != 1 {
		t.Fatalf("explicit --cpu-num changed to %d", cpuNum)
	}
	logLevel = "invalid-level"
	if err := ServeCmd.PreRunE(ServeCmd, nil); err == nil || !strings.Contains(err.Error(), "--log-level") {
		t.Fatalf("invalid log level should fail before startup: %v", err)
	}
}
