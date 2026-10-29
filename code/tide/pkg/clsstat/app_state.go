package clsstat

import "sync"

// AppState represents the state of an application.
type AppState struct {
	AppId     string
	ImageName string
	Commands  []string

	Stubs      []*StubState
	newStubSig *sync.Cond
	mu         sync.Mutex
}

// AddAppOpts represents the options for adding an application.
type AddAppOpts struct {
	AppId     string
	ImageName string
	Commands  []string
}

// ApplicationId represents the Id of App.
type ApplicationId string

// AddStub adds a new stub to the application.
func (as *AppState) AddStub(ss *StubState) {
	as.mu.Lock()
	defer as.mu.Unlock()

	as.Stubs = append(as.Stubs, ss)
	as.newStubSig.L.Lock()
	as.newStubSig.Broadcast()
	as.newStubSig.L.Unlock()
}

// RmStub removes a stub from the application.
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

// WaitNewStub waits for a new stub to be added to the application.
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
