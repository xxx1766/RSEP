package clsstat

import (
	"sync"

	"github.com/ict/tide/pkg/routine"
)

type ClusterState struct {
	apps  map[string]*AppState
	appMu sync.RWMutex

	nodes   []*NodeState
	nid2idx map[string]int
	nodeMu  sync.RWMutex
	self    *NodeState

	stubs   []*StubState
	sid2idx map[string]int
	stubMu  sync.RWMutex
}

var clsStat *ClusterState

func init() {
	clsStat = &ClusterState{
		apps: make(map[string]*AppState),

		nodes:   make([]*NodeState, 0),
		nid2idx: make(map[string]int),

		stubs:   make([]*StubState, 0),
		sid2idx: make(map[string]int),
	}
}

func Init(selfId, selfAddr string, role NodeRole, resTotal *routine.Resource) {
	clsStat.self = clsStat.AddNode(&AddNodeOpts{
		Nid:      selfId,
		Address:  selfAddr,
		Role:     role,
		ResTotal: resTotal,
	})
}

func Singleton() *ClusterState {
	return clsStat
}

func (cs *ClusterState) GetApp(aid string) *AppState {
	cs.appMu.RLock()
	defer cs.appMu.RUnlock()
	if as, ok := cs.apps[aid]; ok {
		return as
	}
	return nil
}

func (cs *ClusterState) AddApp(opts *AddAppOpts) *AppState {
	appStat := &AppState{
		AppId:      opts.AppId,
		ImageName:  opts.ImageName,
		Commands:   opts.Commands,
		Stubs:      make([]*StubState, 0),
		newStubSig: sync.NewCond(&sync.Mutex{}),
	}

	cs.appMu.Lock()
	defer cs.appMu.Unlock()
	cs.apps[opts.AppId] = appStat
	return appStat
}

func (cs *ClusterState) RmApp(aid string) {
	cs.appMu.Lock()
	defer cs.appMu.Unlock()
	delete(cs.apps, aid)
}
