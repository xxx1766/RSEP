package sched

import (
	"context"
	"sync/atomic"
	"time"

	"github.com/ict/tide/pkg/c4"
	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/cmnct"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/event"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/routine"
	"github.com/ict/tide/proto/eventpb"
	"github.com/pkg/errors"
	"github.com/sirupsen/logrus"
	"google.golang.org/protobuf/proto"
)

type SortStubFn func(*routine.Routine, []*clsstat.StubState) []*clsstat.StubState

type SortNodeFn func(*routine.Routine, []*clsstat.NodeState) []*clsstat.NodeState

type Scheduler struct {
	appId    string
	imgName  string
	commands []string

	sortStubFn SortStubFn
	sortNodeFn SortNodeFn

	creating int32

	done chan struct{}
}

func New(appId, imgName string, commands []string) *Scheduler {
	schd := &Scheduler{
		appId:    appId,
		imgName:  imgName,
		commands: commands,

		sortStubFn: defaultSortStub,
		sortNodeFn: defaultSortNode,

		done: make(chan struct{}),
	}
	// go schd.Start()
	return schd
}

func (skdr *Scheduler) Close() {
	close(skdr.done)
}

func (skdr *Scheduler) Schedule(ctx context.Context, rtn *routine.Routine) (toCloud bool, cloudId string) {
	// skdr.pending <- rtn
	return skdr.scheduleOne(ctx, rtn)
}

func (skdr *Scheduler) scheduleOne(ctx context.Context, rtn *routine.Routine) (toCloud bool, cloudId string) {
	if c4.Check(rtn) != nil {
		return false, ""
	}
	logrus.WithField("RoutineId", rtn.Id).Debug("Sched: start scheduling this routine")
	clsStat := clsstat.Singleton()
	appStat := clsStat.GetApp(skdr.appId)

	select {
	case <-ctx.Done():
		return
	case <-skdr.done:
		return
	default:
	}

	var sortedStub []*clsstat.StubState

	// 如果不能满足，直接抛到云上
	endBefore := rtn.SubmitTime + int64(dev.GetToleranceTimeFor(rtn.FnName))

	logrus.WithFields(logrus.Fields{"RoutineId": rtn.Id, "SubmitTime": rtn.SubmitTime, "endBefore": endBefore, "Timenow": time.Now().UnixNano()}).Debug("Sched: judging where to submit")

	if endBefore < time.Now().UnixNano() {
		if toCloud, cloudId = skdr.trySubmitToCloud(ctx, rtn, clsStat); toCloud {
			goto ret
		}
	}

	// try to submit to suitable stub
	sortedStub = skdr.sortStubFn(rtn, appStat.Stubs)
	logrus.WithFields(logrus.Fields{"sortedStub": sortedStub}).Debug("Get sortedStub")
	for _, stStat := range sortedStub {
		logrus.WithFields(logrus.Fields{"stStat": stStat, "sortedStub": sortedStub, "stubid": stStat.Id, "stubmode": stStat.Mode.String(), "stubplace": stStat.NodeId}).Debug("Current Stub")
		if stStat.Mode == interfaces.StubEntry {
			continue
		}

		if !reserveStub(stStat, endBefore) {
			continue
		}

		if err := skdr.submitToStub(rtn, stStat); err != nil {
			// TODO: 减刚才加的时间
			logrus.Warn("Sched: failed to submit routine to stub, ", err)
			continue
		}

		toCloud, cloudId = false, stStat.NodeId
		goto ret
	}

	logrus.WithFields(logrus.Fields{"RoutineId": rtn.Id, "toCloud": toCloud, "cloudId": cloudId}).Debug("Sched: find somewhere to submit")

	if atomic.CompareAndSwapInt32(&skdr.creating, 0, 1) {
		go func() {
			select {
			case <-time.After(time.Second):
				atomic.StoreInt32(&skdr.creating, 0)
			case <-appStat.WaitNewStub():
				atomic.StoreInt32(&skdr.creating, 0)
			}
		}()

		logrus.Debug("test3")

		var err error
		sortedNode := skdr.sortNodeFn(rtn, clsStat.ListEdgeNode())
		logrus.WithFields(logrus.Fields{"sortedNode": sortedNode}).Debug("get sorted node")
		for _, ndStat := range sortedNode {
			logrus.WithFields(logrus.Fields{"ndStat": ndStat}).Debug("select sorted node")
			if err = skdr.requestNewStub(rtn, ndStat); err == nil {
				break
			}
		}
		if err != nil {
			atomic.StoreInt32(&skdr.creating, 0)
		}
	}

	// if there is no suitable stub, just throw to cloud
	toCloud, cloudId = skdr.trySubmitToCloud(ctx, rtn, clsStat)

ret:
	return
}

