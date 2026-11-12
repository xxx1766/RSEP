package executor

import (
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/processor"
	"github.com/ict/tide/pkg/routine"
)

func (e *executor) Id() string {
	return e.id
}

func (e *executor) GetRoutine() *routine.Routine {
	return e.routine
}

func (e *executor) GetProcessor() *processor.Processor {
	return e.proc
}

func (e *executor) GetEventChan() <-chan *interfaces.Event {
	return e.ins.GetEventCh()
}
