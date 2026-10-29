package stub

import (
	"context"
	"sync/atomic"
	"time"

	"github.com/ict/tide/pkg/c4"
	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/cmnct"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/event"
	"github.com/ict/tide/pkg/executor"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/processor"
	"github.com/ict/tide/pkg/procmgr"
	"github.com/ict/tide/pkg/routine"
	"github.com/ict/tide/pkg/stringx"
	"github.com/ict/tide/pkg/stub/execpool"
	"github.com/ict/tide/pkg/stub/rtnstore"
	"github.com/ict/tide/pkg/stub/sched"
	"github.com/ict/tide/pkg/stub/waiters"
	"github.com/ict/tide/proto/eventpb"
	"github.com/pkg/errors"
	"github.com/sirupsen/logrus"
	"google.golang.org/protobuf/proto"
)

// CreateStubOpts is the options for creating a stub.
type CreateStubOpts struct {
	AppId      string
	ImgName    string
	VolumePair string
	Commands   []string
	Mode       interfaces.StubMode
	ResReq     *routine.Resource

	InitExecNo int
	ExecPool   execpool.ExecutorPool

	Proc  *processor.Processor
	ProcM *procmgr.ProcessorManager
}

// NewStub creates a new stub(interface).
func NewStub(opts *CreateStubOpts) interfaces.Stub {
	id := stringx.GenerateId()
	logger := logrus.WithFields(logrus.Fields{
		"StubId":     id,
		"ImageName":  opts.ImgName,
		"VolumePair": opts.VolumePair,
		"Commands":   opts.Commands,
		"Mode":       opts.Mode,
	})
	logger.Debug("Stub: start creating")
	defer logger.Info("Stub: finish creating")

	st := &stub{
		id:         id,
		appId:      opts.AppId,
		resource:   opts.ResReq,
		imgName:    opts.ImgName,
		volumePair: opts.VolumePair,
		commands:   opts.Commands,
		mode:       opts.Mode,

		status: INITIALIZING,

		rtnStore:  rtnstore.New(),
		scheduler: sched.New(opts.AppId, opts.ImgName, opts.Commands),

		waiterStore: waiters.NewStore(),
		execPool:    opts.ExecPool,

		bindProc: opts.Proc,
		bindCh:   make(chan struct{}, 1),
		procM:    opts.ProcM,

		done: make(chan struct{}),

		timerGap: dev.GetStubTTL(),
	}
	st.bindCh <- struct{}{}

	if opts.Mode == interfaces.StubEntry {
		buildEntryStub(st)
	} else {
		buildFnStub(st)
	}
	return st
}

// build a stub in entry mode
func buildEntryStub(st *stub) {
	exec, err := executor.New(st.imgName, st.volumePair, st.commands, st.mode)
	if err != nil {
		logrus.WithFields(logrus.Fields{"StubId": st.id}).Fatal("Stub: create entry executor failed, ", err)
	}
	go st.processEntry(exec)
}

// build a stub in fn mode
func buildFnStub(st *stub) {
	st.lastActivity = time.Now()
	if clsstat.Singleton().GetSelf().Role != clsstat.CLOUD && !dev.RSEPTest {
		st.timer = time.NewTimer(st.timerGap)
		go st.exitWhenIdle()
	}
	go st.processFnLoop()
}

