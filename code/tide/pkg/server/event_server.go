package server

import (
	"context"

	"github.com/ict/tide/pkg/event"
	"github.com/ict/tide/proto/eventpb"
)

type EventServer struct {
	eventpb.UnimplementedEventServiceServer

	evtDispr event.EventDispatcher
}

func NewEventServer(evtDispr event.EventDispatcher) *EventServer {
	return &EventServer{
		evtDispr: evtDispr,
	}
}

var result eventpb.EventResult

func (s *EventServer) SendEvent(ctx context.Context, in *eventpb.EventList) (*eventpb.EventResult, error) {
	go func() {
		evtPbs := in.Events
		for _, evtPb := range evtPbs {
			evt := event.EvtPbToEvent(evtPb)
			s.evtDispr.DispatchEvent(evt)
		}
	}()
	return &result, nil
}
