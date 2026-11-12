package containerxv2

import (
	"fmt"
	"os"
	"runtime"
	"strconv"
	"strings"

	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/processor"
	"github.com/ict/tide/pkg/stringx"
	"github.com/sirupsen/logrus"
)

var cgMap map[string]string

type combConfig struct {
	devCpuNum int
	cpuIds    []int
}

func defaultCombConfig() combConfig {
	cfg := combConfig{
		devCpuNum: runtime.NumCPU(),
		cpuIds:    []int{2, 3, 4, 5, 6, 7},
	}
	if v := os.Getenv("TIDE_COMB_DEV_CPUNUM"); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			cfg.devCpuNum = n
		}
	}
	if v := os.Getenv("TIDE_COMB_CPUIDS"); v != "" {
		parts := strings.Split(v, ",")
		ids := make([]int, 0, len(parts))
		for _, p := range parts {
			n, err := strconv.Atoi(strings.TrimSpace(p))
			if err != nil {
				logrus.Fatalf("Containerxv2: bad TIDE_COMB_CPUIDS entry %q: %v", p, err)
			}
			ids = append(ids, n)
		}
		cfg.cpuIds = ids
	}
	return cfg
}

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

func rsepInitCombCgroup() {
	cfg := defaultCombConfig()
	logrus.WithFields(logrus.Fields{
		"devCpuNum": cfg.devCpuNum,
		"cpuIds":    cfg.cpuIds,
	}).Info("Containerxv2: rsepInitCombCgroup")

	cpuNum := len(cfg.cpuIds)
	cgMap = make(map[string]string)
	for i := 0; i < cpuNum; i++ {
		for j := i + 1; j < cpuNum; j++ {
			res := &processor.PhysicResource{CpuSet: make([]int32, cfg.devCpuNum)}
			res.CpuSet[cfg.cpuIds[i]] = 1
			res.CpuSet[cfg.cpuIds[j]] = 1

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
