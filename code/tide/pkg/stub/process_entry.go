package stub

import (
	"github.com/ict/tide/pkg/cmnct"
	"github.com/ict/tide/pkg/event"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/proto/eventpb"
	"github.com/sirupsen/logrus"
	"google.golang.org/protobuf/proto"
)

func (s *stub) processEntry(exec interfaces.Executor) {
	logger := logrus.WithFields(logrus.Fields{
		"StubId":      s.id,
		"ExecutorId":  exec.Id(),
		"ProcessorId": s.bindProc.Id,
	})

	<-s.bindCh
	if err := exec.BindProc(s.bindProc); err != nil {
		logger.Error("Stub: failed to bind processor to executor")
		return
	}

	if err := exec.Execute(); err != nil {
		logger.Error("Stub: failed to execute the executor, ", err)
	}
	s.listenEvt(exec)

	// clear processor and executor, after executing
	if _, err := exec.UnbindProc(); err != nil {
		logger.Error("Stub: failed to unbind processor, ", err)
	}
	if err := exec.Close(); err != nil {
		logger.Error("Stub: failed to close executor, ", err)
	}
	s.bindCh <- struct{}{}

	// broadcast to stubs of this app that this app is done
	stStopEvtPb := &eventpb.AppStopEvent{AppId: s.appId}
	content, err := proto.Marshal(stStopEvtPb)
	if err != nil {
		logrus.Error("Stub: failed to marshal AppStopEvent, ", err)
	}
	evt := &event.Event{Type: interfaces.AppStopEvt, Content: content}
	if err := cmnct.Singleton().BroadcastToAllNode(evt); err != nil {
		logrus.Error("Stub: failed to broadcast AppStopEvent, ", err)
	}
}
