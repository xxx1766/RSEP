package main

import (
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/sirupsen/logrus"
)

func TestOwnershipTracksColdPreforkAndPhysicalNode(t *testing.T) {
	o := newOwnership(t.TempDir(), "B")
	emit := func(message string, fields logrus.Fields) {
		t.Helper()
		if err := o.Fire(&logrus.Entry{Message: message, Data: fields, Time: time.Now()}); err != nil {
			t.Fatal(err)
		}
	}
	emit("Containerxv2: finish creating cgroup", logrus.Fields{"CgroupPath": "/tide/leaf"})
	for _, id := range []string{"cold-worker-1", "cold-worker-2"} {
		emit("Containerxv2: start creating new container", logrus.Fields{"ExecutorId": id, "CgroupPath": "/tide/leaf"})
		emit("StackPool: received one INIT event", logrus.Fields{"ExecutorId": id})
	}
	emit("Stub: finish creating", logrus.Fields{"StubId": "native-new-stub"})
	emit("Exec: bind routine", logrus.Fields{"ExecutorId": "cold-worker-2", "RoutineId": "request-7"})
	s := o.snapshot()
	if s.PhysicalNode != "B" || len(s.Executors) != 2 || len(s.Stubs) != 1 || s.Stubs[0].Node != "B" || !s.Executors[1].Ready || s.Executors[1].ActiveRequestID != "request-7" {
		t.Fatalf("cold ownership lost: %+v", s)
	}
	s.Executors[0].ID = "changed"
	if o.snapshot().Executors[0].ID == "changed" {
		t.Fatal("snapshot mutable")
	}
}

func TestControlOnlyOwnershipCanPersistEmptyActualInventory(t *testing.T) {
	dir := t.TempDir()
	o := newOwnership(dir, "C")
	if err := o.persist(); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(dir, "owned.json")); err != nil {
		t.Fatal(err)
	}
	if s := o.snapshot(); len(s.Executors) != 0 || len(s.Cgroups) != 0 || s.PhysicalNode != "C" {
		t.Fatalf("control-only inventory fabricated work: %+v", s)
	}
}
