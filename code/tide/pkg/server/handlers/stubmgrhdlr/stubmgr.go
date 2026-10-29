package stubmgrhdlr

import (
	"time"

	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/cmnct"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/event"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/server/handlers/stubhdlr"
	"github.com/ict/tide/pkg/stubmgr"
	"github.com/ict/tide/proto/eventpb"
	"github.com/pkg/errors"
	"github.com/sirupsen/logrus"
	"google.golang.org/protobuf/proto"
)

func RegisterEventHandler(evtDispr event.EventDispatcher, stubm *stubmgr.StubManager) {
	evtDispr.RegisterHandler(interfaces.StubCreateEvt, stubCreateHdlr(evtDispr, stubm))
	evtDispr.RegisterHandler(interfaces.AppStopEvt, AppStopHdlr(stubm))
	evtDispr.RegisterHandler(interfaces.StubStopEvt, stubStopHdlr(stubm))
}

func stubCreateHdlr(evtDispr event.EventDispatcher, stubm *stubmgr.StubManager) event.EventHandler {
	return func(evt *event.Event) {
		stCrtEvt := &eventpb.StubCreateEvent{}
		if err := proto.Unmarshal(evt.Content, stCrtEvt); err != nil {
			logrus.Error("StubMgr: failed to unmarshal stub create event, ", err)
			return
		}

		var err error
		var content []byte
		var st interfaces.Stub
		var appNewEvtPb *eventpb.AppNewEvent
		var stNewEvtPb *eventpb.StubNewEvent

		var mode interfaces.StubMode
		if stCrtEvt.Mode == eventpb.StubMode_ENTRY {
			mode = interfaces.StubEntry
		} else {
			mode = interfaces.StubFn
		}

		// create stub
		// TODO: ResReq 和 InitExecNo 可配置
		initExecNo := dev.GetToleranceTime()/dev.GetEstExecTime(clsstat.Singleton().GetSelf().Id) + 1
		opts := &stubmgr.CreateStubOpts{
			AppId:      stCrtEvt.AppId,
			ImgName:    stCrtEvt.ImageName,
			VolumePair: stCrtEvt.VolumePair,
			Commands:   stCrtEvt.Commands,
			Mode:       mode,
			ResReq:     dev.GetStubResource(),
			InitExecNo: initExecNo,
		}
		createTime := time.Now()
		st, err = stubm.CreateStub(opts)
		if err != nil {
			err = errors.Wrap(err, "StubMgrHdlr: failed to create stub")
			goto onerr
		}
		if st == nil {
			logrus.WithField("AppId", opts.AppId).Debug("App is not existed anymore. Stop creating stub!")
			return
		}
		stubhdlr.RegisterEventHandler(evtDispr, st)

		// An Application is started, notice to all nodes this message
		if mode == interfaces.StubEntry {
			appNewEvtPb = &eventpb.AppNewEvent{
				AppId:     stCrtEvt.AppId,
				ImageName: stCrtEvt.ImageName,
				Commands:  stCrtEvt.Commands,
			}
			content, err = proto.Marshal(appNewEvtPb)
			if err != nil {
				err = errors.Wrap(err, "StubMgrHdlr: failed to marshal AppNewEvent")
				goto onerr
			}
			evt = &event.Event{Type: interfaces.AppNewEvt, Content: content}

			logrus.WithField("AppId", appNewEvtPb.AppId).Debug("StubMgrHdlr: start broadcasting AppNewEvent")
			if err = cmnct.Singleton().BroadcastToAllNode(evt); err != nil {
				err = errors.Wrap(err, "StubMgrHdlr: failed to broadcast AppNewEvent")
				goto onerr
			}
		}

		// notice to all node that add this stub to their ClusterState
		stNewEvtPb = &eventpb.StubNewEvent{
			StubId:     st.Id(),
			AppId:      st.AppId(),
			Mode:       stCrtEvt.Mode,
			CreateTime: createTime.UnixNano(),
		}
		content, err = proto.Marshal(stNewEvtPb)
		if err != nil {
			err = errors.Wrap(err, "StubMgrHdlr: failed to marshal StubNewEvent")
			goto onerr
		}
		evt = &event.Event{Type: interfaces.StubNewEvt, Content: content}

		logrus.WithField("StubId", st.Id()).Debug("StubMgrHdlr: start broadcasting StubNewEvent")
		if err = cmnct.Singleton().BroadcastToEdgThg(evt); err != nil {
			err = errors.Wrap(err, "StubMgrHdlr: failed to broadcast StubNewEvent")
			goto onerr
		}
		return

	onerr:
		logrus.Error(err)
		if st != nil {
			st.Close()
		}
	}
}

func AppStopHdlr(stubm *stubmgr.StubManager) event.EventHandler {
	return func(evt *event.Event) {
		appStopEvtPb := &eventpb.AppStopEvent{}
		if err := proto.Unmarshal(evt.Content, appStopEvtPb); err != nil {
			logrus.Error("StubMgrHdlr: failed to unmarshal AppStopEvent, ", err)
			return
		}
		logrus.WithField("AppId", appStopEvtPb.AppId).Debug("AppStopHdlr try to stop app")
		stubm.StopApp(appStopEvtPb.AppId)
		clsstat.Singleton().RmApp(appStopEvtPb.AppId)
	}
}

func stubStopHdlr(stubm *stubmgr.StubManager) event.EventHandler {
	return func(evt *event.Event) {
		stStopEvtPb := &eventpb.StubStopEvent{}
		if err := proto.Unmarshal(evt.Content, stStopEvtPb); err != nil {
			logrus.Error("StubMgrHdlr: failed to unmarshal AppStopEvent, ", err)
			return
		}
		logrus.WithField("StubId", stStopEvtPb.StubId).Debug("StubMgrHdlr: find handler and start to stop stub")
		// if this stub is on this device, then try to stop this stub
		stubm.StopStub(stStopEvtPb.StubId)
		clsstat.Singleton().RmStub(stStopEvtPb.StubId)
	}
}
