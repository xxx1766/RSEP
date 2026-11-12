package main

import (
	"fmt"
	"strconv"
)

func workloadCommands(value string) ([]string, error) {
	if value == "" {
		value = "10"
	}
	n, err := strconv.Atoi(value)
	if err != nil || n <= 0 {
		return nil, fmt.Errorf("invalid TIDE_DETECTIONS_PER_REQ %q", value)
	}
	return []string{"env", "TIDE_DETECTIONS_PER_REQ=" + strconv.Itoa(n), "python3", "-u", "/app/entrypoint.py", "--mode=paix"}, nil
}
