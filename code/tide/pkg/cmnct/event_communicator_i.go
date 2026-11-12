package cmnct

import "github.com/ict/tide/pkg/event"

type EventCommunicator interface {
	SendToAddress(addr string, evt ...*event.Event) error
	SendToStub(sid string, evt *event.Event) error
	SendToNode(nid string, evt *event.Event) error
	BroadcastToAllNode(evts ...*event.Event) error
	BroadcastToEdgThg(evt ...*event.Event) error
	CacheStubSyncEvt(stubId string, evt *event.Event) error
	NodeStateSync(evt *event.Event) error
}
