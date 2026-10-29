package execpool

import (
	"container/list"
	"sync"

	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/sirupsen/logrus"
)

type waiter struct {
	ready chan<- interfaces.Executor
}

type stackExecPool struct {
	imgName    string
	volumePair string
	commands   []string
	mode       interfaces.StubMode

	noConcurrency int32
	noTotal       int32
	maxTotal      int32 // zero permits normal runtime growth; ablation fixes a worker budget
	idleStack     list.List
	waiters       list.List
	mu            sync.Mutex

	executors []interfaces.Executor

	done chan struct{}
}

func newStackPool(imgName, volumePair string, commands []string, mode interfaces.StubMode) ExecutorPool {
	logrus.WithFields(logrus.Fields{
		"ImageName":  imgName,
		"VolumePair": volumePair,
	}).Debug("StackPool: start creating")
	ep := &stackExecPool{
		imgName:    imgName,
		volumePair: volumePair,
		commands:   commands,
		mode:       mode,
		noTotal:    0,
		executors:  make([]interfaces.Executor, 0, 1),
		done:       make(chan struct{}),
	}
	if dev.RSEPTest {
		ep.maxTotal = int32(clsstat.Singleton().GetSelf().ResTotal.CpuNum)
		if dev.EnableConcurrencyControl {
			ep.maxTotal /= int32(dev.GetReqResource().CpuNum)
		}
	}

	logrus.WithFields(logrus.Fields{
		"ImageName":  imgName,
		"VolumePair": volumePair,
	}).Debug("StackPool: finish creating")
	return ep
}

func (p *stackExecPool) Close() error {
	close(p.done)

	var err error
	var wg sync.WaitGroup
	wg.Add(len(p.executors))
	for _, exe := range p.executors {
		go func(exec interfaces.Executor) {
			defer wg.Done()
			if e := exec.Close(); e != nil {
				err = e
			}
		}(exe)
	}
	wg.Wait()
	return err
}

func (p *stackExecPool) Get() (exec interfaces.Executor) {
	// EXP
	if dev.RSEPTest && !dev.EnableContainerPerRoutine {
		elem := p.idleStack.Back()
		return elem.Value.(interfaces.Executor)
	}

	p.mu.Lock()
	p.noConcurrency++
	if p.noConcurrency <= p.noTotal && p.idleStack.Len() > 0 {
		elem := p.idleStack.Back()
		p.idleStack.Remove(elem)
		exec = elem.Value.(interfaces.Executor)
		p.mu.Unlock()
		return
	}

	ready := make(chan interfaces.Executor)
	w := &waiter{ready: ready}
	p.waiters.PushBack(w)

	if p.noConcurrency > p.noTotal && (p.maxTotal == 0 || p.noTotal < p.maxTotal) {
		p.noTotal++
		go p.createThread()
	}
	p.mu.Unlock()

	select {
	case <-p.done:
	case exec = <-ready:
	}
	return
}

func (p *stackExecPool) Return(e interfaces.Executor) {
	// EXP
	if dev.RSEPTest && !dev.EnableContainerPerRoutine {
		return
	}

	p.mu.Lock()
	defer p.mu.Unlock()

	p.noConcurrency--
	if p.waiters.Len() > 0 { // 如果有等待的，直接给他
		elem := p.waiters.Front()
		w := elem.Value.(*waiter)
		w.ready <- e
		p.waiters.Remove(elem)
	} else {
		p.idleStack.PushBack(e)
	}
}

func (p *stackExecPool) IncInstance(delta int) {
	// EXP
	if p.noTotal == 0 && dev.RSEPTest && !dev.EnableContainerPerRoutine {
		delta = 1
	}

	p.mu.Lock()
	//TODO: 最大数量还应和Routine需求有关
	max_delta := clsstat.Singleton().GetSelf().ResTotal.CpuNum - int(p.noTotal)
	if delta > max_delta {
		delta = max_delta
	}
	p.noTotal += int32(delta)
	p.mu.Unlock()

	var wg sync.WaitGroup
	wg.Add(delta)
	for i := 0; i < delta; i++ {
		go func() {
			defer wg.Done()
			p.createThread()
		}()
	}
	wg.Wait()
}

func (p *stackExecPool) createThread() {
	newExec, err := makeNew(p.imgName, p.volumePair, p.commands, p.mode)
	if err != nil {
		logrus.Fatal("StackPool: make new executor failed, ", err)
		return
	}

	evtCh := newExec.GetEventChan()
	evtPb := <-evtCh
	if evtPb.Type != interfaces.INIT {
		logrus.Fatal("StackPool: the first event is not INIT_EVT")
	}
	logrus.WithField("ExecutorId", newExec.Id()).Info("StackPool: received one INIT event")

	select {
	case <-p.done:
		newExec.Close()
	default:
		p.mu.Lock()
		p.executors = append(p.executors, newExec)
		if p.waiters.Len() > 0 { // 如果有等待的，直接给他
			elem := p.waiters.Front()
			w := elem.Value.(*waiter)
			w.ready <- newExec
			p.waiters.Remove(elem)
		} else {
			p.idleStack.PushFront(newExec)
		}
		p.mu.Unlock()
	}
}