// SubmitRoutine is used by stub to receive routine.
func (s *stub) SubmitRoutine(rtn *routine.Routine) {
	rtn.RequireLocalCapacity = rtn.RequireLocalCapacity || dev.CurrentConfig().C4Functions[rtn.FnName]
	if rtn.RequireLocalCapacity && rtn.InitSid == s.id && rtn.LocalNodeID == "" {
		rtn.LocalNodeID = clsstat.Singleton().GetSelf().Id
	}
	if err := c4.Check(rtn); err != nil {
		s.rejectRoutine(rtn, err, false)
		return
	}
	// Every request must fit the receiving function stub's reserved processor.
	// Keeping the origin capable (C4) does not replace destination feasibility.
	if s.mode == interfaces.StubFn && (rtn.ResReq == nil || s.bindProc == nil || !s.bindProc.LogicRes.GreaterOrEqual(rtn.ResReq)) {
		s.rejectRoutine(rtn, errors.New("request exceeds destination stub capacity"), false)
		return
	}
	logger := logrus.WithFields(logrus.Fields{
		"StubId":    s.id,
		"RoutineId": rtn.Id,
	})
	logger.Debug("Stub: receive a routine submitting")

	// TODO: 预估执行时间等可配置
	estExecTime := dev.GetEstExecTime(clsstat.Singleton().GetSelf().Id)
	endBefore := rtn.SubmitTime + int64(dev.GetToleranceTimeFor(rtn.FnName))
	// TODO: 已经超出endbefore的情况如何处理
	if s.mode == interfaces.StubFn {
		s.mu.Lock()
		logrus.WithFields(logrus.Fields{"RoutineId": rtn.Id, "SubmitTime": rtn.SubmitTime, "endBefore": endBefore, "Timenow": time.Now().UnixNano(), "estWaitTime": s.estWaitTime()}).Debug("StubFn submit: self condition")
		if s.status == RUNNING &&
			time.Now().UnixNano()+s.estWaitTime()+int64(estExecTime) <= endBefore {
			// (endBefore < time.Now().UnixNano() || s.estWaitTime()+int64(estExecTime) < int64(dev.GetToleranceTime())) {
			atomic.AddInt64(&s.noExecuting, 1) // Includes admitted work still waiting in the queue.
			atomic.AddUint64(&s.noSubmitted, 1)
			s.lastActivity = time.Now()
			s.accExecTimeInQue += int64(estExecTime)
			go s.noticeStubUpdate(s.estWaitTime(), time.Now())
			s.mu.Unlock()

			s.rtnStore.Record(rtn)
			s.rtnStore.Push(rtn)
			noticeRtnReceived(rtn, false, s.id)

			logger.Debug("Stub: push a routine to queue")
			return
		}
		s.mu.Unlock()
	}

	go func() {
		toCloud, cloudId := s.scheduler.Schedule(context.Background(), rtn)
		if toCloud {
			noticeRtnReceived(rtn, toCloud, cloudId)
		} else if cloudId == "" {
			s.rejectRoutine(rtn, errors.New("no feasible execution destination"), false)
		}
	}()
}

// SetReceiver sets the receiver of a routine.
func (s *stub) SetReceiver(rtnId string, toCloud bool, recvId string) {
	s.rtnStore.Get(rtnId).SetReceiver(toCloud, recvId)
}

// WaitRoutineReach waits for a routine to reach.
func (s *stub) WaitRoutineReach(ctx context.Context, rid string) bool {
	reachCh := make(chan struct{})
	val, _ := s.reachWaiters.LoadOrStore(rid, reachCh)
	reachCh = val.(chan struct{})

	if s.rtnStore.Exist(rid) {
		// the key may be delete by submit procedure, so cannot close directly.
		// if the key is not existed, it is deleted by submit procedure or
		// the following goroutine of another wait procedure
		if _, ok := s.reachWaiters.LoadAndDelete(rid); ok {
			close(reachCh)
		}
	}

	select {
	case <-reachCh:
		return true
	case <-ctx.Done():
		s.reachWaiters.Delete(rid)
		return false
	}
}

func noticeRtnReceived(rtn *routine.Routine, toCloud bool, receiverId string) {
	// offloaded task is received by itself
	if !toCloud && rtn.InitSid == receiverId {
		rtn.SetReceiver(false, receiverId)
		return
	}

	var evt *event.Event
	rtnRecvEvtPb := &eventpb.RoutineReceiveEvent{
		RtnId:      rtn.Id,
		ToCloud:    toCloud,
		ReceiverId: receiverId,
	}
	content, err := proto.Marshal(rtnRecvEvtPb)
	if err != nil {
		err = errors.Wrap(err, "Sched: failed to marshal RoutineReceiveEvent, ")
		goto onerr
	}

	evt = &event.Event{
		Type:    interfaces.RtnRecvEvt(rtn.InitSid),
		Content: content,
	}
	if err = cmnct.Singleton().SendToStub(rtn.InitSid, evt); err != nil {
		err = errors.Wrap(err, "Sched: failed to send event to stub")
		goto onerr
	}
	return

onerr:
	logrus.WithFields(logrus.Fields{
		"RoutineId":  rtn.Id,
		"InitStubId": rtn.InitSid,
		"ReceiverId": receiverId,
		"ToCloud":    toCloud,
	}).Error(err)
}

