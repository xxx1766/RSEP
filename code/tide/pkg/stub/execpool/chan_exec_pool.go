package execpool

//deprecated: use stack_exec_pool.go instead

import (
	"sync"

	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/sirupsen/logrus"
)

type chanExecPool struct {
	imgName    string
	volumePair string
	commands   []string
	mode       interfaces.StubMode

	noConcurrency int32 // 当前并发的Executor数量
	noTotal       int32 // 当前创建的Executor数量，包括正在创建的
	idleExec      chan interfaces.Executor

	listMu    sync.Mutex // 使executors并发安全
	executors []interfaces.Executor

	done chan struct{}
	mu   sync.Mutex
}

func newChanPool(sz int, imgName, volumePair string, commands []string, mode interfaces.StubMode) ExecutorPool {
	logrus.WithFields(logrus.Fields{
		"ImageName":      imgName,
		"NoInitExecutor": sz,
	}).Debugf("ChanPool: start creating")
	p := &chanExecPool{
		imgName:   imgName,
		commands:  commands,
		mode:      mode,
		idleExec:  make(chan interfaces.Executor, 1000),
		executors: make([]interfaces.Executor, sz),
		noTotal:   int32(sz),
		done:      make(chan struct{}),
	}

	var wg sync.WaitGroup
	wg.Add(sz)
	for i := 0; i < sz; i++ {
		go func(i int) {
			defer wg.Done()
			exe, err := makeNew(imgName, volumePair, commands, mode)
			if err != nil {
				return
			}

			p.executors[i] = exe
			p.idleExec <- exe
		}(i)
	}
	wg.Wait()
	logrus.WithField("ImageName", imgName).Debug("ChanPool: finish creating")
	return p
}

func (p *chanExecPool) Close() error {
	close(p.done)
	var err error
	for _, exe := range p.executors {
		if e := exe.Close(); e != nil {
			err = e
		}
	}
	return err
}

func (p *chanExecPool) Get() interfaces.Executor {
	p.mu.Lock()
	p.noConcurrency++
	// 枚举每个时刻的并发量（即最大并发量）与当前Executor数量（包括正在创建的）的比较
	// 如果并发量小于数量，则等待idle chan即可
	// 否则，需要创建新的executor
	if p.noConcurrency <= p.noTotal {
		p.mu.Unlock()
		return <-p.idleExec
	}
	p.noTotal++
	p.mu.Unlock()

	go p.createThread()
	return <-p.idleExec
}

func (p *chanExecPool) Return(exe interfaces.Executor) {
	p.mu.Lock()
	p.noConcurrency--
	p.mu.Unlock()
	p.idleExec <- exe
}

func (p *chanExecPool) IncInstance(delta int) {
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

func (p *chanExecPool) createThread() {
	exec, err := makeNew(p.imgName, p.volumePair, p.commands, p.mode)
	if err != nil {
		return
	}

	evtCh := exec.GetEventChan()
	evtPb := <-evtCh
	if evtPb.Type != interfaces.INIT {
		logrus.Fatal("StackPool: the first event is not INIT_EVT")
	}

	select {
	case <-p.done:
		exec.Close()
	default:
		p.listMu.Lock()
		p.executors = append(p.executors, exec)
		p.listMu.Unlock()
		p.idleExec <- exec
	}
}

var _ ExecutorPool = &chanExecPool{}
