package procmgr

import (
	"time"

	"github.com/ict/tide/pkg/cmnct"
	"github.com/ict/tide/pkg/event"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/routine"
	"github.com/ict/tide/proto/commpb"
	"github.com/ict/tide/proto/eventpb"
	"github.com/sirupsen/logrus"
	"google.golang.org/protobuf/proto"
)

// ProcessorManager starts the heartbeat loop.
func (pm *ProcessorManager) StartHeartbeat() {
	logrus.Info("ProcMgr: enter heartbeat loop")
	cmnctr := cmnct.Singleton()
	go func() {
		for {
			select {
			case <-pm.done:
				return
			default:
			}

			pm.heartbeat(cmnctr)
			// TODO: 时间可配置化
			time.Sleep(5 * time.Millisecond)
		}
	}()
}

func (pm *ProcessorManager) heartbeat(cmnctr cmnct.EventCommunicator) {
	pm.mu.Lock()
	resPb := resourceToPb(pm.highPrioResRemain)
	pm.mu.Unlock()
	ndStatPb := &eventpb.NodeSyncEvent{RemainResource: resPb}
	content, err := proto.Marshal(ndStatPb)
	if err != nil {
		logrus.Error("ProcMgr: failed to marshal StateSyncEvent, ", err)
		return
	}

	evt := &event.Event{
		Type:    interfaces.NodeSyncEvt,
		Content: content,
	}
	cmnctr.NodeStateSync(evt)
}

func resourceToPb(res *routine.Resource) *commpb.Resource {
	return &commpb.Resource{
		CpuNum: int32(res.CpuNum),
		GpuNum: int32(res.GpuNum),
	}
}
