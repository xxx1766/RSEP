package clsstat

import (
	"sync"
	"time"

	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/routine"
	"github.com/sirupsen/logrus"
)

type StubStatus uint8

const (
	STUB_RUNNING StubStatus = iota
	STUB_SLEEPED
	STUB_STOPPED
)

// StubState represents the state of a stub.
type StubState struct {
	Id          string
	Status      StubStatus
	Mode        interfaces.StubMode
	estWaitTime uint64
	Resource    *routine.Resource

	AppId  string
	NodeId string

	CreateTime time.Time
	UpdateTime time.Time // when the estWaitTime was updated on the device the stub was on

	mu sync.RWMutex
}

// EstWaitTime returns the estimated waiting time of the stub.
// FIXME: concurrent conflicts
// 根据当前负载压力估计预计结束时间
func (ss *StubState) EstEndTime() uint64 {
	ss.mu.RLock()
	defer ss.mu.RUnlock()
	return uint64(ss.UpdateTime.UnixNano()) + ss.estWaitTime
}

func (ss *StubState) SchedulingState() (StubStatus, uint64) {
	ss.mu.RLock()
	defer ss.mu.RUnlock()
	return ss.Status, uint64(ss.UpdateTime.UnixNano()) + ss.estWaitTime
}

// AddStubOpts represents the options for adding a stub.
type AddStubOpts struct {
	StubId     string
	Mode       interfaces.StubMode
	AppId      string
	NodeId     string
	CreateTime time.Time
}

// AddStub adds a new stub to the cluster.
func (cs *ClusterState) AddStub(opts *AddStubOpts) {
	ss := &StubState{
		Id:          opts.StubId,
		Status:      STUB_RUNNING,
		Mode:        opts.Mode,
		estWaitTime: 0,
		Resource:    dev.GetStubResource(),
		AppId:       opts.AppId,
		NodeId:      opts.NodeId,
		CreateTime:  opts.CreateTime,
		UpdateTime:  time.Now(),
	}

	cs.stubMu.Lock()
	defer cs.stubMu.Unlock()
	cs.stubs = append(cs.stubs, ss)
	cs.sid2idx[ss.Id] = len(cs.stubs) - 1
	as := cs.GetApp(ss.AppId)
	//TODO: 在创建 stub 的时候发现 app 已经结束就不再创建 stub
	// for as == nil {
	// 	logrus.Error("ClsStat: No such app state, ", ss.AppId)
	// 	time.Sleep(time.Second)
	// 	as = cs.GetApp(ss.AppId)
	// }
	// as.AddStub(ss)
	if as != nil {
		as.AddStub(ss)
	}
}

type stubUpdateOpt func(stStat *StubState)

// SyncEstWaitTime updates the estimated waiting time and updatetime of the stub.
func SyncEstWaitTime(ewt uint64, updateTime time.Time) stubUpdateOpt {
	return func(stStat *StubState) {
		stStat.mu.Lock()
		defer stStat.mu.Unlock()
		stStat.estWaitTime = ewt
		stStat.UpdateTime = updateTime
	}
}

// UpdateStub updates the stub with the given stubUpdateOpt.
// TODO: update stub state
func (cs *ClusterState) UpdateStub(sid string, o stubUpdateOpt) {
	cs.stubMu.RLock()
	idx, ok := cs.sid2idx[sid]
	if !ok {
		cs.stubMu.RUnlock()
		// Cloud stubs and removed stubs may still publish state syncs, but
		// must not update the first edge stub through a missing map key.
		return
	}
	stStat := cs.stubs[idx]
	cs.stubMu.RUnlock()
	o(stStat)
}

// TryUpdateEstWaitTime tries to update the estimated waiting time of the stub.
func (cs *ClusterState) TryUpdateEstWaitTime(sid string, estExecTime uint64, endBefore uint64) bool {
	stStat := cs.GetStub(sid)
	stStat.mu.Lock()
	defer stStat.mu.Unlock()
	finish := uint64(stStat.UpdateTime.UnixNano()) + stStat.estWaitTime
	if now := uint64(time.Now().UnixNano()); finish < now {
		finish = now
	}
	if stStat.Status == STUB_RUNNING && finish+estExecTime <= endBefore {
		stStat.estWaitTime = finish + estExecTime - uint64(stStat.UpdateTime.UnixNano())
		return true
	}
	return false
}

// GetStub returns the stub with the given stub Id.
func (cs *ClusterState) GetStub(sid string) *StubState {
	cs.stubMu.RLock()
	defer cs.stubMu.RUnlock()
	idx := cs.sid2idx[sid]
	return cs.stubs[idx]
	// for _, st := range cs.stubs {
	// 	if st.Id == sid {
	// 		return st
	// 	}
	// }
	// logrus.Fatalf("ClsStat: No such stub state, %v", sid)
	// return nil
}

// RmStub removes a stub from the cluster.
func (cs *ClusterState) RmStub(sid string) {
	stStat := cs.GetStub(sid)
	stStat.mu.Lock()
	stStat.Status = STUB_STOPPED
	stStat.mu.Unlock()

	appStat := cs.GetApp(stStat.AppId)
	if appStat != nil {
		appStat.RmStub(stStat)
	}

	logrus.WithFields(logrus.Fields{"StubId": sid, "AppId": stStat.AppId, "sid2idx": cs.sid2idx}).Debug("ClsStat: remove stub")

	cs.stubMu.Lock()
	defer cs.stubMu.Unlock()
	delete(cs.sid2idx, sid)

	logrus.WithFields(logrus.Fields{"StubId": sid, "AppId": stStat.AppId, "sid2idx": cs.sid2idx}).Debug("ClsStat: remove stub over")

	// TODO: remove stStat from stubs
}
