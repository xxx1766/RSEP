package stubhdlr

import (
	"github.com/ict/tide/pkg/cmnct"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/event"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/routine"
	"github.com/ict/tide/proto/eventpb"
	"github.com/sirupsen/logrus"
	"google.golang.org/protobuf/proto"
)

func RegisterEventHandler(evtDispr event.EventDispatcher, stub interfaces.Stub) {
	evtDispr.RegisterHandler(interfaces.RtnSbmEvt(stub.Id()), rtnSubmitHdlr(stub))
	evtDispr.RegisterHandler(interfaces.RtnRecvEvt(stub.Id()), rtnRecvHdlr(stub))
	evtDispr.RegisterHandler(interfaces.RtnWaitEvt(stub.Id()), rtnWaitHdlr(stub))
	evtDispr.RegisterHandler(interfaces.RtnDoneEvt(stub.Id()), rtnDoneHdlr(stub))
}

func rtnSubmitHdlr(stub interfaces.Stub) event.EventHandler {
	return func(evt *event.Event) {
		rtnSbmEvt := &eventpb.RoutineSubmitEvent{}
		if err := proto.Unmarshal(evt.Content, rtnSbmEvt); err != nil {
			logrus.WithField("StubId", stub.Id()).Error("StubHdrl: failed to unmarshal routine submit event, ", err)
			return
		}

		rtn := routine.FromSubmitEvent(rtnSbmEvt, dev.GetReqResource())
		stub.SubmitRoutine(rtn)
	}
}

func rtnRecvHdlr(stub interfaces.Stub) event.EventHandler {
	return func(evt *event.Event) {
		rtnRecvEvtPb := &eventpb.RoutineReceiveEvent{}
		if err := proto.Unmarshal(evt.Content, rtnRecvEvtPb); err != nil {
			logrus.WithField("StubId", stub.Id()).Error("StubHdrl: failed to unmarshal RoutineReceiveEvent, ", err)
			return
		}
		stub.SetReceiver(rtnRecvEvtPb.RtnId, rtnRecvEvtPb.ToCloud, rtnRecvEvtPb.ReceiverId)
	}
}

func rtnWaitHdlr(stub interfaces.Stub) event.EventHandler {
	return func(evt *event.Event) {
		rtnWGEvtPb := &eventpb.RoutineWaitGetEvent{}
		if err := proto.Unmarshal(evt.Content, rtnWGEvtPb); err != nil {
			logrus.WithField("StubId", stub.Id()).Error("StubHdrl: failed to unmarshal RoutineWaitEvent, ", err)
			return
		}

		logrus.WithFields(logrus.Fields{"RoutineId": rtnWGEvtPb.RtnId, "StubId": stub.Id()}).
			Debug("StubHdlr: start waitting routine done")
		rtn := <-stub.WaitRtnDone(rtnWGEvtPb.RtnId)
		logrus.WithFields(logrus.Fields{"RoutineId": rtnWGEvtPb.RtnId, "StubId": stub.Id()}).
			Debug("StubHdlr: finish waiting routine done")

		if rtnWGEvtPb.ReqSid == stub.Id() {
			stub.NoticeRtnData(rtn.Id, rtn.Status(), rtn.Result(), rtn.Error())
			return
		}

		rtnDoneEvtPb := &eventpb.RoutineDoneEvent{RtnId: rtn.Id, WithData: rtnWGEvtPb.WithData}
		if rtn.Status() == routine.EXIT_SUCC {
			rtnDoneEvtPb.Status = eventpb.RoutineDoneEvent_SUCC
			if rtnDoneEvtPb.WithData {
				rtnDoneEvtPb.Result = rtn.Result()
			}
		} else {
			rtnDoneEvtPb.Status = eventpb.RoutineDoneEvent_FAIL
			if rtnDoneEvtPb.WithData {
				err := rtn.Error()
				rtnDoneEvtPb.Error = &err
			}
		}
		content, err := proto.Marshal(rtnDoneEvtPb)
		if err != nil {
			logrus.Error("Stub: failed to marshal RoutineDoneEvent, ", err)
			return
		}

		logrus.WithFields(logrus.Fields{
			"StubId":    stub.Id(),
			"RoutineId": rtn.Id,
			"TargetSid": rtnWGEvtPb.ReqSid,
		}).Debug("StubHdlr: start sending RoutineDoneEvent")
		evt = &event.Event{Type: interfaces.RtnDoneEvt(rtnWGEvtPb.ReqSid), Content: content}
		if err := cmnct.Singleton().SendToStub(rtnWGEvtPb.ReqSid, evt); err != nil {
			logrus.WithFields(logrus.Fields{
				"StubId":    stub.Id(),
				"RoutineId": rtn.Id,
				"TargetSid": rtnWGEvtPb.ReqSid,
			}).Error("StubHdrl: send RoutineDoneEvent failed")
		}
	}
}

func rtnDoneHdlr(stub interfaces.Stub) event.EventHandler {
	return func(evt *event.Event) {
		rtnDoneEvtPb := &eventpb.RoutineDoneEvent{}
		if err := proto.Unmarshal(evt.Content, rtnDoneEvtPb); err != nil {
			logrus.WithField("StubId", stub.Id()).Error("StubHdrl: failed to unmarshal RoutineDoneEvent, ", err)
			return
		}
		logrus.WithFields(logrus.Fields{"RoutineId": rtnDoneEvtPb.RtnId, "StubId": stub.Id()}).
			Debug("StubHdlr: receive a RoutineDoneEvent")

		status := routine.EXIT_SUCC
		if rtnDoneEvtPb.Status == eventpb.RoutineDoneEvent_FAIL {
			status = routine.EXIT_FAIL
		}

		if rtnDoneEvtPb.WithData {
			if status == routine.EXIT_SUCC {
				stub.NoticeRtnData(rtnDoneEvtPb.RtnId, status, rtnDoneEvtPb.Result, "")
			} else {
				stub.NoticeRtnData(rtnDoneEvtPb.RtnId, status, nil, *rtnDoneEvtPb.Error)
			}

		} else {
			stub.NoticeRtnDone(rtnDoneEvtPb.RtnId, status)
		}
	}
}