// Network time consumes deadline slack, but does not occupy the execution queue.
func reserveStub(st *clsstat.StubState, deadline int64) bool {
	budget := dev.GetNetworkBudget(st.NodeId).Nanoseconds()
	if deadline <= budget {
		return false
	}
	return clsstat.Singleton().TryUpdateEstWaitTime(st.Id, uint64(dev.GetEstExecTime(st.NodeId)), uint64(deadline-budget))
}

func (skdr *Scheduler) trySubmitToCloud(ctx context.Context, rtn *routine.Routine, clsStat *clsstat.ClusterState) (bool, string) {
	if c4.Check(rtn) != nil {
		return false, ""
	}
	logger := logrus.WithField("RoutineId", rtn.Id)
	logger.Debug("Sched: start submitting routine to cloud")

	clouds := clsStat.ListCloudNode()
	if len(clouds) == 0 {
		logger.Warn("there is no cloud node")
		return false, ""
	}
	cloud := clouds[0]
	logger = logger.WithField("NodeId", cloud.Id)

	var rtnSbmEvtPb *eventpb.RoutineSubmitEvent
	var content []byte
	var err error
	var evt *event.Event

	rtnSbmEvtPb = routine.SubmitEvent(rtn)
	content, err = proto.Marshal(rtnSbmEvtPb)
	if err != nil {
		err = errors.Wrap(err, "Sched: failed to marshal RoutineSubmitEvent, ")
		goto onerr
	}

	evt = &event.Event{
		Type:    interfaces.CldRtnSbmEvt(skdr.appId),
		Content: content,
	}
	if err = cmnct.Singleton().SendToNode(cloud.Id, evt); err != nil {
		err = errors.Wrap(err, "Sched: failed to send event to node")
		goto onerr
	}

	logger.Debug("Sched: finish submitting routine to cloud")
	return true, cloud.Id

onerr:
	logger.Error(err)
	return false, ""
}

func (skdr *Scheduler) submitToStub(rtn *routine.Routine, stStat *clsstat.StubState) error {
	if err := c4.Check(rtn); err != nil {
		return err
	}
	logger := logrus.WithFields(logrus.Fields{
		"RoutineId": rtn.Id,
		"StubId":    stStat.Id,
		"NodeId":    stStat.NodeId,
	})
	logger.Debug("Sched: start submitting routine to stub")
	defer logger.Debug("Sched: finish submitting routine to stub")

	var rtnSbmEvtPb *eventpb.RoutineSubmitEvent
	var content []byte
	var err error
	var evt *event.Event

	rtnSbmEvtPb = routine.SubmitEvent(rtn)
	content, err = proto.Marshal(rtnSbmEvtPb)
	if err != nil {
		err = errors.Wrap(err, "Sched: failed to marshal RoutineSubmitEvent, ")
		goto onerr
	}

	evt = &event.Event{
		Type:    interfaces.RtnSbmEvt(stStat.Id),
		Content: content,
	}
	if err = cmnct.Singleton().SendToNode(stStat.NodeId, evt); err != nil {
		err = errors.Wrap(err, "Sched: failed to send event to node")
		goto onerr
	}
	return nil

onerr:
	logger.Error(err)
	return err
}

func (skdr *Scheduler) requestNewStub(rtn *routine.Routine, ndStat *clsstat.NodeState) error {
	if err := c4.Check(rtn); err != nil {
		return err
	}
	logger := logrus.WithFields(logrus.Fields{
		"RoutineId": rtn.Id,
		"NodeId":    ndStat.Id,
	})
	logger.Debug("Sched: start requesting new stub")
	defer logger.Debug("Sched: finish requesting new stub")

	var stCrtEvtPb *eventpb.StubCreateEvent
	var content []byte
	var err error
	var evt *event.Event

	stCrtEvtPb = &eventpb.StubCreateEvent{
		AppId:     skdr.appId,
		ImageName: skdr.imgName,
		Commands:  skdr.commands,
		Mode:      eventpb.StubMode_FN,
	}
	content, err = proto.Marshal(stCrtEvtPb)
	if err != nil {
		err = errors.Wrap(err, "Sched: failed to marshal StubCreateEvent, ")
		goto onerr
	}

	evt = &event.Event{
		Type:    interfaces.StubCreateEvt,
		Content: content,
	}
	if err = cmnct.Singleton().SendToAddress(ndStat.Address, evt); err != nil {
		err = errors.Wrap(err, "Sched: failed to send event to node")
		goto onerr
	}
	return nil

onerr:
	logger.Error(err)
	return err
}
