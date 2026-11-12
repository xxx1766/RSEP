package event

import (
	"fmt"

	"github.com/ict/tide/proto/eventpb"
)

type Event struct {
	Type    string
	From    string
	Content []byte
}

func (e *Event) String() string {
	return fmt.Sprintf("Event{%s, %s}", e.Type, e.From)
}

func EventToPb(evt *Event) *eventpb.Event {
	return &eventpb.Event{
		Type:    evt.Type,
		From:    evt.From,
		Content: evt.Content,
	}
}

func EvtPbToEvent(pb *eventpb.Event) *Event {
	evt := &Event{}
	evt.Type = pb.Type
	evt.From = pb.From
	evt.Content = pb.Content
	return evt
}
