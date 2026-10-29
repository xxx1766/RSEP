package serve

import "fmt"

// The current processor assigns logical CPUs starting at zero. Refuse a
// configuration it cannot honor instead of silently advertising unavailable CPUs.
func validateCPUCount(count int, allowed []int) error {
	if count <= 0 || count > len(allowed) {
		return fmt.Errorf("--cpu-num must be between 1 and %d allowed CPUs (got %d)", len(allowed), count)
	}
	available := make(map[int]bool, len(allowed))
	for _, cpu := range allowed {
		available[cpu] = true
	}
	for cpu := 0; cpu < count; cpu++ {
		if !available[cpu] {
			return fmt.Errorf("--cpu-num=%d requires logical CPUs 0..%d; CPU %d is not allowed by process affinity", count, count-1, cpu)
		}
	}
	return nil
}
