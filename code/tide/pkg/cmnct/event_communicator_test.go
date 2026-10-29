package cmnct

import (
	"github.com/ict/tide/pkg/event"
	"testing"
	"time"
)

func TestSendDoesNotMutateSharedEvent(t *testing.T) {
	d := event.NewEventDispatcher()
	got := make(chan *event.Event, 1)
	d.RegisterHandler("copy", func(e *event.Event) { got <- e })
	Init("sender", "loop", d)
	original := &event.Event{Type: "copy", From: "original", Content: []byte("data")}
	if err := Singleton().SendToAddress("loop", original); err != nil {
		t.Fatal(err)
	}
	select {
	case received := <-got:
		if received.From != "sender" || original.From != "original" || received == original {
			t.Fatal("sending mutated/reused caller-owned event; broadcast readers can race")
		}
	case <-time.After(time.Second):
		t.Fatal("event not delivered")
	}
}
