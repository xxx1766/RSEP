package waiters

import (
	"fmt"
	"sync"
	"testing"

	"github.com/ict/tide/pkg/interfaces"
)

type testExecutor struct {
	interfaces.Executor
	id string
}

func (e testExecutor) Id() string { return e.id }

func TestConcurrentPushAndMarkDone(t *testing.T) {
	store := NewStore()
	const requests = 1000
	var group sync.WaitGroup
	for i := 0; i < requests; i++ {
		group.Add(1)
		go func(i int) {
			defer group.Done()
			id := fmt.Sprintf("request-%d", i)
			store.Push(NewWaiter([]string{id}, testExecutor{id: id}, true))
			if got := store.MarkDone(id, true); len(got) != 1 {
				t.Errorf("%s completed %d waiters, want one", id, len(got))
			}
		}(i)
	}
	group.Wait()
	if got := store.MarkDone("unknown", true); len(got) != 0 {
		t.Fatalf("unknown request completed %d waiters", len(got))
	}
}
