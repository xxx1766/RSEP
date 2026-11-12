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

const cgroupRoot = "/sys/fs/cgroup"

func CreateCgroup(path string, res *processor.PhysicResource) {
	logger := logrus.WithFields(logrus.Fields{"CgroupPath": path})
	logger.WithField("HasResource", res != nil).Debug("Containerxv2: start creating cgroup")
	defer logger.Debug("Containerxv2: finish creating cgroup")

	cgRes := &cgroup2.Resources{}
	if res != nil && (clsstat.Singleton().GetSelf() == nil || clsstat.Singleton().GetSelf().Role != clsstat.THINGS) {
		if !dev.RSEPTest || dev.EnableCpuset {
			cpuIds := make([]string, 0, 1)
			for i, used := range res.CpuSet {
				if used != 0 {
					cpuIds = append(cpuIds, strconv.Itoa(i))
				}
			}
			cgRes.CPU = &cgroup2.CPU{Cpus: strings.Join(cpuIds, ",")}
		} else {
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
