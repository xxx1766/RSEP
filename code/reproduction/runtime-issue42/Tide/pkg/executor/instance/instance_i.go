package instance

import (
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/processor"
	"github.com/ict/tide/pkg/routine"
)

type Instance interface {
	interfaces.Closer

	Start() error
	Pause() error
	Resume() error

	SendRoutine(*routine.Routine) error
	SendEvt(*interfaces.Event) error

	AllocResource(*processor.PhysicResource) error
	WithdrawResource() error

	GetEventCh() <-chan *interfaces.Event
}

type Object struct {
	RefId string
	Data  []byte
}
