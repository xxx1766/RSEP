package sched

import (
	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/routine"
	"testing"
	"time"
)

func TestExpansionRequiresActualQuotaAndAcceptsCPUOnly(t *testing.T) {
	nodes := []*clsstat.NodeState{
		{Id: "equal", ResTotal: &routine.Resource{CpuNum: 2}, ResRemain: &routine.Resource{CpuNum: 2}},
		{Id: "too-small", ResTotal: &routine.Resource{CpuNum: 4, GpuNum: 4}, ResRemain: &routine.Resource{CpuNum: 1, GpuNum: 4}},
		{Id: "empty", ResTotal: &routine.Resource{}, ResRemain: &routine.Resource{}},
		{Id: "room", ResTotal: &routine.Resource{CpuNum: 8}, ResRemain: &routine.Resource{CpuNum: 6}},
	}
	r := routine.NewSubmittedRtn("r", "cpu", nil, &routine.Resource{CpuNum: 2})
	got := cpuBalanceSortNode(r, nodes)
	if len(got) != 2 || got[0].Id != "equal" || got[1].Id != "room" {
		ids := []string{}
		for _, n := range got {
			ids = append(ids, n.Id)
		}
		t.Fatalf("eligible nodes=%v, want equal then room", ids)
	}
}

func TestWeightedCandidatesFilterBeforeRanking(t *testing.T) {
	old := dev.CurrentConfig()
	t.Cleanup(func() { _ = dev.SetConfig(old) })
	c := dev.DefaultConfig()
	c.ExecutionMS = 10
	c.QoSMS = 100
	c.Weights = [3]float64{0, 1, 0}
	c.NetworkMS = map[string]int{"slow": 60, "fast": 1, "infeasible": 200}
	_ = dev.SetConfig(c)
	cs := clsstat.Singleton()
	clsstat.Init("ranking-self", "", clsstat.EDGE, &routine.Resource{CpuNum: 4})
	nodes := []*clsstat.NodeState{}
	for _, id := range []string{"slow", "fast", "infeasible"} {
		n := cs.AddNode(&clsstat.AddNodeOpts{Nid: id, Role: clsstat.EDGE, ResTotal: &routine.Resource{CpuNum: 4}})
		cs.UpdateNode(&clsstat.UpdateNodeOpts{Nid: id, ResRemain: &routine.Resource{CpuNum: 2}})
		nodes = append(nodes, n)
	}
	r := routine.NewSubmittedRtn("rank", "cpu", nil, dev.GetReqResource())
	r.SubmitTime = time.Now().UnixNano()
	got := defaultSortNode(r, nodes)
	if len(got) != 2 || got[0].Id != "fast" {
		t.Fatalf("node candidates=%v want fast,slow; infeasible excluded", got)
	}
	stubs := []*clsstat.StubState{}
	for i, id := range []string{"slow", "fast", "infeasible"} {
		stubs = append(stubs, &clsstat.StubState{Id: id, NodeId: id, Mode: interfaces.StubFn, Resource: dev.GetStubResource(), UpdateTime: time.Now(), CreateTime: time.Now().Add(time.Duration(i) * time.Second)})
	}
	ss := defaultSortStub(r, stubs)
	if len(ss) != 2 || ss[0].Id != "fast" {
		t.Fatalf("stub candidates=%v want fast,slow", ss)
	}
	if stubs[0].Id != "slow" {
		t.Fatal("ranking mutated shared candidate list")
	}
}

func TestInvalidCapacityAndStaleNodeNeverSelected(t *testing.T) {
	r := routine.NewSubmittedRtn("r", "cpu", nil, dev.GetReqResource())
	r.SubmitTime = time.Now().UnixNano()
	nodes := []*clsstat.NodeState{
		{Id: "invalid", ResTotal: &routine.Resource{CpuNum: 2}, ResRemain: &routine.Resource{CpuNum: 3}, UpdateTime: time.Now()},
		{Id: "stale", ResTotal: &routine.Resource{CpuNum: 2}, ResRemain: &routine.Resource{CpuNum: 2}, UpdateTime: time.Now().Add(-time.Minute)},
	}
	if got := defaultSortNode(r, nodes); len(got) != 0 {
		t.Fatalf("ineligible nodes returned: %v", got)
	}
}

func TestReservationRetainsNetworkBudgetAfterQueueGrowth(t *testing.T) {
	old := dev.CurrentConfig()
	t.Cleanup(func() { _ = dev.SetConfig(old) })
	c := dev.DefaultConfig()
	c.ExecutionMS = 100
	c.NetworkMS = map[string]int{"reservation-node": 250}
	_ = dev.SetConfig(c)
	cs := clsstat.Singleton()
	cs.AddStub(&clsstat.AddStubOpts{StubId: "reservation-stub", NodeId: "reservation-node", Mode: interfaces.StubFn})
	st := cs.GetStub("reservation-stub")
	deadline := time.Now().Add(400 * time.Millisecond).UnixNano()
	if !cs.TryUpdateEstWaitTime(st.Id, uint64(100*time.Millisecond), uint64(deadline)) {
		t.Fatal("fixture reservation failed")
	}
	if reserveStub(st, deadline) {
		t.Fatal("queue+execution+network exceeds deadline after queue growth")
	}
}
