package stub

import (
	"fmt"
	"github.com/ict/tide/pkg/processor"
	"github.com/ict/tide/pkg/routine"
	"testing"
)

func TestAblationOversubscriptionUsesWholeSlice(t *testing.T) {
	s := &stub{resource: &routine.Resource{CpuNum: 2}, bindProc: &processor.Processor{PhysicRes: &processor.PhysicResource{CpuSet: make([]int32, 6)}}}
	counts := make([]int, 6)
	for i := 0; i < 6; i++ {
		id := fmt.Sprint(i)
		p := s.ablationProcForExecutor(id)
		if s.ablationProcForExecutor(id) != p {
			t.Fatal("executor placement changed")
		}
		cores := 0
		for cpu, used := range p.PhysicRes.CpuSet {
			if used != 0 {
				counts[cpu]++
				cores++
			}
		}
		if cores != 2 {
			t.Fatalf("worker has %d CPUs", cores)
		}
	}
	for _, n := range counts {
		if n != 2 {
			t.Fatalf("six workers do not share the six CPUs evenly: %v", counts)
		}
	}
}
