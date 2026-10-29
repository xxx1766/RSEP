package main

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"

	"github.com/aceofkid/posix_mq"
	"github.com/ict/tide/proto/execinspb"
	"google.golang.org/protobuf/proto"
)

func servePAIX() error {
	inName, outName := os.Getenv("TIDE_IN_MQ"), os.Getenv("TIDE_OUT_MQ")
	if inName == "" || outName == "" {
		return fmt.Errorf("missing TIDE_IN_MQ/TIDE_OUT_MQ")
	}
	// Open existing queues. The library uses this size for its receive buffer;
	// it does not alter attributes of already-created queues.
	attr := &posix_mq.MessageQueueAttribute{MsgSize: 1 << 24}
	in, err := posix_mq.NewMessageQueue("/"+strings.TrimPrefix(inName, "/"), posix_mq.O_RDWR, 0600, attr)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := posix_mq.NewMessageQueue("/"+strings.TrimPrefix(outName, "/"), posix_mq.O_RDWR, 0600, attr)
	if err != nil {
		return err
	}
	defer out.Close()
	send := func(evt *execinspb.Event) error {
		data, err := proto.Marshal(evt)
		if err != nil {
			return err
		}
		return out.Send(data, 0)
	}
	if err := send(&execinspb.Event{Type: execinspb.Event_INIT}); err != nil {
		return err
	}
	for {
		data, _, err := in.Receive()
		if err != nil {
			return err
		}
		var evt execinspb.Event
		if err := proto.Unmarshal(data, &evt); err != nil {
			return err
		}
		if evt.Type != execinspb.Event_ROUTINE_SUBMIT || evt.Routine == nil {
			return fmt.Errorf("unexpected PAIX event %v", evt.Type)
		}
		r := execute(evt.Routine.Args)
		result, err := json.Marshal(r)
		if err != nil {
			return err
		}
		status := execinspb.Event_SUCC
		if r.Error != "" {
			status = execinspb.Event_FAIL
		}
		withData := true
		if err := send(&execinspb.Event{Type: execinspb.Event_EXIT, Status: &status, WithData: &withData, Result: result, Error: &r.Error}); err != nil {
			return err
		}
	}
}
