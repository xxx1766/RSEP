package cmnct

import "github.com/ict/tide/pkg/event"

// EventCommunicator represents the communicator for events(sending events to destination).
type EventCommunicator interface {
	// SendToAddress sends the event to the address.
	SendToAddress(addr string, evt ...*event.Event) error
	// SendToStub sends the event to the stub.
	SendToStub(sid string, evt *event.Event) error
	// SendToNode sends the event to the node.
	SendToNode(nid string, evt *event.Event) error
	// BroadcastToAllNode sends the event to all nodes.
	BroadcastToAllNode(evts ...*event.Event) error
	// BroadcastToEdgThg sends the event to all edge and thing nodes.
	BroadcastToEdgThg(evt ...*event.Event) error
	// CacheStubSyncEvt caches the event for the stub.
	CacheStubSyncEvt(stubId string, evt *event.Event) error
	// NodeStateSync synchronizes the node state.
	NodeStateSync(evt *event.Event) error
}
