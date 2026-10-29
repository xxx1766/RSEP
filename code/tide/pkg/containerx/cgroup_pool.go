package containerx

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
		// cgChan <- "/tide"
		cg := stringx.Concat("/tide/", stringx.GenerateId())
		CreateCgroup(cg, nil)
		cgChan <- cg
	}
}

func initCgroup() {
	// TODO:
	totalCgroup := clsstat.Singleton().GetSelf().ResTotal.CpuNum / dev.GetStubResource().CpuNum
	devCpuNum := runtime.NumCPU()
	if devCpuNum != 96 && devCpuNum != 8 && devCpuNum != 4 {
		logrus.Fatal("need access all CPU core")
	}

	// EXP
	if dev.RSEPTest {
		if !dev.EnableConcurrencyControl {
			totalCgroup = devCpuNum
		}
	}

	cpuId := 0
	cgChan = make(chan string, totalCgroup)
	// cgs := make([]string, 0)
	for i := 0; i < totalCgroup; i++ {
		res := &processor.PhysicResource{CpuSet: make([]int32, devCpuNum)}
		for ci := 0; ci < dev.GetStubResource().CpuNum; ci++ {
			res.CpuSet[cpuId] = 1
			cpuId = (cpuId + 1) % devCpuNum
		}

		// double core alloc
		// for ci := 0; ci < dev.GetStubResource().CpuNum/2; ci++ {
		// 	res.CpuSet[cpuId] = 1
		// 	res.CpuSet[cpuId+(devCpuNum/2)] = 1
		// 	cpuId = (cpuId + 1) % (devCpuNum / 2)
		// }

		cg := stringx.Concat("/tide/", stringx.GenerateId())
		CreateCgroup(cg, res)
		cgChan <- cg
		// cgs = append(cgs, cg)
	}

	// if  enableCpuset {
	// 	for _, cg := range cgs {
	// 		content := []byte("1")
	// 		if err := ioutil.WriteFile(stringx.Concat("/sys/fs/cgroup/cpuset", cg, "/cpuset.cpu_exclusive"), content, 0666); err != nil {
	// 			logrus.Fatal("set cpuset.cpu_exclusive failed, ", err)
	// 		}
	// 	}
	// }
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
