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

	accExecTimeInQue int64
	rtnOnBindProc    *routine.Routine
	startTime        time.Time

	waiterStore waiters.WaiterStore
	execPool    execpool.ExecutorPool

	bindProc *processor.Processor
	bindCh   chan struct{}
	procM    *procmgr.ProcessorManager

	done chan struct{}

	reachWaiters sync.Map

	timer         *time.Timer
	timerGap      time.Duration
	mu            sync.Mutex
	startMu       sync.Mutex
	lastActivity  time.Time
	closeOnce     sync.Once
	ablationProcs map[string]*processor.Processor
	ablationSlots []*processor.PhysicResource

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
