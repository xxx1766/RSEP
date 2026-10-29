package stub

import (
	"context"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/cmnct"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/event"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/processor"
	"github.com/ict/tide/pkg/procmgr"
	"github.com/ict/tide/pkg/routine"
	"github.com/ict/tide/proto/eventpb"
	"google.golang.org/protobuf/proto"
)

var ttlInit sync.Once
var ttlStubs sync.Map

// Only the container executor is replaced: admission, dispatch, completion,
// idle timer, stop broadcast, Close and processor accounting are real.
type ttlExecutor struct {
	rtn         *routine.Routine
	proc        *processor.Processor
	started     chan struct{}
	release     chan struct{}
	events      chan *interfaces.Event
	once        sync.Once
	bindStarted chan struct{}
	bindRelease <-chan struct{}
}

func (e *ttlExecutor) Id() string                             { return "ttl-executor" }
func (e *ttlExecutor) GetRoutine() *routine.Routine           { return e.rtn }
func (e *ttlExecutor) GetProcessor() *processor.Processor     { return e.proc }
func (e *ttlExecutor) GetEventChan() <-chan *interfaces.Event { return e.events }
func (e *ttlExecutor) BindRtn(r *routine.Routine) error       { e.rtn = r; return nil }
func (e *ttlExecutor) BindProc(p *processor.Processor) error {
	e.proc = p
	if e.bindStarted != nil {
		close(e.bindStarted)
	}
	if e.bindRelease != nil {
		<-e.bindRelease
	}
	return nil
}
func (e *ttlExecutor) UnbindRtn() (*routine.Routine, error)      { return e.rtn, nil }
func (e *ttlExecutor) UnbindProc() (*processor.Processor, error) { return e.proc, nil }
func (e *ttlExecutor) SendEvt(*interfaces.Event) error           { return nil }
func (e *ttlExecutor) Close() error                              { e.finish(); return nil }
func (e *ttlExecutor) finish()                                   { e.once.Do(func() { close(e.release) }) }
func (e *ttlExecutor) Execute() error {
	close(e.started)
	// Production Execute sends the request and returns; completion arrives
	// later on the event channel rather than blocking the launch call.
	go func() {
		<-e.release
		e.events <- &interfaces.Event{Type: interfaces.EXIT, Status: routine.EXIT_SUCC}
	}()
	return nil
}

type ttlPool struct {
	exec       *ttlExecutor
	getGate    <-chan struct{}
	getStarted chan struct{}
}

func (p *ttlPool) Get() interfaces.Executor {
	if p.getStarted != nil {
		close(p.getStarted)
	}
	if p.getGate != nil {
		<-p.getGate
	}
	if p.exec == nil {
		return nil
	}
	return p.exec
}
func (p *ttlPool) Return(interfaces.Executor) {}
func (p *ttlPool) IncInstance(int)            {}
func (p *ttlPool) Close() error               { return p.exec.Close() }

func newTTLStub(t *testing.T, ttl time.Duration) (*stub, *ttlExecutor) {
	t.Helper()
	ttlInit.Do(func() {
		clsstat.Init("ttl-node", "ttl-local", clsstat.EDGE, &routine.Resource{CpuNum: 2})
		d := event.NewEventDispatcher()
		d.RegisterHandler(interfaces.StubStopEvt, func(evt *event.Event) {
			var stop eventpb.StubStopEvent
			if proto.Unmarshal(evt.Content, &stop) == nil {
				if value, ok := ttlStubs.Load(stop.StubId); ok {
					value.(*stub).Close()
				}
			}
		})
		cmnct.Init("ttl-node", "ttl-local", d)
	})
	pm := procmgr.NewProcMgr(&routine.Resource{CpuNum: 2})
	ctx := context.WithValue(context.Background(), clsstat.ApplicationId("appid"), "ttl-app")
	p, err := pm.Apply(ctx, &routine.Resource{CpuNum: 2})
	if err != nil {
		t.Fatal(err)
	}
	e := &ttlExecutor{started: make(chan struct{}), release: make(chan struct{}), events: make(chan *interfaces.Event, 1)}
	oldConfig := dev.CurrentConfig()
	t.Cleanup(func() {
		if err := dev.SetConfig(oldConfig); err != nil {
			t.Error(err)
		}
	})
	c := oldConfig
	c.TTLMS = int(ttl / time.Millisecond)
	if err := dev.SetConfig(c); err != nil {
		t.Fatal(err)
	}
	s := NewStub(&CreateStubOpts{
		AppId: "ttl-app", Mode: interfaces.StubFn,
		ExecPool: &ttlPool{exec: e}, Proc: p, ProcM: pm,
	}).(*stub)
	if s.timerGap != ttl {
		t.Fatalf("constructor TTL = %s, want %s", s.timerGap, ttl)
	}
	ttlStubs.Store(s.id, s)
	t.Cleanup(func() {
		e.finish()
		waitTTL(t, func() bool { return atomic.LoadInt64(&s.noExecuting) == 0 }, "execution cleanup")
		s.Close()
		ttlStubs.Delete(s.id)
	})
	waitTTL(t, func() bool { s.mu.Lock(); defer s.mu.Unlock(); return s.status == RUNNING }, "stub ready")
	return s, e
}

