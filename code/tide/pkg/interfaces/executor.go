package interfaces

import (
	"fmt"
	"time"

	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/processor"
	"github.com/ict/tide/pkg/routine"
	"github.com/ict/tide/proto/execinspb"
	"github.com/sirupsen/logrus"
	"google.golang.org/protobuf/proto"
)

type Executor interface {
	Closer

	executorGetter

	BindRtn(*routine.Routine) error
	BindProc(*processor.Processor) error
	Execute() error
	SendEvt(evt *Event) error
	UnbindRtn() (*routine.Routine, error)
	UnbindProc() (*processor.Processor, error)
}

type executorGetter interface {
	Id() string

	GetRoutine() *routine.Routine
	GetProcessor() *processor.Processor

	GetEventChan() <-chan *Event
}

// The event stuct between executor and instance

type Event struct {
	Type     EventType
	WithData bool
	RefIds   []string
	Result   []byte
	Error    string
	Routine  *routine.Routine
	Status   routine.Status
}
type EventType uint8

const (
	WAIT_GET EventType = iota
	SUBMIT_ROUTINE
	EXIT
	INIT
)

func (e *Event) String() string {
	var typ string
	switch e.Type {
	case WAIT_GET:
		typ = "WAIT_GET"
	case SUBMIT_ROUTINE:
		typ = "SUBMIT_ROUTINE"
	case EXIT:
		typ = "EXIT"
	case INIT:
		typ = "INIT"
	}
	return fmt.Sprintf("Event{%s,}", typ)
}

func UnmarshalEvent(data []byte) (*Event, error) {
	evtPb := &execinspb.Event{}
	if err := proto.Unmarshal(data, evtPb); err != nil {
		return nil, err
	}

	evt := &Event{}
	switch evtPb.Type {
	case execinspb.Event_ROUTINE_SUBMIT:
		evt.Type = SUBMIT_ROUTINE
		rtnPb := evtPb.Routine
		// TODO: the resource requirement of routine
		resReq := dev.GetReqResource()
		evt.Routine = routine.NewSubmittedRtn(rtnPb.Id, rtnPb.FnName, rtnPb.Args, resReq)
		evt.Routine.SubmitTime = time.Now().UnixNano()

	case execinspb.Event_GET_WAIT:
		evt.Type = WAIT_GET
		evt.WithData = *evtPb.WithData
		evt.RefIds = evtPb.RefIds

	case execinspb.Event_EXIT:
		evt.Type = EXIT
		if *evtPb.Status == execinspb.Event_SUCC {
			evt.Status = routine.EXIT_SUCC
			evt.Result = evtPb.Result
		} else {
			evt.Status = routine.EXIT_FAIL
			evt.Error = *evtPb.Error
		}

	case execinspb.Event_INIT:
		evt.Type = INIT

	default:
		logrus.WithField("EvtType", evtPb.Type).Fatal("unknown event type")
	}
	return evt, nil
}

func MarshalEvent(evt *Event) ([]byte, error) {
	evtPb := &execinspb.Event{}
	switch evt.Type {
	case WAIT_GET:
		evtPb.Type = execinspb.Event_GET_WAIT
		evtPb.RefIds = evt.RefIds
		evtPb.WithData = &evt.WithData
		if evt.Status == routine.EXIT_SUCC {
			evtPb.Status = execinspb.Event_SUCC.Enum()
			if evt.WithData {
				evtPb.Result = evt.Result
			}
		} else {
			evtPb.Status = execinspb.Event_FAIL.Enum()
			if evt.WithData {
				evtPb.Error = &evt.Error
			}
		}

	case SUBMIT_ROUTINE:
		evtPb.Type = execinspb.Event_ROUTINE_SUBMIT
		evtPb.Routine = &execinspb.Routine{
			Id:     evt.Routine.Id,
			FnName: evt.Routine.FnName,
			Args:   evt.Routine.Args,
		}
	}

	return proto.Marshal(evtPb)
}
