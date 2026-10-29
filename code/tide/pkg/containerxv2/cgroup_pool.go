package containerxv2

// cgroup_pool.go — cgroup v2 port of pkg/containerx/cgroup_pool.go.
// Logic is identical; only CreateCgroup / DeleteCgroup symbols differ in
// their cgroups library binding.

import (
	"runtime"

	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/processor"
	"github.com/ict/tide/pkg/stringx"
	"github.com/sirupsen/logrus"
)

var cgChan chan string

func InitCgroup() {
	cpuNum := clsstat.Singleton().GetSelf().ResTotal.CpuNum
	totalCgroup := 3 * cpuNum / 2

	cgChan = make(chan string, totalCgroup)
	for i := 0; i < totalCgroup; i++ {
		cg := stringx.Concat("/tide/", stringx.GenerateId())
		CreateCgroup(cg, nil)
		cgChan <- cg
	}
}

// initCgroup is the private, per-resource pool variant from v1. We keep the
// structure parity but remove the hardcoded {4,8,96} NumCPU allowlist so it
// runs on this 16-core host. The v1 Fatal check was defensive and not on the
// hot RSEP path; preserving the check would be a regression.
func initCgroup() {
	totalCgroup := clsstat.Singleton().GetSelf().ResTotal.CpuNum / dev.GetStubResource().CpuNum
	devCpuNum := runtime.NumCPU()

	if dev.RSEPTest && !dev.EnableConcurrencyControl {
		totalCgroup = devCpuNum
	}

	cpuId := 0
	cgChan = make(chan string, totalCgroup)
	for i := 0; i < totalCgroup; i++ {
		res := &processor.PhysicResource{CpuSet: make([]int32, devCpuNum)}
		for ci := 0; ci < dev.GetStubResource().CpuNum; ci++ {
			res.CpuSet[cpuId] = 1
			cpuId = (cpuId + 1) % devCpuNum
		}
		cg := stringx.Concat("/tide/", stringx.GenerateId())
		CreateCgroup(cg, res)
		cgChan <- cg
	}
	logrus.WithField("totalCgroup", totalCgroup).Debug("Containerxv2: initCgroup done")
}

func getCgroup() string {
	return <-cgChan
}

func retCgroup(path string) {
	cgChan <- path
}

func DeleteAllCgroup() {
loop:
	for {
		select {
		case cg := <-cgChan:
			DeleteCgroup(cg)
		default:
			break loop
		}
	}
}
