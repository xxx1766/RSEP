package main

import "fmt"

func initialExecutorCount(applicationCPUs int, concurrencyControl bool) (int, error) {
	if applicationCPUs <= 0 || applicationCPUs > 16 || applicationCPUs%2 != 0 {
		return 0, fmt.Errorf("-cpu-num must be an even number from 2 to 16, got %d", applicationCPUs)
	}
	if concurrencyControl {
		return applicationCPUs / 2, nil
	}
	return applicationCPUs, nil
}
