package routine

import (
	"sync"

	"github.com/sirupsen/logrus"
)

type Status uint8

const (
	SUBMITTED Status = iota
	PENDING
	RUNNING
	EXIT_SUCC
	EXIT_FAIL
)

// Routine represents a routine that is offloaded to a node or cloud.
type Routine struct {
	Id string

	FnName string
	Args   []byte
	ResReq *Resource

	// Immutable after submission; LocalNodeID remains the originating node.
	RequireLocalCapacity bool
	LocalNodeID          string

	result []byte
	errStr string

	status      Status
	SubmitTime  int64
	InitSid     string // The stub that initiated the offloading
	recvId      string // The stub or node that receive the offloading
	recvByCloud bool
	recvSig     chan struct{}
	doneSig     chan struct{}
	dataSig     chan struct{}

	mu sync.Mutex
}

// NewSubmittedRtn creates a new routine that is submitted to a node or cloud.
func NewSubmittedRtn(id, fnName string, args []byte, resReq *Resource) *Routine {
	return &Routine{
		Id: id,

		FnName: fnName,
		Args:   args,
		ResReq: resReq,

		status: SUBMITTED,

		recvSig: make(chan struct{}),
		doneSig: make(chan struct{}),
		dataSig: make(chan struct{}),
	}
}

// WaitReceive returns a channel that is closed when the routine is received by a node or cloud.
func (r *Routine) WaitReceive() <-chan struct{} {
	return r.recvSig
}

// WaitDone returns a channel that is closed when the routine is done.
func (r *Routine) WaitDone() <-chan struct{} {
	return r.doneSig
}

// WaitData returns a channel that is closed when the routine has data to return.
func (r *Routine) WaitData() <-chan struct{} {
	return r.dataSig
}

// SetReceiver sets the receiver of the routine.
func (r *Routine) SetReceiver(toCloud bool, recvId string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.recvByCloud = toCloud
	r.recvId = recvId
	if r.status != EXIT_SUCC && r.status != EXIT_FAIL {
		r.status = PENDING
	}
	select {
	case <-r.recvSig:
	default:
		close(r.recvSig)
	}
}

// Succ marks the routine as successful.
func (r *Routine) Succ() {
	r.exit(EXIT_SUCC)
}

// SetResult sets the result of the routine.
func (r *Routine) SetResult(res []byte) {
	// logrus.WithField("RoutineId", r.Id).Debug("Routine: start setting result")
	logrus.WithFields(logrus.Fields{"RoutineId": r.Id, "result": res}).Debug("Routine: start setting result")
	defer logrus.WithField("RoutineId", r.Id).Debug("Routine: finish setting result")
	r.mu.Lock()
	defer r.mu.Unlock()
	r.result = res
	close(r.dataSig)
}

// ClearArgs clears the arguments of the routine.
func (r *Routine) ClearArgs() {
	r.Args = nil
}

// Fail marks the routine as failed.
func (r *Routine) Fail() {
	r.exit(EXIT_FAIL)
}

func (r *Routine) exit(status Status) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.status == EXIT_FAIL || r.status == EXIT_SUCC {
		return
	}
	r.status = status
	close(r.doneSig)
}

// SetError sets the error message of the routine.
func (r *Routine) SetError(err string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.errStr = err
	close(r.dataSig)
}

// Status returns the status of the routine.
func (r *Routine) Status() Status {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.status
}

// Receiver returns the receiver of the routine.
func (r *Routine) Receiver() (bool, string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.recvByCloud, r.recvId
}

// Result returns the result of the routine.
func (r *Routine) Result() []byte {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.result
}

// Error returns the error message of the routine.
func (r *Routine) Error() string {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.errStr
}
