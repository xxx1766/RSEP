package sched

import (
	"context"
	"encoding/json"
	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/cmnct"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/event"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/routine"
	"github.com/ict/tide/proto/eventpb"
	"google.golang.org/protobuf/proto"
	"testing"
	"time"
)

func TestC4RejectsExpansionWithoutLocalCPU(t *testing.T) {
	cs := clsstat.Singleton()
	cs.AddNode(&clsstat.AddNodeOpts{Nid: "c4-small", Role: clsstat.THINGS, ResTotal: &routine.Resource{CpuNum: 1}})
	target := &clsstat.NodeState{Id: "c4-remote", ResTotal: &routine.Resource{CpuNum: 8}, ResRemain: &routine.Resource{CpuNum: 8}, UpdateTime: time.Now()}
	r := routine.NewSubmittedRtn("c4", "cpu", nil, dev.GetReqResource())
	r.SubmitTime = time.Now().UnixNano()
	// JSON keeps this regression executable against the pre-C4 Routine type.
	if err := json.Unmarshal([]byte(`{"RequireLocalCapacity":true,"LocalNodeID":"c4-small"}`), r); err != nil {
		t.Fatal(err)
	}
	if got := defaultSortNode(r, []*clsstat.NodeState{target}); len(got) != 0 {
		t.Fatal("critical request with one local CPU was allowed to expand for a two-CPU task")
	}
}

func TestC4AllPathsRejectMissingAnchor(t *testing.T) {
	r := routine.NewSubmittedRtn("missing", "cpu", nil, dev.GetReqResource())
	r.RequireLocalCapacity = true
	r.SubmitTime = time.Now().Add(-time.Hour).UnixNano()
	sk := New("not-registered", "", nil)
	defer sk.Close()
	// No app or communicator is initialized: rejection must precede any send,
	// queue reservation or expansion, including the expired-deadline fast path.
	if ok, _ := sk.Schedule(context.Background(), r); ok {
		t.Fatal("scheduled invalid critical request")
	}
	if ok, _ := sk.trySubmitToCloud(context.Background(), r, clsstat.Singleton()); ok {
		t.Fatal("cloud fallback bypassed C4")
	}
	if err := sk.submitToStub(r, &clsstat.StubState{}); err == nil {
		t.Fatal("direct stub send bypassed C4")
	}
	if err := sk.requestNewStub(r, &clsstat.NodeState{}); err == nil {
		t.Fatal("direct expansion bypassed C4")
	}
	if got := defaultSortStub(r, []*clsstat.StubState{{}}); len(got) != 0 {
		t.Fatal("ranking bypassed C4")
	}
}

func TestC4CapableBusyAnchorAllowsRemoteAndPreservesDemand(t *testing.T) {
	cs := clsstat.Singleton()
	cs.AddNode(&clsstat.AddNodeOpts{Nid: "c4-capable", Role: clsstat.THINGS, ResTotal: &routine.Resource{CpuNum: 4}})
	cs.UpdateNode(&clsstat.UpdateNodeOpts{Nid: "c4-capable", ResRemain: &routine.Resource{}})
	target := cs.AddNode(&clsstat.AddNodeOpts{Nid: "c4-cloud", Address: "c4-loop", Role: clsstat.CLOUD, ResTotal: &routine.Resource{CpuNum: 8}})
	d := event.NewEventDispatcher()
	received := make(chan *routine.Routine, 2)
	d.RegisterHandler(interfaces.CldRtnSbmEvt("c4-app"), func(e *event.Event) {
		pb := &eventpb.RoutineSubmitEvent{}
		if err := proto.Unmarshal(e.Content, pb); err != nil {
			t.Error(err)
			return
		}
		received <- routine.FromSubmitEvent(pb, dev.GetReqResource())
	})
	cmnct.Init("c4-self", "c4-loop", d)
	r := routine.NewSubmittedRtn("valid", "cpu", nil, &routine.Resource{CpuNum: 3})
	r.RequireLocalCapacity = true
	r.LocalNodeID = "c4-capable"
	r.InitSid = "origin-stub"
	sk := New("c4-app", "", nil)
	defer sk.Close()
	if ok, id := sk.trySubmitToCloud(context.Background(), r, cs); !ok || id != target.Id {
		t.Fatal("capable but busy local node should not prohibit cloud execution")
	}
	select {
	case got := <-received:
		if !got.RequireLocalCapacity || got.LocalNodeID != r.LocalNodeID || got.ResReq.CpuNum != 3 {
			t.Fatalf("lost C4 or demand over cloud path: %+v", got)
		}
	case <-time.After(time.Second):
		t.Fatal("cloud send not received")
	}
}
