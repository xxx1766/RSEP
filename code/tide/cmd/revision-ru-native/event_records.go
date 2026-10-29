package main

import (
	"github.com/ict/tide/proto/eventpb"
	"google.golang.org/protobuf/proto"
	"strings"
)

func rpcRecords(batch *eventpb.EventList) []rpcEvent {
	records := make([]rpcEvent, 0, len(batch.Events))
	for _, e := range batch.Events {
		v := rpcEvent{From: e.From, Type: e.Type}
		// All native request messages carry rtn_id as string field1. Restrict this
		// decode to request event names: control events have unrelated field1 IDs.
		if strings.HasSuffix(e.Type, "/submit_routine") || strings.HasSuffix(e.Type, "/submit_rtn") || strings.HasSuffix(e.Type, "/routine_receive") || strings.HasSuffix(e.Type, "/routine_wait") || strings.HasSuffix(e.Type, "/wait_routine") || strings.HasSuffix(e.Type, "/routine_done") {
			var p eventpb.RoutineSubmitEvent
			if proto.Unmarshal(e.Content, &p) == nil {
				v.RequestID = p.RtnId
			}
		}
		records = append(records, v)
	}
	return records
}
