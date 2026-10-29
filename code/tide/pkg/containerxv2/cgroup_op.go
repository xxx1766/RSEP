package containerxv2

import (
	"errors"
	"os"
	"strconv"
	"strings"

	"github.com/containerd/cgroups/v3/cgroup2"
	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/processor"
	"github.com/sirupsen/logrus"
)

// cgroupRoot is the unified v2 mount point. Standard on all modern Linux.
const cgroupRoot = "/sys/fs/cgroup"

// CreateCgroup creates a v2 cgroup at the given path with the given resource.
// Path is passed like "/tide/<id>"; cgroup2.NewManager expects it relative to
// cgroupRoot (the library strips the leading slash internally if present).
func CreateCgroup(path string, res *processor.PhysicResource) {
	logger := logrus.WithFields(logrus.Fields{"CgroupPath": path})
	logger.WithField("HasResource", res != nil).Debug("Containerxv2: start creating cgroup")
	defer logger.Debug("Containerxv2: finish creating cgroup")

	cgRes := &cgroup2.Resources{}
	if res != nil && (clsstat.Singleton().GetSelf() == nil || clsstat.Singleton().GetSelf().Role != clsstat.THINGS) {
		if !dev.RSEPTest || dev.EnableCpuset {
			// PI=on (or non-RSEP): pin to specific logical cores via cpuset.
			cpuIds := make([]string, 0, 1)
			for i, used := range res.CpuSet {
				if used != 0 {
					cpuIds = append(cpuIds, strconv.Itoa(i))
				}
			}
			cgRes.CPU = &cgroup2.CPU{Cpus: strings.Join(cpuIds, ",")}
		} else {
			// PI=off under RSEP: limit CPU time instead of pinning.
			// v1 used period=100ms, quota=100ms*cores. v2 uses the "cpu.max"
			// file with the same (quota, period) semantics (microseconds).
			var coreNum int64
			for _, used := range res.CpuSet {
				if used != 0 {
					coreNum++
				}
			}
			var quota int64 = coreNum * 100 * 1000
			var period uint64 = 100 * 1000
			cgRes.CPU = &cgroup2.CPU{
				Max: cgroup2.NewCPUMax(&quota, &period),
			}
		}
	}
	logger.WithField("CPU", cgRes.CPU).Debug("cgroupv2 CPU setting")

	if _, err := cgroup2.NewManager(cgroupRoot, path, cgRes); err != nil {
		logrus.Fatal("Containerxv2: create cgroup failed, ", err)
	}
}

// DeleteCgroup deletes the v2 cgroup at the given path.
// Non-fatal on failure (matches pkg/containerx semantics).
func DeleteCgroup(path string) {
	mgr, err := cgroup2.Load(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return
		}
		logrus.Error("Containerxv2: load cgroup to delete failed, ", err)
		return
	}
	if err := mgr.Delete(); err != nil && !errors.Is(err, os.ErrNotExist) {
		logrus.Error("Containerxv2: delete cgroup failed, ", err)
	}
}
