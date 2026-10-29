package execpool

import (
	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/routine"
	"testing"
	"time"
)

type poolToken struct{ interfaces.Executor }

func TestAblationPoolQueuesAtDeclaredCapacity(t *testing.T) {
	oldRSEP, oldCC, oldEI := dev.RSEPTest, dev.EnableConcurrencyControl, dev.EnableContainerPerRoutine
	defer func() {
		dev.RSEPTest, dev.EnableConcurrencyControl, dev.EnableContainerPerRoutine = oldRSEP, oldCC, oldEI
	}()
	dev.RSEPTest, dev.EnableContainerPerRoutine = true, true
	clsstat.Init("pool-budget-test", "", clsstat.EDGE, &routine.Resource{CpuNum: 6})
	for _, cc := range []bool{false, true} {
		dev.EnableConcurrencyControl = cc
		cap := 6
		if cc {
			cap = 3
		}
		p := newStackPool("", "", nil, interfaces.StubFn).(*stackExecPool)
		if int(p.maxTotal) != cap {
			t.Fatalf("CC=%v cap=%d", cc, p.maxTotal)
		}
		p.noTotal = int32(cap)
		for i := 0; i < cap; i++ {
			p.idleStack.PushBack(&poolToken{})
		}
		var busy []interfaces.Executor
		for i := 0; i < cap; i++ {
			busy = append(busy, p.Get())
		}
		result := make(chan interfaces.Executor, 1)
		go func() { result <- p.Get() }()
		deadline := time.Now().Add(time.Second)
		for {
			p.mu.Lock()
			waiting, total := p.waiters.Len(), p.noTotal
			p.mu.Unlock()
			if total != int32(cap) {
				t.Fatalf("pool exceeded cap: %d", total)
			}
			if waiting == 1 {
				break
			}
			if time.Now().After(deadline) {
				t.Fatal("extra request did not queue")
			}
			time.Sleep(time.Millisecond)
		}
		p.Return(busy[0])
		select {
		case got := <-result:
			if got != busy[0] {
				t.Fatal("queued request did not reuse returned executor")
			}
			p.Return(got)
		case <-time.After(time.Second):
			t.Fatal("queued request did not wake")
		}
		for _, e := range busy[1:] {
			p.Return(e)
		}
		close(p.done)
	}
}
