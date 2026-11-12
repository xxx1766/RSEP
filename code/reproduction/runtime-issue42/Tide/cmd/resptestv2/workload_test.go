package main

import (
	"reflect"
	"testing"
)

func TestWorkloadCommands(t *testing.T) {
	for _, count := range []string{"1", "10"} {
		got, err := workloadCommands(count)
		want := []string{"env", "TIDE_DETECTIONS_PER_REQ=" + count, "python3", "-u", "/app/entrypoint.py", "--mode=paix"}
		if err != nil || !reflect.DeepEqual(got, want) {
			t.Fatalf("count=%s: %v %v", count, got, err)
		}
	}
	for _, count := range []string{"0", "-1", "1.5", "abc"} {
		if _, err := workloadCommands(count); err == nil {
			t.Fatalf("accepted invalid count %s", count)
		}
	}
}
