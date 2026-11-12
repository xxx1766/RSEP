package clsstat

import (
	"testing"
	"time"
)

func TestUpdateStubIgnoresUnknownSyncWithoutChangingFirstStub(t *testing.T) {
	initial := time.Unix(100, 0)
	first := &StubState{Id: "edge-stub", Status: STUB_RUNNING, UpdateTime: initial, estWaitTime: 17}
	cs := &ClusterState{stubs: []*StubState{first}, sid2idx: map[string]int{"edge-stub": 0}}

	cs.UpdateStub("cloud-stub", SyncEstWaitTime(9000, time.Unix(200, 0)))
	if first.estWaitTime != 17 || !first.UpdateTime.Equal(initial) {
		t.Fatalf("unknown sync changed first stub: wait=%d updated=%v", first.estWaitTime, first.UpdateTime)
	}

	updated := time.Unix(300, 0)
	cs.UpdateStub("edge-stub", SyncEstWaitTime(33, updated))
	if first.estWaitTime != 33 || !first.UpdateTime.Equal(updated) {
		t.Fatalf("known sync not applied: wait=%d updated=%v", first.estWaitTime, first.UpdateTime)
	}
}

func TestUpdateStubUnknownOnEmptyInventory(t *testing.T) {
	cs := &ClusterState{sid2idx: make(map[string]int)}
	cs.UpdateStub("absent", func(*StubState) { t.Fatal("unknown callback invoked") })
}