func waitTTL(t *testing.T, ready func() bool, desc string) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for !ready() {
		if time.Now().After(deadline) {
			t.Fatalf("timed out waiting for %s", desc)
		}
		time.Sleep(time.Millisecond)
	}
}

func submitTTL(t *testing.T, s *stub) {
	t.Helper()
	r := routine.NewSubmittedRtn(t.Name()+"-routine", "test", nil, &routine.Resource{CpuNum: 2})
	r.InitSid = s.id
	r.SubmitTime = time.Now().UnixNano()
	s.SubmitRoutine(r)
	if atomic.LoadUint64(&s.noSubmitted) != 1 {
		t.Fatal("routine was not admitted locally")
	}
}

func assertTTLAlive(t *testing.T, s *stub, duration time.Duration) {
	t.Helper()
	select {
	case <-s.done:
		t.Fatal("stub expired before it became idle for a full TTL")
	case <-time.After(duration):
	}
}

func TestTTLUsedStubExpiresAfterCompletion(t *testing.T) {
	s, e := newTTLStub(t, 80*time.Millisecond)
	submitTTL(t, s)
	<-e.started
	e.finish()
	waitTTL(t, func() bool { return atomic.LoadInt64(&s.noExecuting) == 0 }, "routine completion")
	select {
	case <-s.done:
	case <-time.After(500 * time.Millisecond):
		t.Fatal("used, idle stub did not expire")
	}
}

func TestTTLBusyStubSurvivesAndCompletionRestartsIdlePeriod(t *testing.T) {
	const ttl = 100 * time.Millisecond
	s, e := newTTLStub(t, ttl)
	submitTTL(t, s)
	<-e.started
	assertTTLAlive(t, s, 2*ttl+ttl/2)
	e.finish()
	waitTTL(t, func() bool { return atomic.LoadInt64(&s.noExecuting) == 0 }, "routine completion")
	assertTTLAlive(t, s, ttl/2)
	select {
	case <-s.done:
	case <-time.After(5 * ttl):
		t.Fatal("stub did not expire after completion restarted its idle period")
	}
}

func TestTTLCloseIsConcurrentAndIdempotent(t *testing.T) {
	s, _ := newTTLStub(t, time.Hour)
	var wg sync.WaitGroup
	for i := 0; i < 20; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); s.Close() }()
	}
	wg.Wait()
	// ProcessorManager fatals if the bound resource is returned twice. A full
	// allocation also proves that Close returned it at least once.
	ctx, cancel := context.WithTimeout(context.WithValue(context.Background(), clsstat.ApplicationId("appid"), s.appId), time.Second)
	defer cancel()
	p, err := s.procM.Apply(ctx, &routine.Resource{CpuNum: 2})
	if err != nil || p == nil {
		t.Fatalf("resource not returned: proc=%v err=%v", p, err)
	}
	s.procM.Return(p, true)
}

func TestTTLIdleUnusedStubExpires(t *testing.T) {
	s, _ := newTTLStub(t, 80*time.Millisecond)
	select {
	case <-s.done:
	case <-time.After(500 * time.Millisecond):
		t.Fatal("unused idle stub did not expire")
	}
}

func TestTTLPendingExecutorPreservesStub(t *testing.T) {
	const ttl = 80 * time.Millisecond
	s, e := newTTLStub(t, ttl)
	gate := make(chan struct{})
	s.execPool.(*ttlPool).getGate = gate
	var release sync.Once
	t.Cleanup(func() { release.Do(func() { close(gate) }) })
	submitTTL(t, s)
	assertTTLAlive(t, s, 3*ttl)
	select {
	case <-e.started:
		t.Fatal("executor unexpectedly started while unavailable")
	default:
	}
	if n := atomic.LoadInt64(&s.noExecuting); n != 1 {
		t.Fatalf("pending admission count = %d, want 1", n)
	}
	release.Do(func() { close(gate) })
	<-e.started
	e.finish()
	waitTTL(t, func() bool { return atomic.LoadInt64(&s.noExecuting) == 0 }, "routine completion")
	assertTTLAlive(t, s, ttl/2)
	select {
	case <-s.done:
	case <-time.After(5 * ttl):
		t.Fatal("stub did not expire after pending work finished")
	}
}

func TestTTLStoppedStubRejectsCloudAdmission(t *testing.T) {
	s, _ := newTTLStub(t, time.Hour)
	s.Close()
	r := routine.NewSubmittedRtn("closed-cloud-routine", "test", nil, &routine.Resource{CpuNum: 2})
	s.CloudSubmitRoutine(r)
	if atomic.LoadInt64(&s.noExecuting) != 0 || atomic.LoadUint64(&s.noSubmitted) != 0 {
		t.Fatal("closed stub admitted cloud work")
	}
	select {
	case <-s.rtnStore.Pop():
		t.Fatal("rejected cloud work was queued")
	default:
	}
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	if !s.WaitRoutineReach(ctx, r.Id) {
		t.Fatal("cloud waiter cannot find the rejected routine's failure")
	}
	select {
	case result := <-s.WaitRtnDone(r.Id):
		if result.Status() != routine.EXIT_FAIL {
			t.Fatal("rejected routine must fail")
		}
	case <-time.After(time.Second):
		t.Fatal("rejected routine did not receive failure data")
	}
}

