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

type Routine struct {
	Id string

	FnName string
	Args   []byte
	ResReq *Resource

	RequireLocalCapacity bool
	LocalNodeID          string

	result []byte
	errStr string

	status      Status
	SubmitTime  int64
	InitSid     string
	recvId      string
	recvByCloud bool
	recvSig     chan struct{}
	doneSig     chan struct{}
	dataSig     chan struct{}

	mu sync.Mutex
}

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

func (r *Routine) WaitReceive() <-chan struct{} {
	return r.recvSig
}

func (r *Routine) WaitDone() <-chan struct{} {
	return r.doneSig
}

func (r *Routine) WaitData() <-chan struct{} {
	return r.dataSig
}

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

func (r *Routine) Succ() {
	r.exit(EXIT_SUCC)
}

func (r *Routine) SetResult(res []byte) {
	logrus.WithFields(logrus.Fields{"RoutineId": r.Id, "result": res}).Debug("Routine: start setting result")
	defer logrus.WithField("RoutineId", r.Id).Debug("Routine: finish setting result")
	r.mu.Lock()
	defer r.mu.Unlock()
	r.result = res
	close(r.dataSig)
}

func (r *Routine) ClearArgs() {
	r.Args = nil
}

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

func (r *Routine) SetError(err string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.errStr = err
	close(r.dataSig)
}

func (r *Routine) Status() Status {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.status
}

func (r *Routine) Receiver() (bool, string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.recvByCloud, r.recvId
}

func (r *Routine) Result() []byte {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.result
}

func (r *Routine) Error() string {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.errStr
}
