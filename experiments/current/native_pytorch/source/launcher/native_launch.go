package main

import (
	"fmt"
	"path/filepath"
	"strings"
)

func benchmarkWorkload(root, count string) ([]string, string, error) {
	if root == "" {
		command, err := workloadCommands(count)
		return command, "", err
	}
	if !filepath.IsAbs(root) || filepath.Clean(root) != root || strings.ContainsAny(root, ",:\n\r") {
		return nil, "", fmt.Errorf("invalid owned workload root %q", root)
	}
	if count != "10" {
		return nil, "", fmt.Errorf("legacy validation requires ten detections, got %q", count)
	}
	command := []string{"env", "LEGACY_ROOT=/legacy", "PYTHONPATH=/legacy/deps:/legacy/source/workload:/legacy/source/YOLOX-0.3.0", "PYTHONDONTWRITEBYTECODE=1", "MPLCONFIGDIR=/legacy/tmp/matplotlib", "TIDE_DETECTIONS_PER_REQ=10", "python3", "-u", "/legacy/source/workload/native_worker.py"}
	return command, root + ":/legacy", nil
}