// WaitRtnDone waits for a routine to be done.
func (s *stub) WaitRtnDone(rid string) <-chan *routine.Routine {
	rtn := s.rtnStore.Get(rid)
	ch := make(chan *routine.Routine, 1)

	go func() {
		<-rtn.WaitData()
		ch <- rtn
		close(ch)
	}()
	return ch
}

// NoticeRtnDone notices certain routine is done.
func (s *stub) NoticeRtnDone(rid string, status routine.Status) {
	rtn := s.rtnStore.Get(rid)
	toCloud, recvId := rtn.Receiver()
	if toCloud || recvId != s.id {
		if status == routine.EXIT_SUCC {
			rtn.Succ()
			rtn.ClearArgs()
		} else {
			rtn.Fail()
		}
	}

	ws := s.waiterStore.MarkDone(rid, false)
	for _, w := range ws {
		s.responseToExec(w, rtn)
	}
}

// NoticeRtnData notices that a routine is done with data.
func (s *stub) NoticeRtnData(rid string, status routine.Status, result []byte, err string) {
	rtn := s.rtnStore.Get(rid)
	toCloud, recvId := rtn.Receiver()
	if toCloud || recvId != s.id {
		if status == routine.EXIT_SUCC {
			rtn.Succ()
			rtn.SetResult(result)
			rtn.ClearArgs()
		} else {
			rtn.Fail()
			rtn.SetError(err)
		}
	}

	ws := s.waiterStore.MarkDone(rid, true)
	for _, w := range ws {
		s.responseToExec(w, rtn)
	}
}

// TODO: 需要返回一个Rtn结果数组
func (s *stub) responseToExec(w *waiters.Waiter, rtn *routine.Routine) {
	logrus.WithFields(logrus.Fields{
		"StubId":     s.id,
		"RoutineIds": w.RefIds,
		"WithData":   w.WithData,
	}).Debug("Stub: notice executor that the routine(s) is done")

	evt := &interfaces.Event{}
	evt.Type = interfaces.WAIT_GET
	evt.RefIds = w.RefIds
	evt.Status = rtn.Status()
	evt.WithData = w.WithData
	if w.WithData {
		if evt.Status == routine.EXIT_SUCC {
			evt.Result = rtn.Result()
		} else {
			evt.Error = rtn.Error()
		}
	}
	w.Exec.SendEvt(evt)
}

// CloudSubmitRoutine is used by cloud to receive routine.
func (s *stub) CloudSubmitRoutine(rtn *routine.Routine) {
	rtn.RequireLocalCapacity = rtn.RequireLocalCapacity || dev.CurrentConfig().C4Functions[rtn.FnName]
	if err := c4.Check(rtn); err != nil {
		s.rejectRoutine(rtn, err, true)
		return
	}
	// Every request must fit the receiving function stub's reserved processor.
	// Keeping the origin capable (C4) does not replace destination feasibility.
	if s.mode == interfaces.StubFn && (rtn.ResReq == nil || s.bindProc == nil || !s.bindProc.LogicRes.GreaterOrEqual(rtn.ResReq)) {
		s.rejectRoutine(rtn, errors.New("request exceeds destination stub capacity"), true)
		return
	}
	logger := logrus.WithFields(logrus.Fields{
		"StubId":    s.id,
		"RoutineId": rtn.Id,
	})
	logger.Debug("Stub: receive a routine submitting")

	s.mu.Lock()
	if s.status == STOPPING || s.status == STOPPED {
		s.mu.Unlock()
		rtn.Fail()
		rtn.SetError("stub is stopping")
		// Cloud wait events may arrive before submission. Retain the failure
		// for those waiters without admitting execution on the stopped stub.
		s.rtnStore.Record(rtn)
		go s.noticeReachWaiter(rtn)
		return
	}
	atomic.AddInt64(&s.noExecuting, 1)
	atomic.AddUint64(&s.noSubmitted, 1)
	s.lastActivity = time.Now()
	s.mu.Unlock()

	s.rtnStore.Record(rtn)
	s.rtnStore.Push(rtn)
	go s.noticeReachWaiter(rtn)
	logger.Debug("Stub: push a routine to queue")
}

