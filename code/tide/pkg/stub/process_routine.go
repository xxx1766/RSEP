package stub

import (
	"context"
	"sync/atomic"
	"time"

	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/processor"
	"github.com/ict/tide/pkg/routine"
	"github.com/sirupsen/logrus"
)

func (s *stub) processFnLoop() {
	logrus.WithFields(logrus.Fields{
		"StubId": s.id,
	}).Info("Stub: enter process loop")
	s.mu.Lock()
	if s.status != INITIALIZING {
		s.mu.Unlock()
		return
	}
	s.status = RUNNING
	s.lastActivity = time.Now()
	s.mu.Unlock()

	for {
		select {
		case <-s.done:
			return

		case r := <-s.rtnStore.Pop():
			// Run dispatch in parallel so multiple routines can be in
			// acquireProcessor / pool.Get concurrently. Without this, the
			// dispatch loop serializes on acquireProcessor and caps throughput
			// at ~1/T_request (≈3-4 RPS for our 430 ms heavy workload), even
			// when 6 long-lived executors are available. procM and the
			// executor pool both have proper internal locking so concurrent
			// callers are safe; queueing of contended routines is handled by
			// procM's waiter list.
			go s.processOne(r)
		}
	}
}

func (s *stub) processOne(r *routine.Routine) {
	logger := logrus.WithFields(logrus.Fields{
		"StubId":    s.id,
		"RoutineId": r.Id,
	})
	logger.Debug("Stub: start to process this routine, and acquire a proc")

	proc := s.acquireProcessor(r)
	if proc == nil { // stub is closed
		s.finishRoutine()
		return
	}
	// logger.WithField("ProcessorId", proc.Id).Debug("Stub: got a processor")
	logger.WithFields(logrus.Fields{
		"ProcessorId":      proc.Id,
		"logic resource":   proc.LogicRes,
		"physics resource": proc.PhysicRes,
	}).Debug("Stub: got a processor")
	go s.executeRoutine(proc, r)
}

func (s *stub) acquireProcessor(r *routine.Routine) (proc *processor.Processor) {
	// A ready processor channel can win select even when done is closed.
	// Serialize the handoff with Close, including blocked acquisitions that
	// wake after shutdown. Close owns the bound processor's sole return.
	defer func() {
		if proc == nil {
			return
		}
		s.mu.Lock()
		stopped := s.status == STOPPING || s.status == STOPPED
		s.mu.Unlock()
		if stopped {
			if proc != s.bindProc {
				s.procM.Return(proc, true)
			}
			proc = nil
		}
	}()
	// EXP 如果不并发控制，则直接使用高优资源
	if dev.RSEPTest && !dev.EnableConcurrencyControl {
		return s.bindProc
	}

	select {
	case <-s.done:
		return nil

	case <-s.bindCh:
		return s.bindProc

	default: // 先尝试bindCh，没有再尝试ProcM抢用
		ctx, cancel := context.WithCancel(context.Background())
		ctx = context.WithValue(ctx, clsstat.ApplicationId("appid"), s.appId)
		defer cancel()
		select {
		case <-s.done:
			return nil

		case <-s.bindCh:
			return s.bindProc

		case proc := <-s.procM.Rob(ctx, r.ResReq):
			return proc
		}
	}
}

func (s *stub) executeRoutine(proc *processor.Processor, rtn *routine.Routine) {
	defer s.finishRoutine()

	logger := logrus.WithFields(logrus.Fields{
		"StubId":      s.id,
		"ProcessorId": proc.Id,
		"RoutineId":   rtn.Id,
	})

	logger.Debug("Stub: try to get executor")
	exec := s.execPool.Get()
	if exec == nil { // executor pool exitd, the stub also exited
		s.returnProc(proc)
		return
	}
	// Close uses the same lock: checking the state and launching execution
	// form one transition, not a check followed by an unprotected Execute.
	s.startMu.Lock()
	s.mu.Lock()
	stopped := s.status == STOPPING || s.status == STOPPED
	s.mu.Unlock()
	if stopped {
		s.startMu.Unlock()
		// Executor acquisition can also outlive Close; do not start queued
		// work on a processor that shutdown has already released.
		s.execPool.Return(exec)
		s.returnProc(proc)
		return
	}
	logger = logger.WithField("ExecutorId", exec.Id())
	logger.Debug("Stub: got a executor")
	if dev.RSEPTest && !dev.EnableConcurrencyControl {
		proc = s.ablationProcForExecutor(exec.Id())
	}

	var executeErr error
	// TODO: need handle the routine
	if err := exec.BindProc(proc); err != nil {
		logrus.WithFields(logrus.Fields{
			"StubId":     s.id,
			"ExecutorId": exec.Id(),
		}).Error("Stub: failed to bind processor to executor, ", err)
		goto onerr
	}
	if err := exec.BindRtn(rtn); err != nil {
		logrus.WithFields(logrus.Fields{
			"StubId":     s.id,
			"ExecutorId": exec.Id(),
		}).Error("Stub: failed to bind routine to executor, ", err)
		goto onerr
	}

	s.mu.Lock()
	if proc == s.bindProc {
		s.rtnOnBindProc = rtn
		s.startTime = time.Now()
	}
	// 队列长度减小，队列中等待执行执行的预估等待时间发生改变
	s.accExecTimeInQue -= int64(dev.GetEstExecTime(clsstat.Singleton().GetSelf().Id))
	go s.noticeStubUpdate(s.estWaitTime(), time.Now())
	s.mu.Unlock()

	logger.Debug("Stub: notice executor to execute")
	executeErr = exec.Execute()
	s.startMu.Unlock()
	if executeErr != nil {
		s.postProcess(exec)
		logrus.Fatal(executeErr)
	}
	s.listenEvt(exec)
	s.postProcess(exec)
	return

onerr:
	s.startMu.Unlock()
	s.returnProc(proc)
}

func (s *stub) finishRoutine() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.lastActivity = time.Now()
	atomic.AddInt64(&s.noExecuting, -1)
}

func (s *stub) postProcess(exec interfaces.Executor) error {
	logger := logrus.WithFields(logrus.Fields{
		"StubId":     s.id,
		"ExecutorId": exec.Id(),
	})
	logger.Debug("Stub: start post process")

	// unbind routine
	rtn, err := exec.UnbindRtn()
	if err != nil {
		logger.Fatal("Stub: failed to unbind routine, ", err)
	}
	logger = logger.WithField("RoutineId", rtn.Id)
	logger.Debug("Stub: finish processing one routine")
	defer logger.Debug("Stub: finish post process")

	// unbind processor
	proc, err := exec.UnbindProc()
	if err != nil {
		logger.Error("Stub: failed to unbind processor, ", err)
		return err
	}

	// 必须先还Executor，再还Processor
	// 如果先还Processor，Stub获取后又申请新的Executor，而这里有一个空闲的Executor尚未归还，导致创建新的Executor
	// 但是会导致Proc有一个空闲时间
	s.execPool.Return(exec)
	s.returnProc(proc)
	return nil
}

func (s *stub) returnProc(proc *processor.Processor) {
	// EXP
	if dev.RSEPTest && !dev.EnableConcurrencyControl {
		return
	}

	if s.bindProc != proc {
		s.procM.Return(proc, true)

	} else {
		s.mu.Lock()
		s.startTime = time.Time{}
		s.rtnOnBindProc = nil
		go s.noticeStubUpdate(s.estWaitTime(), time.Now())
		s.mu.Unlock()

		s.bindCh <- struct{}{}
	}
}
