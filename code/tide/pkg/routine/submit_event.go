package routine

import (
	"github.com/ict/tide/proto/commpb"
	"github.com/ict/tide/proto/eventpb"
)

func SubmitEvent(r *Routine) *eventpb.RoutineSubmitEvent {
	e := &eventpb.RoutineSubmitEvent{RtnId: r.Id, FnName: r.FnName, Args: r.Args, SubmitTime: r.SubmitTime, InitSid: r.InitSid, RequireLocalCapacity: r.RequireLocalCapacity, LocalNodeId: r.LocalNodeID}
	if r.ResReq != nil {
		e.ResourceReq = &commpb.Resource{CpuNum: int32(r.ResReq.CpuNum), GpuNum: int32(r.ResReq.GpuNum)}
	}
	return e
}

func FromSubmitEvent(e *eventpb.RoutineSubmitEvent, legacyRequirement *Resource) *Routine {
	var req *Resource
	if e.ResourceReq != nil {
		req = &Resource{CpuNum: int(e.ResourceReq.CpuNum), GpuNum: int(e.ResourceReq.GpuNum)}
	} else if !e.RequireLocalCapacity {
		req = legacyRequirement.Copy()
	}
	r := NewSubmittedRtn(e.RtnId, e.FnName, e.Args, req)
	r.SubmitTime = e.SubmitTime
	r.InitSid = e.InitSid
	r.RequireLocalCapacity = e.RequireLocalCapacity
	r.LocalNodeID = e.LocalNodeId
	return r
}
