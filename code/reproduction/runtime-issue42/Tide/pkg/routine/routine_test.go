package routine

import (
	"sync"
	"testing"
)

func TestCompletionCannotBeRevertedByLateReceiver(t *testing.T) {
	r := NewSubmittedRtn("r", "f", nil, &Resource{CpuNum: 2})
	r.Succ()
	r.SetReceiver(false, "s")
	if r.Status() != EXIT_SUCC {
		t.Fatal("late receiver overwrote terminal status")
	}
}

func TestReceiverAndCompletionConcurrent(t *testing.T) {
	for i := 0; i < 100; i++ {
		r := NewSubmittedRtn("r", "f", nil, &Resource{CpuNum: 2})
		var wg sync.WaitGroup
		wg.Add(2)
		go func() { defer wg.Done(); r.SetReceiver(false, "s") }()
		go func() { defer wg.Done(); r.Succ() }()
		wg.Wait()
		if r.Status() != EXIT_SUCC {
			t.Fatal("completion lost")
		}
	}
}
