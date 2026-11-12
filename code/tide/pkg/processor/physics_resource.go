package processor

import (
	"sync/atomic"

	"github.com/ict/tide/pkg/routine"
	"github.com/sirupsen/logrus"
)

type PhysicResource struct {
	CpuSet []int32
	cpuOrd []int
}

var EmptyResourse *PhysicResource = &PhysicResource{
	CpuSet: make([]int32, 0),
}

func (r *PhysicResource) GetAndSubtract(req *routine.Resource) *PhysicResource {
	if r.cpuOrd == nil {
		r.cpuOrd = make([]int, len(r.CpuSet))
		for i := 0; i < len(r.cpuOrd); i += 4 {
			if i+2 >= len(r.cpuOrd) {
				r.cpuOrd[i], r.cpuOrd[i+1] = i, i+1
				break
			}

			r.cpuOrd[i], r.cpuOrd[i+1] = i, i+2
			r.cpuOrd[i+2], r.cpuOrd[i+3] = i+1, i+3
		}
	}

	retRes := &PhysicResource{CpuSet: make([]int32, len(r.CpuSet))}

	cnt := 0
	for i := range r.CpuSet {
		coreIdx := r.cpuOrd[i]
		used := r.CpuSet[coreIdx]

		if used == 1 {
			continue
		}
		if atomic.CompareAndSwapInt32(&r.CpuSet[coreIdx], 0, 1) {
			retRes.CpuSet[coreIdx] = 1
			cnt++
			if cnt == req.CpuNum {
				break
			}
		}
	}
	if cnt < req.CpuNum {
		logrus.Fatal("cpu is not enough")
	}
	return retRes
}

func (r *PhysicResource) Add(delta *PhysicResource) {
	for i, used := range delta.CpuSet {
		if used == 0 {
			continue
		}
		r.CpuSet[i] = 0
	}
}
