package main

import (
	"github.com/sirupsen/logrus"
	"os"
	"path/filepath"
	"testing"
)

func TestOwnershipRecordsOnlyCreatedLeavesAndActualWarmExecutors(t *testing.T) {
	o := newOwnership(t.TempDir())
	if !ownedLeaf.MatchString("/tide/8c7e2fb9-6224-444d-b741-0fb3718bda84") {
		t.Fatal("native UUID leaf rejected")
	}
	emit := func(msg string, f logrus.Fields) {
		t.Helper()
		if err := o.Fire(&logrus.Entry{Message: msg, Data: f}); err != nil {
			t.Fatal(err)
		}
	}
	emit("Containerxv2: finish creating cgroup", logrus.Fields{"CgroupPath": "/tide"})
	emit("Containerxv2: finish creating cgroup", logrus.Fields{"CgroupPath": "/tide/leaf1"})
	emit("Containerxv2: start creating new container", logrus.Fields{"ExecutorId": "exec1", "CgroupPath": "/tide/leaf1"})
	emit("StackPool: received one INIT event", logrus.Fields{"ExecutorId": "exec1"})
	s := o.snapshot()
	if len(s.Cgroups) != 1 || s.Cgroups[0] != "/tide/leaf1" || len(s.Executors) != 1 || !s.Executors[0].Ready || s.Executors[0].Cgroup != "/tide/leaf1" {
		t.Fatalf("ownership %v", s)
	}
	if _, err := os.Stat(filepath.Join(o.dir, "owned.json")); err != nil {
		t.Fatal(err)
	}
	s.Cgroups[0] = "mutation"
	if o.snapshot().Cgroups[0] != "/tide/leaf1" {
		t.Fatal("mutable snapshot")
	}
}