func TestTTLCloseDuringExecutionReturnsBoundProcessorOnce(t *testing.T) {
	s, e := newTTLStub(t, time.Hour)
	submitTTL(t, s)
	<-e.started
	s.Close()
	e.finish()
	waitTTL(t, func() bool { return atomic.LoadInt64(&s.noExecuting) == 0 }, "execution cleanup after close")
	s.Close()
}

func TestTTLCancelledProcessorAcquisitionClearsAdmission(t *testing.T) {
	s := &stub{done: make(chan struct{}), noExecuting: 1}
	close(s.done)
	s.processOne(routine.NewSubmittedRtn("cancelled", "test", nil, &routine.Resource{CpuNum: 2}))
	if n := atomic.LoadInt64(&s.noExecuting); n != 0 {
		t.Fatalf("cancelled admission count = %d, want 0", n)
	}
}

func TestTTLUnavailableExecutorReturnsProcessorAndExpires(t *testing.T) {
	s, _ := newTTLStub(t, 80*time.Millisecond)
	s.execPool.(*ttlPool).exec = nil
	submitTTL(t, s)
	waitTTL(t, func() bool { return atomic.LoadInt64(&s.noExecuting) == 0 }, "unavailable executor cleanup")
	if len(s.bindCh) != 1 {
		t.Fatal("bound processor was not returned after executor acquisition failed")
	}
	select {
	case <-s.done:
	case <-time.After(500 * time.Millisecond):
		t.Fatal("stub did not expire after failed executor acquisition")
	}
}

func TestTTLClosedStubNeverReturnsBoundProcessor(t *testing.T) {
	s, _ := newTTLStub(t, time.Hour)
	s.Close()
	r := routine.NewSubmittedRtn("after-close", "test", nil, &routine.Resource{CpuNum: 2})
	for i := 0; i < 64; i++ {
		if proc := s.acquireProcessor(r); proc != nil {
			t.Fatal("closed stub returned its already-released bound processor")
		}
	}
}

func TestTTLClosedStubCancelsAdmittedDispatch(t *testing.T) {
	s, e := newTTLStub(t, time.Hour)
	// Model an accepted request whose dispatch goroutine has not run yet.
	s.mu.Lock()
	atomic.AddInt64(&s.noExecuting, 1)
	atomic.AddUint64(&s.noSubmitted, 1)
	s.mu.Unlock()
	r := routine.NewSubmittedRtn("admitted-before-close", "test", nil, &routine.Resource{CpuNum: 2})
	s.Close()
	e.finish()
	s.processOne(r)
	waitTTL(t, func() bool { return atomic.LoadInt64(&s.noExecuting) == 0 }, "cancelled admitted dispatch")
	select {
	case <-e.started:
		t.Fatal("executor started after Close returned the bound processor")
	default:
	}
}

func TestTTLClosedStubCancelsPendingExecutor(t *testing.T) {
	s, e := newTTLStub(t, time.Hour)
	gate := make(chan struct{})
	started := make(chan struct{})
	pool := s.execPool.(*ttlPool)
	pool.getGate = gate
	pool.getStarted = started
	var release sync.Once
	t.Cleanup(func() { release.Do(func() { close(gate) }) })
	submitTTL(t, s)
	<-started
	s.Close()
	e.finish()
	release.Do(func() { close(gate) })
	waitTTL(t, func() bool { return atomic.LoadInt64(&s.noExecuting) == 0 }, "cancelled executor acquisition")
	select {
	case <-e.started:
		t.Fatal("executor started after pending acquisition outlived Close")
	default:
	}
}

func TestTTLCloseSerializesWithExecutionStart(t *testing.T) {
	s, e := newTTLStub(t, time.Hour)
	bound := make(chan struct{})
	gate := make(chan struct{})
	e.bindStarted, e.bindRelease = bound, gate
	var release sync.Once
	t.Cleanup(func() { release.Do(func() { close(gate) }) })
	submitTTL(t, s)
	<-bound
	closing, closed := make(chan struct{}), make(chan struct{})
	go func() { close(closing); s.Close(); close(closed) }()
	<-closing
	select {
	case <-closed:
		t.Error("Close released the processor while execution start was still binding")
	case <-time.After(30 * time.Millisecond):
	}
	release.Do(func() { close(gate) })
	select {
	case <-closed:
	case <-time.After(time.Second):
		t.Fatal("Close did not finish after execution start completed")
	}
	select {
	case <-e.started:
	default:
		t.Fatal("Close completed before the serialized Execute call")
	}
	e.finish()
	waitTTL(t, func() bool { return atomic.LoadInt64(&s.noExecuting) == 0 }, "execution cleanup")
}
