package main

import (
	"fmt"
	"golang.org/x/sys/unix"
	"os"
	"strconv"
	"strings"
)

func setCPUs(list string) error {
	if list == "" {
		return nil
	}
	var set unix.CPUSet
	for _, v := range strings.Split(list, ",") {
		n, err := strconv.Atoi(v)
		if err != nil || n < 0 || n >= 1024 {
			return fmt.Errorf("invalid CPU list %q", list)
		}
		set.Set(n)
	}
	for pass := 0; pass < 3; pass++ {
		tasks, err := os.ReadDir("/proc/self/task")
		if err != nil {
			return err
		}
		for _, task := range tasks {
			pid, _ := strconv.Atoi(task.Name())
			if err := unix.SchedSetaffinity(pid, &set); err != nil && err != unix.ESRCH {
				return err
			}
		}
	}
	return nil
}
