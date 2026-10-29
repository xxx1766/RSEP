package containerx

/*
边缘侧设备创建两核所有组合的Cgroup
*/

import (
	"fmt"
	"runtime"
	"strconv"

	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/processor"
	"github.com/ict/tide/pkg/stringx"
)

var cgMap map[string]string

func InitCombCgroup() {
	if dev.RSEPTest {
		rsepInitCombCgroup()
		return
	}

	devCpuNum := runtime.NumCPU()
	cpuNum := clsstat.Singleton().GetSelf().ResTotal.CpuNum
	cgMap = make(map[string]string)
	for i := 0; i < cpuNum; i++ {
		for j := i + 1; j < cpuNum; j++ {
			res := &processor.PhysicResource{CpuSet: make([]int32, devCpuNum)}
			res.CpuSet[i] = 1
			res.CpuSet[j] = 1

			cg := stringx.Concat("/tide/", stringx.GenerateId())
			CreateCgroup(cg, res)
			cgMap[fmt.Sprintf("%d%d", i, j)] = cg
		}
	}
}

// TODO： 核心可配置
func rsepInitCombCgroup() {
	devCpuNum := 8
	cpuNum := 6
	cpuIds := []int{0, 1, 2, 4, 5, 6}

	cgMap = make(map[string]string)
	for i := 0; i < cpuNum; i++ {
		for j := i + 1; j < cpuNum; j++ {
			res := &processor.PhysicResource{CpuSet: make([]int32, devCpuNum)}
			res.CpuSet[cpuIds[i]] = 1
			res.CpuSet[cpuIds[j]] = 1

			cg := stringx.Concat("/tide/", stringx.GenerateId())
			CreateCgroup(cg, res)
			cgMap[fmt.Sprintf("%d%d", i, j)] = cg
		}
	}
}

func getCombCgroup(res *processor.PhysicResource) string {
	key := ""
	for id, used := range res.CpuSet {
		if used != 0 {
			key += strconv.Itoa(id)
		}
	}
	return cgMap[key]
}

func DeleteAllCombCgroup() {
	for _, v := range cgMap {
		DeleteCgroup(v)
	}
}
