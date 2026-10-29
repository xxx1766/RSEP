package resource

import (
	"sync/atomic"

	"github.com/ict/tide/pkg/routine"
	"github.com/sirupsen/logrus"
)

type Physic struct {
	CpuSet []int32
	GpuSet []int32
}

var EmptyPhysic *Physic = &Physic{
	CpuSet: make([]int32, 0),
	GpuSet: make([]int32, 0),
}

func (r *Physic) GetAndSubtract(req *routine.Resource) *Physic {
	retRes := &Physic{CpuSet: make([]int32, len(r.CpuSet)), GpuSet: make([]int32, len(r.GpuSet))}

	cnt := 0
	for i, used := range r.CpuSet {
		if used == 1 {
			continue
		}
		if atomic.CompareAndSwapInt32(&r.CpuSet[i], 0, 1) {
			retRes.CpuSet[i] = 1
			cnt++
			if cnt == req.CpuNum {
				break
			}
		}
	}
	if cnt < req.CpuNum {
		logrus.Fatal("cpu is not enough")
	}

	cnt = 0
	for i, used := range r.GpuSet {
		if used == 1 {
			continue
		}
		if atomic.CompareAndSwapInt32(&r.GpuSet[i], 0, 1) {
			retRes.GpuSet[i] = 1
			cnt++
			if cnt == req.GpuNum {
				break
			}
		}
	}
	if cnt < req.GpuNum {
		logrus.Fatal("gpu is not enough")
	}

	return retRes
}

func (r *Physic) Add(delta *Physic) {
	for i, used := range delta.CpuSet {
		if used == 0 {
			continue
		}
		r.CpuSet[i] = 0
	}

	for i, used := range delta.GpuSet {
		if used == 0 {
			continue
		}
		r.GpuSet[i] = 0
	}
}
