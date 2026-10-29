package procmgr

import (
	"container/list"
	"context"
	"sync"

	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/processor"
	"github.com/ict/tide/pkg/routine"
	"github.com/ict/tide/pkg/stringx"
	"github.com/pkg/errors"
	"github.com/sirupsen/logrus"
)

// ProcessorManager manages the processors.
// STAR
// 模仿信号量，利用列表前后顺序，实现Apply高优占用，Rob低优抢用
// NOTE: 这里可以用优先级进行排序，进一步加强优先化
type ProcessorManager struct {
	logResTotal       *routine.Resource
	logResRemain      *routine.Resource
	highPrioResRemain *routine.Resource
	physResRemain     *processor.PhysicResource

	mu      sync.Mutex
	waiters list.List

	done chan struct{}
}

type waiter struct {
	req   *routine.Resource
	ready chan<- struct{}
}

// for transmitting ProcessorId
type procIdKey string

// NewProcMgr creates a new ProcessorManager.
func NewProcMgr(selfRes *routine.Resource) *ProcessorManager {
	return &ProcessorManager{
		logResTotal:       selfRes.Copy(),
		logResRemain:      selfRes.Copy(),
		highPrioResRemain: selfRes.Copy(),
		physResRemain:     &processor.PhysicResource{CpuSet: make([]int32, selfRes.CpuNum)},

		done: make(chan struct{}),
	}
}

// Close closes the ProcessorManager.
func (pm *ProcessorManager) Close() error {
	close(pm.done)
	return nil
}

// Apply applies a processor.
func (pm *ProcessorManager) Apply(ctx context.Context, req *routine.Resource) (*processor.Processor, error) {
	if !pm.logResTotal.GreaterOrEqual(req) {
		return nil, errors.New("no enough resource")
	}
	id := stringx.GenerateId()
	logger := logrus.WithFields(logrus.Fields{
		"ProcessorId": id,
		"AllocRes":    req,
		"RemainRes":   pm.logResRemain,
	})
	logger.Debug("ProcMgr: start applying")
	defer logger.Debug("ProcMgr: finish applying")

	ctx = context.WithValue(ctx, procIdKey("id"), id)
	return pm.acquire(ctx, req, true), nil
}

// Rob robs a processor.
func (pm *ProcessorManager) Rob(ctx context.Context, req *routine.Resource) <-chan *processor.Processor {
	id := stringx.GenerateId()
	logger := logrus.WithFields(logrus.Fields{
		"ProcessorId": id,
		"AllocRes":    req,
		"RemainRes":   pm.logResRemain,
	})
	logger.Debug("ProcMgr: start robbing")

	ch := make(chan *processor.Processor)
	go func() {
		select {
		case <-ctx.Done():
			logger.Debug("ProcMgr: finish robbing, give up before acquiring")
			return
		default:
		}

		ctx = context.WithValue(ctx, procIdKey("id"), id)
		proc := pm.acquire(ctx, req, false)
		if proc == nil {
			logger.Debug("ProcMgr: finish robbing, give up while acquiring")
			return
		}

		select {
		case <-ctx.Done():
			logger.Debug("ProcMgr: finish robbing, give up after acquiring")
			pm.Return(proc, false)
		case ch <- proc:
			logger.Debug("ProcMgr: finish robbing, successfully acquiring")
		}
	}()
	return ch
}

func (pm *ProcessorManager) acquire(ctx context.Context, req *routine.Resource, highPriority bool) *processor.Processor {
	id := ctx.Value(procIdKey("id")).(string)
	AppId := ctx.Value(clsstat.ApplicationId("appid")).(string)
	pm.mu.Lock()
	if pm.logResRemain.GreaterOrEqual(req) && (highPriority || pm.waiters.Len() == 0) {
		pm.logResRemain.Subtract(req)
		physRes := pm.physResRemain.GetAndSubtract(req)
		if highPriority {
			pm.highPrioResRemain.Subtract(req)
		}
		pm.mu.Unlock()
		return processor.NewProc(id, req, physRes, highPriority)
	}

	ready := make(chan struct{})
	waiter := &waiter{req: req, ready: ready}
	var elem *list.Element
	if highPriority { // 高优占用，优先满足
		elem = pm.waiters.PushFront(waiter)
	} else {
		elem = pm.waiters.PushBack(waiter)
	}
	pm.mu.Unlock()

	select {
	case <-ctx.Done():
		pm.mu.Lock()
		select {
		case <-ready:
			pm.returnLogRes(req)
		default:
			isFront := pm.waiters.Front() == elem
			pm.waiters.Remove(elem)
			if isFront && pm.logResRemain.Greater(routine.EmptyResourse) {
				pm.notifyWaiters()
			}
		}
		pm.mu.Unlock()
		return nil

	case <-ready:
		cs := clsstat.Singleton()
		as := cs.GetApp(AppId)
		pm.mu.Lock()
		defer pm.mu.Unlock()
		if as == nil {
			// notifyWaiters already reserved logical capacity. If the
			// application disappeared, return that reservation before leaving.
			pm.returnLogRes(req)
			return nil
		}
		physRes := pm.physResRemain.GetAndSubtract(req)
		if highPriority {
			pm.highPrioResRemain.Subtract(req)
		}
		return processor.NewProc(id, req, physRes, highPriority)
	}
}

// Return returns a processor.
func (pm *ProcessorManager) Return(proc *processor.Processor, used bool) {
	logger := logrus.WithFields(logrus.Fields{
		"ProcessorId": proc.Id,
		"ReturnRes":   proc.LogicRes,
		"RemainRes":   pm.logResRemain,
		"Used":        used,
	})
	logger.Debug("ProcMgr: start withdrawing a processor")
	defer logger.Debug("ProcMgr: finish withdrawing a processor")

	pm.mu.Lock()
	defer pm.mu.Unlock()
	if proc.HighPriority {
		pm.highPrioResRemain.Add(proc.LogicRes)
	}
	pm.physResRemain.Add(proc.PhysicRes)
	pm.returnLogRes(proc.LogicRes)
}

func (pm *ProcessorManager) returnLogRes(res *routine.Resource) {
	pm.logResRemain.Add(res)
	if pm.logResRemain.Greater(pm.logResTotal) {
		logrus.Fatal("ProcMgr: returned more than held")
	}
	pm.notifyWaiters()
}

func (pm *ProcessorManager) notifyWaiters() {
	for {
		next := pm.waiters.Front()
		if next == nil {
			break // No more waiters blocked.
		}

		w := next.Value.(*waiter)
		if !pm.logResRemain.GreaterOrEqual(w.req) {
			// Not enough tokens for the next waiter.  We could keep going (to try to
			// find a waiter with a smaller request), but under load that could cause
			// starvation for large requests; instead, we leave all remaining waiters
			// blocked.
			//
			// Consider a semaphore used as a read-write lock, with N tokens, N
			// readers, and one writer.  Each reader can Acquire(1) to obtain a read
			// lock.  The writer can Acquire(N) to obtain a write lock, excluding all
			// of the readers.  If we allow the readers to jump ahead in the queue,
			// the writer will starve — there is always one token available for every
			// reader.
			break
		}

		pm.logResRemain.Subtract(w.req)
		pm.waiters.Remove(next)
		close(w.ready)
	}
}
