package containerx

import (
	"strconv"
	"strings"

	"github.com/containerd/cgroups"
	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/processor"
	"github.com/opencontainers/runtime-spec/specs-go"
	"github.com/sirupsen/logrus"
)

// CreateCgroup creates a cgroup with the given path and resource.
func CreateCgroup(path string, res *processor.PhysicResource) {
	logger := logrus.WithFields(logrus.Fields{"CgroupPath": path})
	logger.WithField("HasResource", res != nil).Debug("Containerx: start creating cgroup")
	defer logger.Debug("Containerx: finish creating cgroup")

	// do not limit resource usage on things
	spec := &specs.LinuxResources{}
	if res != nil && (clsstat.Singleton().GetSelf() == nil || clsstat.Singleton().GetSelf().Role != clsstat.THINGS) {
		if !dev.RSEPTest || dev.EnableCpuset {
			cpuIds := make([]string, 0, 1)
			for i, used := range res.CpuSet {
				if used != 0 {
					cpuIds = append(cpuIds, strconv.Itoa(i))
				}
			}
			cpus := strings.Join(cpuIds, ",")
			spec.CPU = &specs.LinuxCPU{Cpus: cpus}

		} else {
			var coreNum int64 = 0
			for _, used := range res.CpuSet {
				if used != 0 {
					coreNum++
				}
			}
			var quota int64 = coreNum * 100 * 1000
			var period uint64 = 1 * 100 * 1000
			spec.CPU = &specs.LinuxCPU{Quota: &quota, Period: &period}
		}
	}
	logger.WithField("CPUs", spec.CPU).Debug("cpuset.cpus setting")

	_, err := cgroups.New(cgroups.V1, cgroups.StaticPath(path), spec)
	if err != nil {
		logrus.Fatal("Containerx: create cgroup failed, ", err)
	}
}

// DeleteCgroup deletes the cgroup with the given path.
func DeleteCgroup(path string) {
	control, err := cgroups.Load(cgroups.V1, cgroups.StaticPath(path))
	if err != nil {
		logrus.Error("Containerx: failed to delete cgroup, ", err)
		return
	}
	control.Delete()
}
