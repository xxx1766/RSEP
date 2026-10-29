package containerxv2

import (
	"fmt"
	"github.com/sirupsen/logrus"
	logtest "github.com/sirupsen/logrus/hooks/test"
	"testing"
	"time"
)

func TestDeleteMissingCgroupIsIdempotent(t *testing.T) {
	hook := logtest.NewGlobal()
	path := fmt.Sprintf("/tide/revision-nonexistent-%d", time.Now().UnixNano())
	DeleteCgroup(path)
	for _, entry := range hook.AllEntries() {
		if entry.Level <= logrus.ErrorLevel {
			t.Fatalf("missing cgroup cleanup logged failure: %s", entry.Message)
		}
	}
}
