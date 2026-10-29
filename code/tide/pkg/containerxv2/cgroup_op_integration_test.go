//go:build integration
// +build integration

// Run with: sudo go test -tags=integration ./pkg/containerxv2/ -v
//
// Requires cgroup v2 (cgroup2fs) and root privileges. The test creates a
// transient cgroup under /sys/fs/cgroup/tide-test-xxx/ with a specific CPU
// mask, verifies the cpuset.cpus file reflects the mask, and deletes it.

package containerxv2

import (
	"fmt"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/ict/tide/pkg/processor"
)

func TestCgroupRoundtrip(t *testing.T) {
	if os.Geteuid() != 0 {
		t.Skip("requires root (creates/deletes under /sys/fs/cgroup)")
	}
	path := fmt.Sprintf("/tide-test-%d", time.Now().UnixNano())

	// 16-slot cpuset with cores 2,3,5 marked — matches the RSEP-mode layout on
	// a 16-core host (system cores 0-1 reserved, not in mask).
	cpuSet := make([]int32, 16)
	cpuSet[2], cpuSet[3], cpuSet[5] = 1, 1, 1

	// Bypass clsstat/dev guards by leaving res non-nil; CreateCgroup's logic
	// takes the cpuset branch when clsstat singleton is unset.
	CreateCgroup(path, &processor.PhysicResource{CpuSet: cpuSet})
	defer DeleteCgroup(path)

	cpusFile := "/sys/fs/cgroup" + path + "/cpuset.cpus"
	want := "2-3,5"
	b, err := os.ReadFile(cpusFile)
	if err != nil {
		t.Fatalf("read %s: %v", cpusFile, err)
	}
	got := strings.TrimSpace(string(b))
	if got != want {
		t.Fatalf("cpuset.cpus: got %q, want %q", got, want)
	}

	// Explicit delete and assert removal.
	DeleteCgroup(path)
	if _, err := os.Stat("/sys/fs/cgroup" + path); !os.IsNotExist(err) {
		t.Fatalf("cgroup still exists after Delete: %v", err)
	}
}

func TestComputeCgroupQuotaMode(t *testing.T) {
	// Documents the RSEPTest+!EnableCpuset branch: quota-based limiting
	// rather than pinning. Not executed (would require manipulating
	// pkg/dev globals); serves as a code-reading checkpoint.
	t.Skip("documentation test: see CreateCgroup branch when !dev.EnableCpuset")
}
