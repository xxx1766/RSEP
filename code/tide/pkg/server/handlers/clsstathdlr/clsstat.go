package clsstathdlr

import (
	"time"

	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/cmnct"
	"github.com/ict/tide/pkg/event"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/routine"
	"github.com/ict/tide/proto/commpb"
	"github.com/ict/tide/proto/eventpb"
	"github.com/sirupsen/logrus"
	"google.golang.org/protobuf/proto"
)

func RegisterEventHandler(evtDispr event.EventDispatcher) {
	evtDispr.RegisterHandler(interfaces.NodeNewEvt, NodeNewHdlr())
	evtDispr.RegisterHandler(interfaces.NodeSyncEvt, nodeSyncHdlr())

	evtDispr.RegisterHandler(interfaces.StubNewEvt, stubNewHdlr())
	evtDispr.RegisterHandler(interfaces.StubSyncEvt, stubSyncHdlr())

	evtDispr.RegisterHandler(interfaces.AppNewEvt, appNewHdlr())
}

func NodeNewHdlr() event.EventHandler {
	cs := clsstat.Singleton()
	self := cs.GetSelf() // 保证 clsstat Init 在此之前

	return func(evt *event.Event) {
		ndNewEvt := &eventpb.NodeNewEvent{}
		if err := proto.Unmarshal(evt.Content, ndNewEvt); err != nil {
			logrus.Error("ClsStatHdlr: failed to unmarshal NodeNewEvent, ", err)
			return
		}

		logrus.WithField("NodeId", ndNewEvt.NodeId).Info("ClsStatHdlr: receive a NodeNewEvent")
		if cs.ContainNode(ndNewEvt.NodeId) {
			logrus.WithField("NodeId", ndNewEvt.NodeId).Warn("ClsStatHdlr: already join")
			return
		}

		opts := &clsstat.AddNodeOpts{
			Nid:      ndNewEvt.NodeId,
			Address:  ndNewEvt.Address,
			Role:     clsstat.NodeRole(ndNewEvt.Role),
			ResTotal: &routine.Resource{CpuNum: int(ndNewEvt.TotalResource.CpuNum), GpuNum: int(ndNewEvt.TotalResource.GpuNum)},
		}
		newNdStat := cs.AddNode(opts)

		if ndNewEvt.IsReply {
			return
		}

		ndNewEvt = &eventpb.NodeNewEvent{
			NodeId:  self.Id,
			Address: self.Address,
			Role:    int32(self.Role),
			TotalResource: &commpb.Resource{
				CpuNum: int32(self.ResTotal.CpuNum),
				GpuNum: int32(self.ResTotal.GpuNum),
			},
			IsReply: true,
		}
		content, err := proto.Marshal(ndNewEvt)
		if err != nil {
			logrus.Error("ClsStatHdlr: failed to marshal NodeNewEvent, ", err)
			return
		}
		evt = &event.Event{Type: interfaces.NodeNewEvt, Content: content}
		if err := cmnct.Singleton().SendToAddress(newNdStat.Address, evt); err != nil {
			logrus.WithField("Addr", newNdStat.Address).Error("ClsStatHdlr: faild to send NodeNewEvent")
		}
	}
}

func nodeSyncHdlr() event.EventHandler {
	cs := clsstat.Singleton()
	return func(evt *event.Event) {
		ndSyncEvtPb := &eventpb.NodeSyncEvent{}
		if err := proto.Unmarshal(evt.Content, ndSyncEvtPb); err != nil {
			logrus.Error("ClsStatHdlr: failed to unmarshal NodeSyncEvent, ", err)
			return
		}

		opts := &clsstat.UpdateNodeOpts{
			Nid: evt.From,
			ResRemain: &routine.Resource{
				CpuNum: int(ndSyncEvtPb.RemainResource.CpuNum),
				GpuNum: int(ndSyncEvtPb.RemainResource.GpuNum),
			},
		}
		cs.UpdateNode(opts)
	}
}

func stubNewHdlr() event.EventHandler {
	cs := clsstat.Singleton()
	return func(evt *event.Event) {
		stNewEvt := &eventpb.StubNewEvent{}
		if err := proto.Unmarshal(evt.Content, stNewEvt); err != nil {
			logrus.Error("ClsStatHdlr: failed to unmarshal StubNewEvent, ", err)
			return
		}
		logrus.WithFields(logrus.Fields{
			"StubId": stNewEvt.StubId,
			"AppId":  stNewEvt.AppId,
			"NodeId": evt.From,
		}).Debug("ClsStatHdlr: receive a StubNewEvent")

		var mode interfaces.StubMode
		if stNewEvt.Mode == eventpb.StubMode_ENTRY {
			mode = interfaces.StubEntry
		} else {
			mode = interfaces.StubFn
		}

		// TODO: Resource 可配置
		opts := &clsstat.AddStubOpts{
			StubId:     stNewEvt.StubId,
			Mode:       mode,
			AppId:      stNewEvt.AppId,
			NodeId:     evt.From,
			CreateTime: time.Unix(0, stNewEvt.CreateTime),
		}
		cs.AddStub(opts)
	}
}

func stubSyncHdlr() event.EventHandler {
	cs := clsstat.Singleton()
	return func(evt *event.Event) {
		stSyncEvtPb := &eventpb.StubSyncEvent{}
		if err := proto.Unmarshal(evt.Content, stSyncEvtPb); err != nil {
			logrus.Error("ClsStatHdlr: failed to unmarshal StubSyncEvent, ", err)
			return
		}
		cs.UpdateStub(stSyncEvtPb.StubId,
			clsstat.SyncEstWaitTime(stSyncEvtPb.EstWaitTime, time.Unix(0, stSyncEvtPb.UpdateTime)))
	}
}

func appNewHdlr() event.EventHandler {
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
	}
}
