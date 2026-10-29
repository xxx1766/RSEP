package instance

import (
	"github.com/ict/tide/pkg/executor/instance/ipc"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/processor"
	"github.com/ict/tide/pkg/routine"
	"github.com/ict/tide/pkg/stringx"
	"github.com/pkg/errors"
	"github.com/sirupsen/logrus"
)

type baseIns struct {
	id string

	inQue   ipc.InQue
	outQue  ipc.OutQue
	eventCh chan *interfaces.Event

	physRes *processor.PhysicResource

	done chan struct{}
}

func newBaseIns(id string) *baseIns {
	baseIns := &baseIns{
		id:      id,
		eventCh: make(chan *interfaces.Event, 10000),
		done:    make(chan struct{}),
	}
	return baseIns
}

func (i *baseIns) initMQ() error {
	logger := logrus.WithField("ExecutorId", i.id)

	var err error
	logger.Debug("BaseIns: start creating in mq")
	i.inQue, err = ipc.New(stringx.Concat(i.id, "-in"))
	if err != nil {
		return errors.Wrap(err, "BaseIns: create in mq failed")
	}
	logger.Debug("BaseIns: finish creating in mq")

	logger.Debug("BaseIns: start creating out mq")
	i.outQue, err = ipc.New(stringx.Concat(i.id, "-out"))
	if err != nil {
		return errors.Wrap(err, "BaseIns: create out mq failed")
	}
	logger.Debug("BaseIns: finish creating out mq")

	go i.receiveEventLoop()
	return nil
}

func (i *baseIns) close() (e error) {
	close(i.done)
	if err := i.inQue.Close(); err != nil {
		e = errors.Wrap(err, "BaseIns close in que failed")
	}
	if err := i.outQue.Close(); err != nil {
		e = errors.Wrap(err, "BaseIns: close out que failed")
	}
	return
}

func (ins *baseIns) GetEventCh() <-chan *interfaces.Event {
	return ins.eventCh
}

func (ins *baseIns) receiveEventLoop() {
	for {
		select {
		case <-ins.done:
			return

		default:
			data, err := ins.outQue.Receive()
			if err != nil {
				logrus.Error("BaseIns: receive event failed ", err)
				continue
			}
			logrus.WithField("ExecutorId", ins.id).Debug("BaseIns: receive a msg from out queue")
			evt, err := interfaces.UnmarshalEvent(data)
			if err != nil {
				logrus.Error("BaseIns: unmarshal event failed", err)
				continue
			}
			ins.eventCh <- evt
		}
	}
}

func (ins *baseIns) sendRtnToMQ(r *routine.Routine) error {
	evt := &interfaces.Event{
		Type:    interfaces.SUBMIT_ROUTINE,
		Routine: r,
	}
	return ins.SendEvt(evt)
}

func (ins *baseIns) SendEvt(evt *interfaces.Event) error {
	data, err := interfaces.MarshalEvent(evt)
	if err != nil {
		logrus.Error("BaseIns: marshal event failed, ", err)
		return err
	}
	ins.inQue.Send(data)
	return nil
}
