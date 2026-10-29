package cloudhdlr

import (
	"github.com/ict/tide/pkg/event"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/routine"
	"google.golang.org/protobuf/proto"
	"testing"
)

type captureStub struct {
	interfaces.Stub
	got *routine.Routine
}

func (s *captureStub) Id() string                            { return "receiver" }
func (s *captureStub) CloudSubmitRoutine(r *routine.Routine) { s.got = r }
func TestC4WireMetadataReachesAdmission(t *testing.T) {
	r := routine.NewSubmittedRtn("request", "critical", nil, &routine.Resource{CpuNum: 3})
	r.RequireLocalCapacity = true
	r.LocalNodeID = "origin"
	r.InitSid = "source-stub"
	b, err := proto.Marshal(routine.SubmitEvent(r))
	if err != nil {
		t.Fatal(err)
	}
	s := &captureStub{}
	rtnSbmHdlr(s)(&event.Event{Content: b})
	if s.got == nil || !s.got.RequireLocalCapacity || s.got.LocalNodeID != "origin" || s.got.ResReq.CpuNum != 3 || s.got.InitSid != "source-stub" {
		t.Fatalf("lost admission metadata: %+v", s.got)
	}
}