func (s *stub) noticeReachWaiter(rtn *routine.Routine) {
	val, ok := s.reachWaiters.LoadAndDelete(rtn.Id)
	if !ok {
		return
	}
	ch := val.(chan struct{})
	close(ch)
}

func (st *stub) noticeStubUpdate(estWaitTime int64, updateTime time.Time) {
	if dev.RSEPTest {
		return
	}

	// It is guaranteed that only the last call is executed when called concurrently
	ts := updateTime.UnixNano()
	for {
		lastUpdateTime := atomic.LoadInt64(&st.lastUpdateTime)
		if lastUpdateTime >= ts {
			return
		}
		if atomic.CompareAndSwapInt64(&st.lastUpdateTime, lastUpdateTime, ts) {
			break
		}
	}

	stSyncEvtPb := &eventpb.StubSyncEvent{
		StubId:      st.id,
		EstWaitTime: uint64(estWaitTime),
		UpdateTime:  ts,
	}
	content, err := proto.Marshal(stSyncEvtPb)
	if err != nil {
		logrus.Error("Stub: failed to marshal StubSyncEvent, ", err)
		return
	}

	evt := &event.Event{
		Type:    interfaces.StubSyncEvt,
		Content: content,
	}
	cmnct.Singleton().CacheStubSyncEvt(st.id, evt)
}

// EXP
func (s *stub) SubmitRoutineExp(rtn *routine.Routine) {
	// This direct experimental API is also a source ingress.
	if rtn.InitSid == "" {
		rtn.InitSid = s.id
	}
	rtn.RequireLocalCapacity = rtn.RequireLocalCapacity || dev.CurrentConfig().C4Functions[rtn.FnName]
	if rtn.RequireLocalCapacity && rtn.InitSid == s.id && rtn.LocalNodeID == "" {
		rtn.LocalNodeID = clsstat.Singleton().GetSelf().Id
	}
	if err := c4.Check(rtn); err != nil {
		s.rejectRoutine(rtn, err, false)
		return
	}
	// Every request must fit the receiving function stub's reserved processor.
	// Keeping the origin capable (C4) does not replace destination feasibility.
	if s.mode == interfaces.StubFn && (rtn.ResReq == nil || s.bindProc == nil || !s.bindProc.LogicRes.GreaterOrEqual(rtn.ResReq)) {
		s.rejectRoutine(rtn, errors.New("request exceeds destination stub capacity"), false)
		return
	}
	logger := logrus.WithFields(logrus.Fields{
		"StubId":    s.id,
		"RoutineId": rtn.Id,
	})
	logger.Debug("Stub: receive a routine submitting")

	s.mu.Lock()
	if s.status == STOPPING || s.status == STOPPED {
		s.mu.Unlock()
		rtn.Fail()
		rtn.SetError("stub is stopping")
		return
	}
	atomic.AddInt64(&s.noExecuting, 1)
	atomic.AddUint64(&s.noSubmitted, 1)
	s.lastActivity = time.Now()
	s.mu.Unlock()

	s.rtnStore.Record(rtn)
	if dev.RSEPTest && dev.EnableConcurrencyControl {
		s.rtnStore.Push(rtn)
	} else {
		s.processOne(rtn)
	}

	logger.Debug("Stub: push a routine to queue")
}

