package main

import "fmt"

func initialExecutorCountForRequest(budget, demand int, cc bool) (int, error) {
	if demand != 1 && demand != 2 {
		return 0, fmt.Errorf("request CPUs must be1 or2")
	}
	total, err := initialExecutorCount(budget, false)
	if err != nil {
		return 0, err
	}
	if cc {
		return total / demand, nil
	}
	return total, nil
}

func configuredWorkload(root, count, threads string) ([]string, string, error) {
	if threads == "" {
		threads = "2"
	}
	if threads != "1" && threads != "2" {
		return nil, "", fmt.Errorf("torch threads must be1 or2")
	}
	args, volume, err := benchmarkWorkload(root, count)
	if err != nil || root == "" {
		return args, volume, err
	}
	command := append([]string{"env", "TIDE_TORCH_THREADS=" + threads, "TIDE_OPENCV_THREADS=1"}, args[1:]...)
	return command, volume, nil
}
