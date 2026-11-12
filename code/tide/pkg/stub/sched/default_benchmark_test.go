package sched

import (
	"fmt"
	"testing"
	"time"

	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/routine"
)

func BenchmarkRevisionRank(b *testing.B) {
	old := dev.CurrentConfig()
	defer dev.SetConfig(old)
	c := dev.DefaultConfig()
	c.ExecutionMS, c.QoSMS, c.StateMaxAgeMS = 22, 86400000, 86400000
	if err := dev.SetConfig(c); err != nil {
		b.Fatal(err)
	}
	for _, count := range []int{4, 16, 64, 256} {
		b.Run(fmt.Sprint(count), func(b *testing.B) {
			nodes := make([]*clsstat.NodeState, count)
			for i := range nodes {
				nodes[i] = &clsstat.NodeState{Id: fmt.Sprintf("n%03d", i), ResTotal: &routine.Resource{CpuNum: 8}, ResRemain: &routine.Resource{CpuNum: 2 + i%7}, UpdateTime: time.Now()}
			}
			r := routine.NewSubmittedRtn("bench", "cpu", nil, dev.GetReqResource())
			r.SubmitTime = time.Now().UnixNano()
			b.ReportAllocs()
			b.ResetTimer()
			for i := 0; i < b.N; i++ {
				if got := defaultSortNode(r, nodes); len(got) != count {
					b.Fatalf("eligible=%d want%d", len(got), count)
				}
			}
		})
	}
}