// estimate wait time, should be called in the critical section
func (s *stub) estWaitTime() int64 {
	var remainTime int64 = 0
	if s.rtnOnBindProc != nil {
		// Elapsed time of routine executing on 'bindProc'
		elspsedTime := time.Since(s.startTime).Nanoseconds()
		estExecTime := int64(dev.GetEstExecTime(clsstat.Singleton().GetSelf().Id))
		if elspsedTime > estExecTime { // TODO: 实际执行时间已超出预估执行时间，如何处理？
			remainTime = estExecTime
		} else {
			remainTime = estExecTime - elspsedTime
		}
	}
	return s.accExecTimeInQue + remainTime
}

// Close closes the stub.
func (s *stub) Close() error {
	s.closeOnce.Do(func() {
		// EXP: 等待所有任务执行结束
		if dev.RSEPTest {
			for atomic.LoadUint64(&s.noSubmitted) != atomic.LoadUint64(&s.noSucc)+atomic.LoadUint64(&s.noFail) {
				time.Sleep(time.Second)
			}
		}

		// An execution launch that won this lock must finish Bind/Execute
		// before its processor can be released. Later launches see STOPPED.
		s.startMu.Lock()
		defer s.startMu.Unlock()
		s.mu.Lock()
		s.status = STOPPED
		close(s.done)
		s.mu.Unlock()
		s.scheduler.Close()

		s.procM.Return(s.bindProc, true)
		// Keep the bound processor's identity stable: a concurrent execution's
		// cleanup must not mistake it for a borrowed processor and return it again.

		logrus.WithFields(logrus.Fields{
			"StubId":      s.id,
			"Mode":        s.mode,
			"NoSubmitted": atomic.LoadUint64(&s.noSubmitted),
			"NoExecuted":  atomic.LoadInt64(&s.noExecuting),
			"NoSucc":      atomic.LoadUint64(&s.noSucc),
			"NoFail":      atomic.LoadUint64(&s.noFail),
		}).Info("Stub: destory one")
	})
	return nil
}

func (st *stub) exitWhenIdle() {
	defer st.timer.Stop()
	for {
		select {
		case <-st.done:
			return

		case <-st.timer.C:
			st.mu.Lock()
			if st.status == STOPPING || st.status == STOPPED {
				st.mu.Unlock()
				return
			}
			remaining := st.timerGap - time.Since(st.lastActivity)
			if st.status == RUNNING && atomic.LoadInt64(&st.noExecuting) == 0 && remaining <= 0 {
				// Admission uses the same lock: queued and running work both
				// count as executing, and no work can enter after STOPPING.
				st.status = STOPPING
				st.mu.Unlock()
				logrus.WithField("StubId", st.id).Info("idle TTL expired, exiting")
				stubExit(st.id)
				return
			}
			if remaining <= 0 {
				remaining = st.timerGap
			}
			st.mu.Unlock()
			// Only this goroutine resets the timer, after receiving its tick.
			st.timer.Reset(remaining)
		}
	}
}

func stubExit(sid string) {
	// EXP
	if dev.RSEPTest {
		return
	}

	stStopEvtPb := &eventpb.StubStopEvent{StubId: sid}
	content, err := proto.Marshal(stStopEvtPb)
	if err != nil {
		logrus.Error("Stub: failed to marshal StubStopEvent, ", err)
	}
	evt := &event.Event{Type: interfaces.StubStopEvt, Content: content}
	if err := cmnct.Singleton().BroadcastToEdgThg(evt); err != nil {
		logrus.Error("Stub: failed to broadcast StubStopEvent, ", err)
	}
}

// Rejection is a terminal result, not an execution admission. Keep the normal
// wait protocol usable, including cloud wait events that arrive before submit.
func (s *stub) rejectRoutine(rtn *routine.Routine, err error, cloud bool) {
	rtn.Fail()
	rtn.SetError(err.Error())
	s.rtnStore.Record(rtn)
	if cloud {
		go s.noticeReachWaiter(rtn)
	} else {
		noticeRtnReceived(rtn, false, s.id)
	}
}
