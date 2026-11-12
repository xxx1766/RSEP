package cloudhdlr

import (
	"context"
	"time"

	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/cmnct"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/event"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/routine"
	"github.com/ict/tide/pkg/server/handlers/clsstathdlr"
	"github.com/ict/tide/pkg/server/handlers/stubmgrhdlr"
	"github.com/ict/tide/pkg/stubmgr"
	"github.com/ict/tide/proto/eventpb"
	"github.com/pkg/errors"
	"github.com/sirupsen/logrus"
	"google.golang.org/protobuf/proto"
)

func RegisterEventHandler(evtDispr event.EventDispatcher, stubm *stubmgr.StubManager) {
	evtDispr.RegisterHandler(interfaces.NodeNewEvt, clsstathdlr.NodeNewHdlr())
	evtDispr.RegisterHandler(interfaces.AppNewEvt, appNewHdlr(evtDispr, stubm))
	evtDispr.RegisterHandler(interfaces.AppStopEvt, stubmgrhdlr.AppStopHdlr(stubm))
}

func appNewHdlr(evtDispr event.EventDispatcher, stubm *stubmgr.StubManager) event.EventHandler {
	cs := clsstat.Singleton()
	return func(evt *event.Event) {
		appNewEvtPb := &eventpb.AppNewEvent{}
		if err := proto.Unmarshal(evt.Content, appNewEvtPb); err != nil {
			logrus.Error("ClsStatHdlr: failed to unmarshal AppNewEvent, ", err)
			return
		}

		cs.AddApp(&clsstat.AddAppOpts{
			AppId:     appNewEvtPb.AppId,
			ImageName: appNewEvtPb.ImageName,
			Commands:  appNewEvtPb.Commands,
		})
		buildApp(evtDispr, stubm, appNewEvtPb.AppId, appNewEvtPb.ImageName, appNewEvtPb.Commands)
	}
}

func buildApp(evtDispr event.EventDispatcher, stubm *stubmgr.StubManager, appId, imgName string, commands []string) {
	opts := &stubmgr.CreateStubOpts{
		AppId:      appId,
		ImgName:    imgName,
		Commands:   commands,
		Mode:       interfaces.StubFn,
		ResReq:     dev.GetStubResource(),
		InitExecNo: 2,
	}
	st, err := stubm.CreateStub(opts)
	if err != nil {
		err = errors.Wrap(err, "StubMgrHdlr: failed to create stub")
		logrus.Fatal(err)
	}
	RegisterStubHandlers(evtDispr, st)
}

func RegisterStubHandlers(evtDispr event.EventDispatcher, st interfaces.Stub) {
	evtDispr.RegisterHandler(interfaces.CldRtnSbmEvt(st.AppId()), rtnSbmHdlr(st))
	evtDispr.RegisterHandler(interfaces.CldRtnWaitEvt(st.AppId()), rtnWaitHdlr(st))
}

func rtnSbmHdlr(stub interfaces.Stub) event.EventHandler {
	return func(evt *event.Event) {
		rtnSbmEvt := &eventpb.RoutineSubmitEvent{}
		if err := proto.Unmarshal(evt.Content, rtnSbmEvt); err != nil {
			logrus.WithField("StubId", stub.Id()).Error("StubHdrl: failed to unmarshal routine submit event, ", err)
			return
		}

		rtn := routine.FromSubmitEvent(rtnSbmEvt, dev.GetReqResource())
		stub.CloudSubmitRoutine(rtn)
	}
}

func rtnWaitHdlr(stub interfaces.Stub) event.EventHandler {
	return func(reqEvt *event.Event) {
		rtnWGEvtPb := &eventpb.RoutineWaitGetEvent{}
		if err := proto.Unmarshal(reqEvt.Content, rtnWGEvtPb); err != nil {
			logrus.WithField("StubId", stub.Id()).Error("StubHdrl: failed to unmarshal RoutineWaitEvent, ", err)
			return
		}

		logrus.WithFields(logrus.Fields{"RoutineId": rtnWGEvtPb.RtnId, "StubId": stub.Id()}).
			Debug("StubHdlr: start waitting routine done")

		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		if clsstat.Singleton().GetSelf().Role == clsstat.CLOUD && !stub.WaitRoutineReach(ctx, rtnWGEvtPb.RtnId) {
			logrus.WithFields(logrus.Fields{"RequestStubId": rtnWGEvtPb.ReqSid, "RoutineId": rtnWGEvtPb.RtnId}).
				Fatal("didn't wait for this routine")
		}
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
			"StubId":      stub.Id(),
			"RoutineId":   rtn.Id,
			"RequestStub": rtnWGEvtPb.ReqSid,
			"RequestNode": reqEvt.From,
		}).Debug("StubHdlr: start sending RoutineDoneEvent")
		evt := &event.Event{Type: interfaces.RtnDoneEvt(rtnWGEvtPb.ReqSid), Content: content}
		if err := cmnct.Singleton().SendToNode(reqEvt.From, evt); err != nil {
			logrus.WithFields(logrus.Fields{
				"StubId":    stub.Id(),
				"RoutineId": rtn.Id,
				"TargetSid": rtnWGEvtPb.ReqSid,
			}).Error("StubHdrl: send RoutineDoneEvent failed")
		}
	}
}
