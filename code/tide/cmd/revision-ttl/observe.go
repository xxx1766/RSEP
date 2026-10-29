package main

import (
	"encoding/json"
	"fmt"
	"io"
	"sync"
	"time"

	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/event"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/proto/eventpb"
	"google.golang.org/protobuf/proto"
)

type observation struct {
	NS     int64          `json:"t_ns"`
	Kind   string         `json:"kind"`
	Detail map[string]any `json:"detail,omitempty"`
}

type observer struct {
	mu        sync.Mutex
	start     time.Time
	events    *json.Encoder
	resources *json.Encoder
	samples   []resourceSample
	alive     map[string]bool
	idle      bool
	closed    bool
	err       error
}

func newObserver(start time.Time, events, resources io.Writer) *observer {
	return &observer{start: start, events: json.NewEncoder(events), resources: json.NewEncoder(resources), alive: make(map[string]bool)}
}

func (o *observer) emitLocked(kind string, detail map[string]any) {
	if err := o.events.Encode(observation{time.Since(o.start).Nanoseconds(), kind, detail}); err != nil && o.err == nil {
		o.err = err
	}
}

func (o *observer) emit(kind string, detail map[string]any) {
	o.mu.Lock()
	defer o.mu.Unlock()
	if !o.closed {
		o.emitLocked(kind, detail)
	}
}

func (o *observer) sampleLocked(total, remaining int, source string) {
	sample := resourceSample{time.Since(o.start).Nanoseconds(), total, remaining, o.idle, len(o.alive), source}
	if len(o.samples) > 0 && o.samples[len(o.samples)-1].RemainingCPU != remaining {
		o.emitLocked("capacity_change_observed", map[string]any{"remaining_cpu": remaining, "reserved_cpu": total - remaining})
	}
	o.samples = append(o.samples, sample)
	if err := o.resources.Encode(sample); err != nil && o.err == nil {
		o.err = err
	}
}

func (o *observer) heartbeat() {
	// Capacity() returns a locked copy of the snapshot published by the
	// allocator's existing 5 ms heartbeat, not a prediction from configured TTL.
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.closed {
		return
	}
	total, remaining, _ := clsstat.Singleton().GetSelf().Capacity()
	o.sampleLocked(total.CpuNum, remaining.CpuNum, "heartbeat")
}

func (o *observer) state(kind, sid string, alive bool) {
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.closed {
		return
	}
	if alive {
		o.alive[sid] = true
	} else {
		delete(o.alive, sid)
	}
	o.emitLocked(kind, map[string]any{"stub_id": sid, "alive_stubs": len(o.alive)})
	if len(o.samples) > 0 {
		last := o.samples[len(o.samples)-1]
		o.sampleLocked(last.TotalCPU, last.RemainingCPU, "stub_state")
	}
}

func (o *observer) phase(idle bool, burst int) {
	o.mu.Lock()
	defer o.mu.Unlock()
	o.idle = idle
	o.emitLocked("phase", map[string]any{"post_burst_idle": idle, "burst": burst})
	if len(o.samples) > 0 {
		last := o.samples[len(o.samples)-1]
		o.sampleLocked(last.TotalCPU, last.RemainingCPU, "phase")
	}
}

func (o *observer) released() bool {
	o.mu.Lock()
	defer o.mu.Unlock()
	return len(o.samples) > 0 && o.samples[len(o.samples)-1].RemainingCPU == 2 && len(o.alive) == 0
}

func (o *observer) gapState(burst int, gap, ttl time.Duration) error {
	o.mu.Lock()
	defer o.mu.Unlock()
	if len(o.samples) == 0 {
		return fmt.Errorf("no capacity observation after idle gap")
	}
	remaining, alive := o.samples[len(o.samples)-1].RemainingCPU, len(o.alive)
	expect := gapExpectation(gap, ttl)
	o.emitLocked("idle_gap_observed", map[string]any{"burst": burst, "gap_ns": gap.Nanoseconds(), "expectation": expect, "remaining_cpu": remaining, "alive_stubs": alive})
	if expect == "released" && (remaining != 2 || alive != 0) {
		return fmt.Errorf("burst %d: expected observed TTL release, got remaining=%d alive=%d", burst, remaining, alive)
	}
	if expect == "retained" && (remaining != 0 || alive != 1) {
		return fmt.Errorf("burst %d: expected retained used stub, got remaining=%d alive=%d", burst, remaining, alive)
	}
	return nil
}

func (o *observer) finish() (allocationStats, error) {
	o.mu.Lock()
	defer o.mu.Unlock()
	if len(o.samples) > 0 {
		last := o.samples[len(o.samples)-1]
		o.sampleLocked(last.TotalCPU, last.RemainingCPU, "end")
	}
	o.closed = true
	if o.err != nil {
		return allocationStats{}, o.err
	}
	return integrateAllocation(o.samples)
}

// Observe the real dispatcher without replacing its state transitions.
type observedDispatcher struct {
	event.EventDispatcher
	observer *observer
}

func (d *observedDispatcher) DispatchEvent(evt *event.Event) {
	if evt.Type == interfaces.StubStopEvt {
		var stop eventpb.StubStopEvent
		if err := proto.Unmarshal(evt.Content, &stop); err != nil {
			d.observer.emit("invalid_stop_event", map[string]any{"error": fmt.Sprint(err)})
			d.EventDispatcher.DispatchEvent(evt)
			return
		}
		d.observer.emit("stub_stop_received", map[string]any{"stub_id": stop.StubId})
		d.EventDispatcher.DispatchEvent(evt)
		// The original handler has now closed the stub, returned its bound
		// processor and marked its cluster-state entry STOPPED.
		d.observer.state("stub_stop_handled", stop.StubId, false)
		return
	}
	d.EventDispatcher.DispatchEvent(evt)
	if evt.Type == interfaces.NodeSyncEvt {
		d.observer.heartbeat()
	}
}
