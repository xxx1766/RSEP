package clsstat

import "sync"

type AppState struct {
	AppId     string
	ImageName string
	Commands  []string

	Stubs      []*StubState
	newStubSig *sync.Cond
	mu         sync.Mutex
}

type AddAppOpts struct {
	AppId     string
	ImageName string
	Commands  []string
}

type ApplicationId string

func (as *AppState) AddStub(ss *StubState) {
	as.mu.Lock()
	defer as.mu.Unlock()

	as.Stubs = append(as.Stubs, ss)
	as.newStubSig.L.Lock()
	as.newStubSig.Broadcast()
	as.newStubSig.L.Unlock()
}

func (as *AppState) RmStub(ss *StubState) {
	as.mu.Lock()
	defer as.mu.Unlock()

	for i, stStat := range as.Stubs {
		if stStat == ss {
			as.Stubs = append(as.Stubs[:i], as.Stubs[i+1:]...)
			break
		}
	}
}

func (as *AppState) WaitNewStub() <-chan struct{} {
	ch := make(chan struct{})
	go func() {
		as.newStubSig.L.Lock()
		as.newStubSig.Wait()
		as.newStubSig.L.Unlock()
		close(ch)
	}()
	return ch
}
