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
		cg := stringx.Concat("/tide/", stringx.GenerateId())
		CreateCgroup(cg, nil)
		cgChan <- cg
	}
}

func initCgroup() {
	totalCgroup := clsstat.Singleton().GetSelf().ResTotal.CpuNum / dev.GetStubResource().CpuNum
	devCpuNum := runtime.NumCPU()
	if devCpuNum != 96 && devCpuNum != 8 && devCpuNum != 4 {
		logrus.Fatal("need access all CPU core")
	}

	if dev.RSEPTest {
		if !dev.EnableConcurrencyControl {
			totalCgroup = devCpuNum
		}
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
