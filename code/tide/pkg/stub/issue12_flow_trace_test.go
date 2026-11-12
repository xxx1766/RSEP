package stub

import (
	"testing"
	"time"

	"github.com/sirupsen/logrus"
)

type issue12FlowClock struct{}

func (issue12FlowClock) Levels() []logrus.Level { return logrus.AllLevels }
func (issue12FlowClock) Fire(entry *logrus.Entry) error {
	entry.Data["trace_unix_ns"] = entry.Time.UnixNano()
	return nil
}

func issue12FlowLogging(t *testing.T) {
	t.Helper()
	t.Setenv("TMC_ISSUE12_DEBUG", "1")
	logrus.SetFormatter(&logrus.JSONFormatter{TimestampFormat: time.RFC3339Nano})
	logrus.AddHook(issue12FlowClock{})
}

func TestIssue12FlowWorker(t *testing.T) {
	issue12FlowLogging(t)
	TestIssue5WorkerProcess(t)
}

func TestIssue12FlowOrigin(t *testing.T) {
	issue12FlowLogging(t)
	TestIssue5OriginProcess(t)
}
