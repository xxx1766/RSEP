package main

import (
	"reflect"
	"testing"
)

func TestBenchmarkWorkloadKeepsOriginalDefault(t *testing.T) {
	want, _ := workloadCommands("10")
	got, volume, err := benchmarkWorkload("", "10")
	if err != nil || volume != "" || !reflect.DeepEqual(got, want) {
		t.Fatalf("default launch changed: %v %q %v", got, volume, err)
	}
}

func TestBenchmarkWorkloadUsesOneOwnedBind(t *testing.T) {
	got, volume, err := benchmarkWorkload("/dev/shm/native-validation", "10")
	if err != nil || volume != "/dev/shm/native-validation:/legacy" {
		t.Fatalf("unexpected volume: %q %v", volume, err)
	}
	want := []string{"env", "LEGACY_ROOT=/legacy", "PYTHONPATH=/legacy/deps:/legacy/source/workload:/legacy/source/YOLOX-0.3.0", "PYTHONDONTWRITEBYTECODE=1", "MPLCONFIGDIR=/legacy/tmp/matplotlib", "TIDE_DETECTIONS_PER_REQ=10", "python3", "-u", "/legacy/source/workload/native_worker.py"}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("unexpected command: %v", got)
	}
}

func TestBenchmarkWorkloadRejectsAmbiguousBindAndWrongCount(t *testing.T) {
	for _, root := range []string{"relative", "/tmp/a,b", "/tmp/a:b", "/tmp/a\nb", "/tmp/../a"} {
		if _, _, err := benchmarkWorkload(root, "10"); err == nil {
			t.Fatalf("accepted invalid root %q", root)
		}
	}
	for _, count := range []string{"1", "0", "x"} {
		if _, _, err := benchmarkWorkload("/dev/shm/native-validation", count); err == nil {
			t.Fatalf("accepted wrong legacy count %q", count)
		}
	}
}
