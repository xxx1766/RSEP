package executor

import (
	"github.com/ict/tide/pkg/executor/instance"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/processor"
	"github.com/ict/tide/pkg/routine"
	"github.com/ict/tide/pkg/stringx"
	"github.com/pkg/errors"
	"github.com/sirupsen/logrus"
)

type executor struct {
	id string

	imgName    string
	volumePair string
	stubMode   interfaces.StubMode

	routine *routine.Routine
	proc    *processor.Processor

	ins instance.Instance
}

func New(imgName, volumePair string, commands []string, mode interfaces.StubMode) (interfaces.Executor, error) {
	id := stringx.GenerateId()
	logger := logrus.WithFields(logrus.Fields{
		"ExecutorId": id,
		"ImageName":  imgName,
		"VolumePair": volumePair,
	})
	logger.Debug("Exec: start creating")
	defer logger.Info("Exec: finish creating")

	newInsArgs := &instance.NewInstanceArgs{
		Id:         id,
		ImageName:  imgName,
		VolumePair: volumePair,
		Commands:   commands,
		Mode:       mode,
	}

	ins, err := instance.New(newInsArgs)
	if err != nil {
		return nil, errors.Wrap(err, "Exec: create instance failed")
	}
	exec := &executor{
		id:         id,
		imgName:    imgName,
		volumePair: volumePair,
		stubMode:   mode,
		ins:        ins,
	}

	if mode == interfaces.StubFn {
		if err := exec.ins.Start(); err != nil {
			logger.Error("Exec: failed to start instance, ", err)
			exec.Close()
			return nil, err
		}
		// if err := exec.ins.Pause(); err != nil {
		// 	logger.Error("Exec: failed to pause instance, ", err)
		// 	exec.Close()
		// 	return nil, err
		// }
	}

	return exec, nil
}

func (e *executor) Close() error {
	defer logrus.WithField("ExecutorId", e.id).Debug("Exec: destory one")
	if err := e.ins.Close(); err != nil {
		return err
	}
	return nil
}

func (e *executor) BindProc(p *processor.Processor) error {
	if e.proc != nil {
		logrus.Fatal("Exec: when executor binding, processor is not nil")
	}
	logger := logrus.WithFields(logrus.Fields{"ProcessorId": p.Id, "ExecutorId": e.id})
	e.proc = p
	logger.Debug("Exec: bind processor")

	logger.Debug("Exec: start updating the resource occupation")
	if err := e.ins.AllocResource(p.PhysicRes); err != nil {
		logger.Fatal("alloc resource failed, ", err)
	}
	logger.Debug("Exec: finish updating the resource occupation")
	return nil
}

func (e *executor) BindRtn(r *routine.Routine) error {
	if e.routine != nil {
		logrus.Fatal("Exec: when executor binding, the routine is not nil")
	}
	e.routine = r
	logrus.WithFields(logrus.Fields{"RoutineId": r.Id, "ExecutorId": e.id}).Debug("Exec: bind routine")
	return nil
}

func (e *executor) UnbindProc() (proc *processor.Processor, err error) {
	if e.proc == nil {
		logrus.Fatal("Exec: when executor unbinding, the proc is nil")
	}
	proc, e.proc = e.proc, nil
	logrus.WithFields(logrus.Fields{"ProcessorId": proc.Id, "ExecutorId": e.id}).Debug("Exec: unbind processor")
	e.ins.WithdrawResource()
	return
}

func (e *executor) UnbindRtn() (rtn *routine.Routine, err error) {
	if e.routine == nil {
		logrus.Fatal("Exec: when executor unbinding, the routine is nil")
	}
	rtn, e.routine = e.routine, nil
	logrus.WithFields(logrus.Fields{"RoutineId": rtn.Id, "ExecutorId": e.id}).Debug("Exec: unbind routine")
	return
}

func (e *executor) SendEvt(evt *interfaces.Event) error {
	return e.ins.SendEvt(evt)
}
