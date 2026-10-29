//go:build linux

package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/ict/tide/pkg/event"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/proto/eventpb"
	"github.com/sirupsen/logrus"
	"google.golang.org/protobuf/proto"
)

func TestObserverDelegatesNativeEventsAndKeepsLifetimeDestination(t *testing.T) {
	ledger := newWitnessLedger()
	d := &observedDispatcher{EventDispatcher: event.NewEventDispatcher(), ledger: ledger, node: "threefig-nj2"}
	newCalls, stopCalls := 0, 0
	d.RegisterHandler(interfaces.StubNewEvt, func(e *event.Event) {
		newCalls++
		if !ledger.destination(false, "new-function").Known {
			t.Fatal("native scheduling could see function before attribution")
		}
	})
	d.RegisterHandler(interfaces.StubStopEvt, func(e *event.Event) { stopCalls++ })
	created := int64(100)
	raw, err := proto.Marshal(&eventpb.StubNewEvent{StubId: "new-function", AppId: campaignID, Mode: eventpb.StubMode_FN, CreateTime: created})
	if err != nil {
		t.Fatal(err)
	}
	d.DispatchEvent(&event.Event{Type: interfaces.StubNewEvt, From: "threefig-nj3", Content: raw})
	raw, _ = proto.Marshal(&eventpb.StubStopEvent{StubId: "new-function"})
	d.DispatchEvent(&event.Event{Type: interfaces.StubStopEvt, From: "threefig-nj3", Content: raw})
	if newCalls != 1 || stopCalls != 1 {
		t.Fatal("observer changed native event delivery")
	}
	s := ledger.snapshot()
	if len(s) != 1 || s[0].Status != "stopped" || s[0].CreatedNS != created || !ledger.destination(false, "new-function").Known {
		t.Fatalf("lost native witness %+v", s)
	}
}
func TestCgroupOwnershipRecordsOnlyCreatedPaths(t *testing.T) {
	path := filepath.Join(t.TempDir(), "owned.json")
	o := &cgroupOwnership{paths: map[string]bool{}, manifest: path}
	entry := &logrus.Entry{Time: time.Now(), Message: "Containerxv2: finish creating cgroup", Data: logrus.Fields{"CgroupPath": "/tide/owned-unique"}}
	if err := o.Fire(entry); err != nil {
		t.Fatal(err)
	}
	entry.Message = "unrelated log"
	entry.Data["CgroupPath"] = "/tide/unrelated"
	if err := o.Fire(entry); err != nil {
		t.Fatal(err)
	}
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var paths []string
	if err := json.Unmarshal(b, &paths); err != nil {
		t.Fatal(err)
	}
	if len(paths) != 1 || paths[0] != "/tide/owned-unique" {
		t.Fatalf("ownership too broad %+v", paths)
	}
}
