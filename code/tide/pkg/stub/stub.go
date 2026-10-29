package stub

import (
	"sync"
	"time"

	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/processor"
	"github.com/ict/tide/pkg/procmgr"
	"github.com/ict/tide/pkg/routine"
	"github.com/ict/tide/pkg/stub/execpool"
	"github.com/ict/tide/pkg/stub/rtnstore"
	"github.com/ict/tide/pkg/stub/sched"
	"github.com/ict/tide/pkg/stub/waiters"
)

type stub struct {
	id    string
	appId string

	resource   *routine.Resource
	imgName    string
	volumePair string
	commands   []string
	mode       interfaces.StubMode
	scheduler  *sched.Scheduler

	status Status

	noSubmitted uint64
	noExecuting int64
	noSucc      uint64
	noFail      uint64
	rtnStore    rtnstore.RoutineStore

	// the infomation about estimated wait time,
	// update below in the critical section
	accExecTimeInQue int64            // The accumulative estimated execution time of all tasks in the task queue, atomic update
	rtnOnBindProc    *routine.Routine // the routine binding 'bindProc'
	startTime        time.Time        // the start time of task binding 'bindProc'

	waiterStore waiters.WaiterStore // wait/get waiters
	execPool    execpool.ExecutorPool

	bindProc *processor.Processor
	bindCh   chan struct{}
	procM    *procmgr.ProcessorManager

	done chan struct{}

	// when target routine reached, notice the waiter
	reachWaiters sync.Map

	timer         *time.Timer
	timerGap      time.Duration
	mu            sync.Mutex
	startMu       sync.Mutex // serializes Bind/Execute with Close's resource return
	lastActivity  time.Time  // guarded by mu; acceptance or completion, not lifetime submissions
	closeOnce     sync.Once
	ablationProcs map[string]*processor.Processor // stable oversubscribed slots for CC-off experiments
	ablationSlots []*processor.PhysicResource

	// To ensure that invalid notices can be discarded
	lastUpdateTime int64
}

type Status uint8

const (
	INITIALIZING Status = iota
	RUNNING
	STOPPING
	STOPPED
)

func (s *stub) Id() string {
	return s.id
}

func (s *stub) AppId() string {
	return s.appId
}
