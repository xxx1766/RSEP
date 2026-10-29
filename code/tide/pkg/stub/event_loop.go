package stub

import (
	"sync/atomic"

	"github.com/ict/tide/pkg/cmnct"
	"github.com/ict/tide/pkg/event"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/routine"
	"github.com/ict/tide/pkg/stub/waiters"
	"github.com/ict/tide/proto/eventpb"
	"github.com/pkg/errors"
	"github.com/sirupsen/logrus"
	"google.golang.org/protobuf/proto"
)

func (s *stub) listenEvt(exec interfaces.Executor) {
	evtCh := exec.GetEventChan()
loop:
	for {
		select {
		case <-s.done:
			return

		case evt := <-evtCh:
			if evt.Type == interfaces.INIT {
				if s.mode != interfaces.StubEntry {
					logrus.Fatal("Stub: receive a INIT event in event loop, but this stub is not in entry mode")
				}
				s.mu.Lock()
				if s.status == INITIALIZING {
					s.status = RUNNING
				}
				s.mu.Unlock()
				continue
			}

			if err := s.handleEvent(exec, evt); err != nil {
				logrus.Fatal(errors.Wrapf(err, "Stub: handle event failed, %v", evt))
			}
			if evt.Type == interfaces.EXIT {
				break loop
			}
		}
	}
}

// Event Handler

var evtHdlrMap map[interfaces.EventType]func(*stub, interfaces.Executor, *interfaces.Event) error

func init() {
	evtHdlrMap = make(map[interfaces.EventType]func(*stub, interfaces.Executor, *interfaces.Event) error)
	evtHdlrMap[interfaces.WAIT_GET] = handleWaitGet
	evtHdlrMap[interfaces.SUBMIT_ROUTINE] = handleSubmitRtn
	evtHdlrMap[interfaces.EXIT] = handleExit
}

func (s *stub) handleEvent(e interfaces.Executor, evt *interfaces.Event) error {
	hdlr, ok := evtHdlrMap[evt.Type]
	if !ok {
		logrus.Fatal("invalid event type", evt)
	}
	logrus.Debugf("Stub: receive a event, %v", evt)
	return hdlr(s, e, evt)
}

func handleWaitGet(s *stub, e interfaces.Executor, evt *interfaces.Event) error {
	logrus.WithFields(logrus.Fields{
		"StubId":     s.id,
		"RoutineIds": evt.RefIds,
		"WithData":   evt.WithData,
	}).Debug("Stub: start sending the wait event")

	s.waiterStore.Push(&waiters.Waiter{RefIds: evt.RefIds, Exec: e, WithData: evt.WithData})
	for _, refId := range evt.RefIds {
		rtn := s.rtnStore.Get(refId)
		go sendRtnWaitEvt(rtn, s.appId, s.id, evt.WithData)
	}
	return nil
}

func handleSubmitRtn(s *stub, e interfaces.Executor, evt *interfaces.Event) error {
	evt.Routine.InitSid = s.id
	s.rtnStore.Record(evt.Routine)
	s.SubmitRoutine(evt.Routine)
	return nil
}

func handleExit(s *stub, e interfaces.Executor, evt *interfaces.Event) error {
	if s.mode == interfaces.StubFn {
		rtn := e.GetRoutine()
		logrus.
			WithFields(logrus.Fields{"RoutineId": rtn.Id, "StubId": s.id}).
			Debug("Stub: receive the routine exit event")
		if evt.Status == routine.EXIT_SUCC {
			rtn.Succ()
			rtn.SetResult(evt.Result)
			atomic.AddUint64(&s.noSucc, 1)
		} else {
			rtn.Fail()
			rtn.SetError(evt.Error)
			atomic.AddUint64(&s.noFail, 1)
		}
	}
	return nil
}

func sendRtnWaitEvt(rtn *routine.Routine, reqAppId, reqSid string, withData bool) {
	rtnWGEvtPb := &eventpb.RoutineWaitGetEvent{RtnId: rtn.Id, ReqSid: reqSid, WithData: withData}
	content, err := proto.Marshal(rtnWGEvtPb)
	if err != nil {
		logrus.Error("Stub: failed to marshal RoutineWaitGetEvent, ", err)
		return
	}

	logrus.WithFields(logrus.Fields{"RoutineId": rtn.Id, "StubId": reqSid}).Debug("Stub: send rtn wait event")

	<-rtn.WaitReceive() // TODO: 此处默认由InitStub发起，如果以rtn为参数传输后，会出现问题

	logrus.WithFields(logrus.Fields{"RoutineId": rtn.Id, "StubId": reqSid}).Debug("Stub: send rtn wait event over")

	toCloud, recvId := rtn.Receiver()

	logrus.WithFields(logrus.Fields{"RoutineId": rtn.Id, "StubId": reqSid, "toCloud": toCloud, "recvId": recvId}).Debug("Stub: send rtn wait event to receiver")

	if !toCloud {
		evt := &event.Event{Type: interfaces.RtnWaitEvt(recvId), Content: content}
		if err := cmnct.Singleton().SendToStub(recvId, evt); err != nil {
			logrus.
				WithFields(logrus.Fields{"StubId": reqSid, "RoutineId": rtn.Id, "TargetSid": recvId}).
				Error("Stub: send RoutineWaitEvent failed")
		}

	} else {
		evt := &event.Event{Type: interfaces.CldRtnWaitEvt(reqAppId), Content: content}
		if err := cmnct.Singleton().SendToNode(recvId, evt); err != nil {
			logrus.
				WithFields(logrus.Fields{"StubId": reqSid, "RoutineId": rtn.Id, "TargetNid": recvId}).
				Error("Stub: send RoutineWaitEvent failed")
		}
	}

	logrus.WithFields(logrus.Fields{"RoutineId": rtn.Id, "StubId": reqSid, "toCloud": toCloud, "recvId": recvId}).Debug("Stub: send rtn wait event to receiver over")

}
