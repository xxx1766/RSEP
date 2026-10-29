package cmnct

import (
	"context"
	"sync"
	"time"

	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/event"
	"github.com/ict/tide/proto/eventpb"
	"github.com/pkg/errors"
	"github.com/sirupsen/logrus"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

var cmnctr *evtCommunicator

type clientCh struct {
	ch    chan eventpb.EventServiceClient
	total int64
	used  int64

	mu sync.Mutex
}

type evtCommunicator struct {
	addr2cliCh map[string]*clientCh
	mu         sync.RWMutex

	clsStat  *clsstat.ClusterState
	selfId   string
	selfAddr string
	evtDispr event.EventDispatcher

	stubSyncEvtCache sync.Map
	cacheMu          sync.RWMutex
}

func Init(selfId, selfAddr string, evtDispr event.EventDispatcher) {
	cmnctr = &evtCommunicator{
		addr2cliCh: make(map[string]*clientCh),
		clsStat:    clsstat.Singleton(),
		selfId:     selfId,
		selfAddr:   selfAddr,
		evtDispr:   evtDispr,
	}
}

func Singleton() EventCommunicator {
	return cmnctr
}

func (ec *evtCommunicator) CacheStubSyncEvt(stubId string, evt *event.Event) error {
	ec.cacheMu.RLock()
	defer ec.cacheMu.RUnlock()
	ec.stubSyncEvtCache.Store(stubId, evt)
	return nil
}

func (ec *evtCommunicator) NodeStateSync(evt *event.Event) error {
	evts := []*event.Event{evt}
	ec.cacheMu.Lock()
	ec.stubSyncEvtCache.Range(func(key, value any) bool {
		evt := value.(*event.Event)
		evts = append(evts, evt)
		return true
	})
	ec.stubSyncEvtCache = sync.Map{}
	ec.cacheMu.Unlock()
	return ec.BroadcastToEdgThg(evts...)
}

// BroadcastToAllNode sends events to all nodes in the cluster.
func (ec *evtCommunicator) BroadcastToAllNode(evts ...*event.Event) error {
	var err error
	ndStats := ec.clsStat.ListNode()
	for _, ndStat := range ndStats {
		e := ec.SendToAddress(ndStat.Address, evts...)
		if err == nil && e != nil {
			err = e
		}
	}
	return err
}

// BroadcastToEdgThg sends events to all edge and thing nodes in the cluster.
func (ec *evtCommunicator) BroadcastToEdgThg(evts ...*event.Event) error {
	var err error
	ndStats := ec.clsStat.ListNode()
	for _, ndStat := range ndStats {
		if ndStat.Role == clsstat.CLOUD {
			continue
		}
		e := ec.SendToAddress(ndStat.Address, evts...)
		if err == nil && e != nil {
			err = e
		}
	}
	return err
}

// SendToNode sends events to a certain node in the cluster.
func (ec *evtCommunicator) SendToNode(nid string, evt *event.Event) error {
	ndStat := ec.clsStat.GetNode(nid)

	logrus.WithFields(logrus.Fields{"NodeId": nid}).Debugf("Communicator: Send to certain address %v, %v", ndStat.Address, evt)

	// logrus.Debugf("clsStat is %v", ec.clsStat)

	// logrus.WithFields(logrus.Fields{"nodes": ec.clsStat.ListNode(), "stubs": ec.clsStat.ListStub()}).Debug("nodes and stubs")

	return ec.SendToAddress(ndStat.Address, evt)
}

// SendToAddress sends events to a certain address.
func (ec *evtCommunicator) SendToAddress(addr string, evts ...*event.Event) error {
	logrus.WithFields(logrus.Fields{"TargetAddr": addr, "EvtNum": len(evts)}).Trace("send some events to destination")

	// if send to itsely, just dispatch
	if ec.selfAddr == addr {
		for _, evt := range evts {
			// Broadcasts share the input event with asynchronous readers.
			copyEvt := *evt
			copyEvt.From = ec.selfId
			go ec.evtDispr.DispatchEvent(&copyEvt)
		}
		return nil
	}

	evtsPb := &eventpb.EventList{Events: make([]*eventpb.Event, 0, len(evts))}
	for _, evt := range evts {
		copyEvt := *evt
		copyEvt.From = ec.selfId
		evtsPb.Events = append(evtsPb.Events, event.EventToPb(&copyEvt))
	}

	cli, err := ec.getClient(addr)
	if err != nil {
		logrus.WithField("Addr", addr).Warn("Cmnct: failed to get client")
		return err
	}
	defer ec.retClient(addr, cli)
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if _, err := cli.SendEvent(ctx, evtsPb); err != nil {
		return errors.Wrapf(err, "EvtCmnct: failed to send event to node:%s", addr)
	}
	return nil
}

// SendToStub sends events to a certain stub in the cluster.
func (ec *evtCommunicator) SendToStub(sid string, evt *event.Event) error {

	logrus.Debugf("clsStat is %v", ec.clsStat)

	logrus.WithFields(logrus.Fields{"nodes": ec.clsStat.ListNode(), "stubs": ec.clsStat.ListStub()}).Debug("nodes and stubs")

	nid := ec.clsStat.GetStub(sid).NodeId

	logrus.WithFields(logrus.Fields{"originStubId": sid, "Idx": ec.clsStat.GetIdx(sid), "currentStubId": ec.clsStat.GetStub(sid).Id}).Debug("Communicator: compare Stub")

	logrus.WithFields(logrus.Fields{"StubId": sid, "NodeId": nid}).Debugf("Communicator: Send to certain stub, %v", evt)

	return ec.SendToNode(nid, evt)
}

func (ec *evtCommunicator) retClient(addr string, cli eventpb.EventServiceClient) {
	cliCh := ec.addr2cliCh[addr]
	cliCh.mu.Lock()
	defer cliCh.mu.Unlock()
	cliCh.ch <- cli
	cliCh.used--
}

func (ec *evtCommunicator) getClient(addr string) (cli eventpb.EventServiceClient, err error) {
	var cliCh *clientCh
	var ok bool

	ec.mu.RLock()
	cliCh, ok = ec.addr2cliCh[addr]
	ec.mu.RUnlock()
	if !ok {
		cliCh = &clientCh{ch: make(chan eventpb.EventServiceClient, 100000)}
		ec.mu.Lock()
		ec.addr2cliCh[addr] = cliCh
		ec.mu.Unlock()
	}

	cliCh.mu.Lock()
	defer cliCh.mu.Unlock()
	if cliCh.used < cliCh.total {
		cliCh.used++
		cli = <-cliCh.ch
		return
	}
	if cli, err = createNewCli(addr); err == nil {
		cliCh.used++
		cliCh.total++
	}
	return
}

func createNewCli(addr string) (eventpb.EventServiceClient, error) {
	conn, err := grpc.Dial(addr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		logrus.Debug("Cmnct: failed to connect server, ", err)
		return nil, err
	}
	return eventpb.NewEventServiceClient(conn), nil
}
