package stub

import (
	"github.com/ict/tide/pkg/processor"
	"github.com/sirupsen/logrus"
)

// CC-off deliberately oversubscribes the six-CPU slice with six workers.
// Each worker keeps a two-CPU slot; two workers share each slot when all six
// are busy. Reusing bindProc for every worker incorrectly confined the entire
// C5 cell to one two-CPU slot (and C3 to a shared two-CPU quota).
func (s *stub) ablationProcForExecutor(execID string) *processor.Processor {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.ablationProcs == nil {
		s.ablationProcs = make(map[string]*processor.Processor)
		free := &processor.PhysicResource{CpuSet: make([]int32, len(s.bindProc.PhysicRes.CpuSet))}
		for i := 0; i < len(free.CpuSet)/s.resource.CpuNum; i++ {
			s.ablationSlots = append(s.ablationSlots, free.GetAndSubtract(s.resource))
		}
	}
	if p := s.ablationProcs[execID]; p != nil {
		return p
	}
	slot := s.ablationSlots[len(s.ablationProcs)%len(s.ablationSlots)]
	p := processor.NewProc("ablation-"+execID, s.resource.Copy(), slot, false)
	s.ablationProcs[execID] = p
	logrus.WithFields(logrus.Fields{"ExecutorId": execID, "PhysicRes": slot}).Info("ablation oversubscribed worker slot")
	return p
}
