package execpool

import (
	"github.com/ict/tide/pkg/executor"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/pkg/errors"
	"github.com/sirupsen/logrus"
)

type ExecutorPool interface {
	interfaces.Closer

	Get() interfaces.Executor
	Return(interfaces.Executor)

	IncInstance(delta int)
}

func New(imgName, volumePair string, commands []string, mode interfaces.StubMode) ExecutorPool {
	return newStackPool(imgName, volumePair, commands, mode)
}

func makeNew(imgName, volumePair string, commands []string, mode interfaces.StubMode) (interfaces.Executor, error) {
	exec, err := executor.New(imgName, volumePair, commands, mode)
	if err != nil {
		logrus.
			WithField("ImageName", imgName).
			Fatal("ExecPool: create executor failed, ", err)
		return nil, errors.Wrap(err, "ExecPool: failed to create executor")
	}
	return exec, nil
}
