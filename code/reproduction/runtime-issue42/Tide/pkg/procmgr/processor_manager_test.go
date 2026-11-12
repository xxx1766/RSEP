package procmgr

import (
	"context"
	"testing"
	"time"

	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/processor"
	"github.com/ict/tide/pkg/routine"
)

func TestQueuedAcquisitionPreservesResources(t *testing.T) {
	for _, registered := range []bool{false, true} {
		name := "missing-app"
		if registered {
			name = "registered-app"
		}
		t.Run(name, func(t *testing.T) {
			appID := "procmgr-test-" + name
			if registered {
				clsstat.Singleton().AddApp(&clsstat.AddAppOpts{AppId: appID})
				defer clsstat.Singleton().RmApp(appID)
			}
			pm := NewProcMgr(&routine.Resource{CpuNum: 2})
			ctx, cancel := context.WithTimeout(context.WithValue(context.Background(), clsstat.ApplicationId("appid"), appID), 2*time.Second)
			defer cancel()
			first, err := pm.Apply(ctx, &routine.Resource{CpuNum: 2})
			if err != nil || first == nil {
				t.Fatalf("first acquisition: %v", err)
			}
			result := make(chan *processor.Processor, 1)
			go func() { p, _ := pm.Apply(ctx, &routine.Resource{CpuNum: 2}); result <- p }()
			for {
				pm.mu.Lock()
				waiting := pm.waiters.Len() == 1
				pm.mu.Unlock()
				if waiting {
					break
				}
				select {
				case <-ctx.Done():
					t.Fatal("acquisition did not queue")
				default:
					time.Sleep(time.Millisecond)
				}
			}
			pm.Return(first, true)
			select {
			case p := <-result:
				if (p != nil) != registered {
					t.Fatalf("processor=%v, registered=%v", p, registered)
				}
				if p != nil {
					pm.Return(p, true)
				}
			case <-ctx.Done():
				t.Fatal("queued acquisition did not finish")
			}
			pm.mu.Lock()
			defer pm.mu.Unlock()
			if pm.logResRemain.CpuNum != 2 || pm.highPrioResRemain.CpuNum != 2 {
				t.Fatalf("CPU reservation leaked: remaining=%v, highPriority=%v", pm.logResRemain, pm.highPrioResRemain)
			}
			for _, used := range pm.physResRemain.CpuSet {
				if used != 0 {
					t.Fatalf("physical resource leaked: %v", pm.physResRemain)
				}
			}
		})
	}
}
