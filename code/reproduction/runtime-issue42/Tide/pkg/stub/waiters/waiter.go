package waiters

import (
	"sync"
	"sync/atomic"

	"github.com/ict/tide/pkg/interfaces"
)

type WaiterStore interface {
	Push(w *Waiter)
	MarkDone(rid string, withData bool) []*Waiter
}

type waiterStore struct {
	ref2waiters map[string]*sync.Map
	mu          sync.RWMutex
}

func NewStore() WaiterStore {
	return &waiterStore{
		ref2waiters: make(map[string]*sync.Map),
	}
}

type Type int

type Waiter struct {
	RefIds   []string
	Exec     interfaces.Executor
	WithData bool

	noDone uint64
}

func NewWaiter(refIds []string, exec interfaces.Executor, withData bool) *Waiter {
	return &Waiter{RefIds: refIds, Exec: exec, WithData: withData}
}

func (s *waiterStore) Push(w *Waiter) {
	for _, refId := range w.RefIds {
		waiters, ok := s.ref2waiters[refId]
		if !ok {
			waiters = &sync.Map{}

			s.mu.Lock()
			s.ref2waiters[refId] = waiters
			s.mu.Unlock()
		}
		waiters.Store(w.Exec.Id(), w)
	}
}

func (s *waiterStore) MarkDone(refId string, withData bool) []*Waiter {
	s.mu.RLock()
	waiters := s.ref2waiters[refId]
	s.mu.RUnlock()

	result := make([]*Waiter, 0)
	waiters.Range(func(key, value any) bool {
		w := value.(*Waiter)
		if w.WithData && !withData {
			return true
		}

		ret := atomic.AddUint64(&w.noDone, 1)
		if ret == uint64(len(w.RefIds)) {
			result = append(result, w)
			waiters.Delete(key)
		}
		return true
	})
	return result
}
