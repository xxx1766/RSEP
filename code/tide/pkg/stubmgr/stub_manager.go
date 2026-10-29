package stubmgr

import (
	"context"
	"sync"

	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/procmgr"
	"github.com/ict/tide/pkg/routine"
	"github.com/ict/tide/pkg/stub"
	"github.com/ict/tide/pkg/stub/execpool"
	"github.com/sirupsen/logrus"
)

// StubManager manages the stubs.
type StubManager struct {
	execPools sync.Map
	stubs     sync.Map
	procM     *procmgr.ProcessorManager
}

// NewStubMgr creates a new StubManager.
func NewStubMgr(pm *procmgr.ProcessorManager) *StubManager {
	return &StubManager{
		procM: pm,
	}
}

// Close closes the StubManager.
func (sm *StubManager) Close() error {
	var err error
	sm.stubs.Range(func(key, value any) bool {
		st := value.(interfaces.Stub)
		if e := st.Close(); e != nil {
			logrus.WithField("StubId", st.Id()).Error("StubMgr: failed to close stub, ", e)
			err = e
		}
		return true
	})
	sm.execPools.Range(func(key, value any) bool {
		execPool := value.(execpool.ExecutorPool)
		if e := execPool.Close(); e != nil {
			logrus.WithField("AppId", key).Error("StubMgr: failed to close exec pool, ", e)
			err = e
		}
		return true
	})
	return err
}

// CreateStubOpts is the options for creating a stub.
type CreateStubOpts struct {
	AppId      string
	ImgName    string
	VolumePair string
	Commands   []string
	Mode       interfaces.StubMode
	ResReq     *routine.Resource
	InitExecNo int
}

// CreateStub creates a new stub.
func (sm *StubManager) CreateStub(opts *CreateStubOpts) (interfaces.Stub, error) {
	ctx, cancel := context.WithCancel(context.Background())
	ctx = context.WithValue(ctx, clsstat.ApplicationId("appid"), opts.AppId)
	defer cancel()
	// proc, err := sm.procM.Apply(context.Background(), opts.ResReq)
	proc, err := sm.procM.Apply(ctx, opts.ResReq)

	if err != nil {
		logrus.WithField("AppId", opts.AppId).Warn("StubMgr: failed to apply processor, ", err)
		return nil, err
	}

	// cs := clsstat.Singleton()
	// as := cs.GetApp(opts.AppId)
	// if opts.Mode == interfaces.StubFn {
	// 	if as == nil {
	// 		logrus.WithField("AppId", opts.AppId).Debug("App is not existed anymore!")
	// 		return nil, nil
	// 	}
	// }

	if proc == nil {
		return nil, nil
	}

	var execPool execpool.ExecutorPool
	if opts.Mode == interfaces.StubFn {
		defExecPool := execpool.New(opts.ImgName, opts.VolumePair, opts.Commands, opts.Mode)
		val, _ := sm.execPools.LoadOrStore(opts.AppId, defExecPool)
		execPool = val.(execpool.ExecutorPool)
		execPool.IncInstance(opts.InitExecNo)
	}

	stOpts := &stub.CreateStubOpts{
		AppId:      opts.AppId,
		ImgName:    opts.ImgName,
		VolumePair: opts.VolumePair,
		Commands:   opts.Commands,
		Mode:       opts.Mode,
		ResReq:     opts.ResReq,
		InitExecNo: opts.InitExecNo,
		ExecPool:   execPool,
		Proc:       proc,
		ProcM:      sm.procM,
	}
	st := stub.NewStub(stOpts)
	sm.stubs.Store(st.Id(), st)
	return st, nil
}

// StopApp stops the stubs of the app.
func (sm *StubManager) StopApp(aid string) {
	var wg sync.WaitGroup
	sm.stubs.Range(func(key, value any) bool {
		st := value.(interfaces.Stub)
		if st.AppId() != aid {
			return true
		}

		wg.Add(1)
		go func(st interfaces.Stub) {
			defer wg.Done()
			sm.stubs.Delete(st.Id())
			if err := st.Close(); err != nil {
				logrus.WithField("StubId", st.Id()).Error("StubMgr: failed to close stub, ", err)
			}
		}(st)
		return true
	})
	wg.Wait()

	value, ok := sm.execPools.LoadAndDelete(aid)
	if !ok {
		return
	}
	execPool := value.(execpool.ExecutorPool)
	if err := execPool.Close(); err != nil {
		logrus.WithField("AppId", aid).Error("StubMgr: failed to close executor pool, ", err)
	}
}

// StopStub stops the stub.
func (sm *StubManager) StopStub(sid string) {
	value, ok := sm.stubs.LoadAndDelete(sid)
	if !ok {
		return
	}

	st := value.(interfaces.Stub)
	if err := st.Close(); err != nil {
		logrus.WithField("StubId", st.Id()).Error("StubMgr: failed to close stub, ", err)
	}
}
