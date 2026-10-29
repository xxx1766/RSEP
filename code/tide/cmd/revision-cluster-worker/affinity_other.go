//go:build !linux

package main

import "fmt"

func setCPUs(list string) error {
	if list != "" {
		return fmt.Errorf("CPU affinity requires Linux")
	}
	return nil
}
