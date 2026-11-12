//go:build integration
// +build integration

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

	cpuSet := make([]int32, 16)
	cpuSet[2], cpuSet[3], cpuSet[5] = 1, 1, 1

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

	DeleteCgroup(path)
	if _, err := os.Stat("/sys/fs/cgroup" + path); !os.IsNotExist(err) {
		t.Fatalf("cgroup still exists after Delete: %v", err)
	}
}

func TestComputeCgroupQuotaMode(t *testing.T) {
	t.Skip("documentation test: see CreateCgroup branch when !dev.EnableCpuset")
}
