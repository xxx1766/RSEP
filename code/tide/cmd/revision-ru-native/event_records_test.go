package main

import (
	"github.com/ict/tide/proto/eventpb"
	"google.golang.org/protobuf/proto"
	"testing"
)

func TestGateEvidenceDoesNotMislabelNodeIDsAsRequests(t *testing.T) {
	request, _ := proto.Marshal(&eventpb.RoutineSubmitEvent{RtnId: "request-1"})
	node, _ := proto.Marshal(&eventpb.NodeNewEvent{NodeId: "ru-cloud"})
	got := rpcRecords(&eventpb.EventList{Events: []*eventpb.Event{{From: "ru-cloud", Type: "/cloud/app/r-u-20261020/submit_rtn", Content: request}, {From: "ru-cloud", Type: "/clsstat/new_node", Content: node}}})
	if len(got) != 2 || got[0].RequestID != "request-1" || got[1].RequestID != "" {
		t.Fatalf("mislabeled gate records %+v", got)
	}
}
