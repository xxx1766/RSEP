package procmgr

import (
	"context"
	"testing"
	"time"

	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/routine"
)

func TestSnapshotTracksReservationsBorrowingAndReturns(t *testing.T) {
	const appID = "threefig-snapshot-test"
	clsstat.Singleton().AddApp(&clsstat.AddAppOpts{AppId: appID})
	defer clsstat.Singleton().RmApp(appID)
	pm := NewProcMgr(&routine.Resource{CpuNum: 12})
	defer pm.Close()
	ctx, cancel := context.WithTimeout(context.WithValue(context.Background(), clsstat.ApplicationId("appid"), appID), time.Second)
	defer cancel()
	check := func(reserved, committed, borrowed, physical int) {
		t.Helper()
		s := pm.Snapshot()
		if s.TotalCPUs != 12 || s.ReservedCPUs != reserved || s.CommittedCPUs != committed || s.BorrowedCPUs != borrowed || s.PhysicalOccupiedCPUs != physical || s.ObservedNS <= 0 {
			t.Fatalf("unexpected snapshot %+v", s)
		}
	}
	check(0, 0, 0, 0)
	reserved, err := pm.Apply(ctx, &routine.Resource{CpuNum: 2})
	if err != nil || reserved == nil {
		t.Fatalf("Apply: %v", err)
	}
	check(2, 2, 0, 2)
	select {
	case borrowed := <-pm.Rob(ctx, &routine.Resource{CpuNum: 2}):
		if borrowed == nil {
			t.Fatal("Rob returned nil")
		}
		check(2, 4, 2, 4)
		// The exported observation is a value copy and cannot modify PM state.
		copy := pm.Snapshot()
		copy.ReservedCPUs = 99
		check(2, 4, 2, 4)
		pm.Return(borrowed, true)
	case <-ctx.Done():
		t.Fatal("Rob did not acquire spare CPUs")
	}
	check(2, 2, 0, 2)
	pm.Return(reserved, true)
	check(0, 0, 0, 0)
}
