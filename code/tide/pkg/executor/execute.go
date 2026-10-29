package executor

import (
	"github.com/ict/tide/pkg/interfaces"
	"github.com/pkg/errors"
	"github.com/sirupsen/logrus"
)

func (e *executor) Execute() error {
	if e.stubMode == interfaces.StubEntry {
		if e.proc == nil {
			logrus.Fatal("Exec: proc cannot be nil")
		}
		if err := e.ins.Start(); err != nil {
			return errors.Wrap(err, "Exec: failed to start instance")
		}

	} else {
		if e.routine == nil || e.proc == nil {
			logrus.Fatal("Exec: when executor tries to exeucte, the routine or proc is nil")
		}
		if err := e.ins.SendRoutine(e.routine); err != nil {
			return errors.Wrap(err, "Exec: send routine failed")
		}
	}
	return nil
}
