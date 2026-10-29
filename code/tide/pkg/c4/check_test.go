package c4

import (
	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/routine"
	"testing"
)

func TestCPUCapabilityEligibility(t *testing.T) {
	cs := clsstat.Singleton()
	for _, n := range []struct {
		id   string
		cpu  int
		role clsstat.NodeRole
	}{{"local", 2, clsstat.THINGS}, {"small", 1, clsstat.EDGE}, {"cloud", 8, clsstat.CLOUD}} {
		cs.AddNode(&clsstat.AddNodeOpts{Nid: n.id, ResTotal: &routine.Resource{CpuNum: n.cpu}, Role: n.role})
	}
	for _, tc := range []struct {
		name, anchor   string
		req            *routine.Resource
		critical, pass bool
	}{
		{"ordinary", "", nil, false, true}, {"equal total but no free CPU", "local", &routine.Resource{CpuNum: 2}, true, true},
		{"insufficient local", "small", &routine.Resource{CpuNum: 2}, true, false}, {"missing", "", &routine.Resource{CpuNum: 2}, true, false},
		{"cloud is not local", "cloud", &routine.Resource{CpuNum: 2}, true, false}, {"missing demand", "local", nil, true, false},
		{"GPU unsupported", "local", &routine.Resource{CpuNum: 1, GpuNum: 1}, true, false}, {"zero demand", "local", &routine.Resource{}, true, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := routine.NewSubmittedRtn("id", "cpu", nil, tc.req)
			r.RequireLocalCapacity = tc.critical
			r.LocalNodeID = tc.anchor
			if got := Check(r) == nil; got != tc.pass {
				t.Fatalf("eligible=%v want %v", got, tc.pass)
			}
		})
	}
}
