package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestExitedWorkerReturnsAndUnlinksOwnedQueues(t *testing.T) {
	pattern := fmt.Sprintf("/dev/mqueue/revision-ipc-%d-*", os.Getpid())
	defer func() {
		paths, _ := filepath.Glob(pattern)
		for _, p := range paths {
			os.Remove(p)
		}
	}()
	result := make(chan error, 1)
	go func() { result <- run("/bin/false", filepath.Join(t.TempDir(), "failed"), 1, 64) }()
	select {
	case err := <-result:
		if err == nil {
			t.Fatal("expected child failure")
		}
		if !strings.Contains(err.Error(), "worker exited before reply") {
			t.Fatalf("wrong failure path: %v", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("failed worker leaves PAIX receive hanging")
	}
	paths, _ := filepath.Glob(pattern)
	if len(paths) > 0 {
		t.Fatalf("owned queues survived: %v", paths)
	}
}
