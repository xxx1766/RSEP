package processor

import (
	"github.com/ict/tide/pkg/routine"
)

// Processor represents a processor, including logicres and physicres.
type Processor struct {
	Id string

	LogicRes  *routine.Resource
	PhysicRes *PhysicResource

	HighPriority bool
}

// NewProc creates a new processor.
func NewProc(id string, logRes *routine.Resource, physRes *PhysicResource, prio bool) *Processor {
	return &Processor{
		Id:           id,
		LogicRes:     logRes,
		PhysicRes:    physRes,
		HighPriority: prio,
	}
}
