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

	// 创建完后才返回，而非后台创建，
	// 可以使得Stub是在Executor创建并初始化结束后再广播创建成功，
	// 避免广播成功后，接受到Routine，Executor却未初始化完成
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
