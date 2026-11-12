package c4

import (
	"fmt"
	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/routine"
)

func Check(r *routine.Routine) error {
	if !r.RequireLocalCapacity {
		return nil
	}
	if r.ResReq == nil || r.ResReq.CpuNum <= 0 || r.ResReq.GpuNum != 0 {
		return fmt.Errorf("C4: only positive CPU-only requirements are supported")
	}
	n := clsstat.Singleton().GetNode(r.LocalNodeID)
	if n == nil || (n.Role != clsstat.THINGS && n.Role != clsstat.EDGE) {
		return fmt.Errorf("C4: local capability node is missing or is not a things/edge node")
	}
	total, _, _ := n.Capacity()
	if total.CpuNum < r.ResReq.CpuNum {
		return fmt.Errorf("C4: local CPU capacity %d is below requirement %d", total.CpuNum, r.ResReq.CpuNum)
	}
	return nil
}
