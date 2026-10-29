package interfaces

import (
	"context"

	"github.com/ict/tide/pkg/routine"
)

type Stub interface {
	Closer
	stubGetter

	SubmitRoutine(*routine.Routine)
	SetReceiver(rtnId string, toCloud bool, recvId string)
	WaitRoutineReach(ctx context.Context, rid string) bool
	WaitRtnDone(rid string) <-chan *routine.Routine
	NoticeRtnDone(rid string, status routine.Status)
	NoticeRtnData(rid string, status routine.Status, result []byte, err string)

	CloudSubmitRoutine(*routine.Routine)

	// EXP: 实验需要
	SubmitRoutineExp(rtn *routine.Routine)
}

type stubGetter interface {
	Id() string
	AppId() string
}

type StubMode uint8

const (
	StubEntry StubMode = iota
	StubFn
)

func (m StubMode) String() string {
	if m == StubEntry {
		return "entry"
	}
	return "fn"
}
