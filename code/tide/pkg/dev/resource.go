package dev

import "github.com/ict/tide/pkg/routine"

func GetReqResource() *routine.Resource {
	return &routine.Resource{CpuNum: 2, GpuNum: 0}
	// return &routine.Resource{CpuNum: 4, GpuNum: 0}
}

func GetStubResource() *routine.Resource {
	return &routine.Resource{CpuNum: 2, GpuNum: 0}
	// return &routine.Resource{CpuNum: 4, GpuNum: 0}

}
