package routine

import (
	"github.com/ict/tide/proto/eventpb"
	"google.golang.org/protobuf/proto"
	"testing"
)

func TestC4SubmitEventRoundTrip(t *testing.T) {
	r := NewSubmittedRtn("id", "cpu", []byte("args"), &Resource{CpuNum: 3})
	r.RequireLocalCapacity = true
	r.LocalNodeID = "origin"
	r.InitSid = "stub"
	r.SubmitTime = 42
	b, err := proto.Marshal(SubmitEvent(r))
	if err != nil {
		t.Fatal(err)
	}
	e := &eventpb.RoutineSubmitEvent{}
	if err = proto.Unmarshal(b, e); err != nil {
		t.Fatal(err)
	}
	got := FromSubmitEvent(e, &Resource{CpuNum: 2})
	if !got.RequireLocalCapacity || got.LocalNodeID != "origin" || got.ResReq.CpuNum != 3 || got.InitSid != "stub" || got.SubmitTime != 42 || string(got.Args) != "args" {
		t.Fatalf("lost request metadata: %+v", got)
	}
}
func TestC4MissingWireRequirementCannotUseLegacyDefault(t *testing.T) {
	got := FromSubmitEvent(&eventpb.RoutineSubmitEvent{RequireLocalCapacity: true}, &Resource{CpuNum: 2})
	if got.ResReq != nil {
		t.Fatal("critical request silently acquired a default requirement")
	}
	old := FromSubmitEvent(&eventpb.RoutineSubmitEvent{}, &Resource{CpuNum: 2})
	if old.ResReq == nil || old.ResReq.CpuNum != 2 || old.RequireLocalCapacity {
		t.Fatal("ordinary legacy event changed")
	}
}
