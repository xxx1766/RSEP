package event

import (
	"fmt"

	"github.com/ict/tide/proto/eventpb"
)

// Event represents an event.
type Event struct {
	Type    string
	From    string
	Content []byte
}

// String returns the string representation of the event.
func (e *Event) String() string {
	return fmt.Sprintf("Event{%s, %s}", e.Type, e.From)
}

// EventToPb converts an event to a protobuf event.
func EventToPb(evt *Event) *eventpb.Event {
	return &eventpb.Event{
		Type:    evt.Type,
		From:    evt.From,
		Content: evt.Content,
	}
}

// EvtPbToEvent converts a protobuf event to an event.
func EvtPbToEvent(pb *eventpb.Event) *Event {
	evt := &Event{}
	evt.Type = pb.Type
	evt.From = pb.From
	evt.Content = pb.Content
	return evt
}
