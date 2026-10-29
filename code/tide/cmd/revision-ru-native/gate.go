package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sync"
)

type rpcEvent struct {
	From      string `json:"from"`
	Type      string `json:"type"`
	RequestID string `json:"request_id,omitempty"`
}
type gateState struct {
	Enabled     bool  `json:"enabled"`
	ActualNS    int64 `json:"actual_unix_ns"`
	MonotonicNS int64 `json:"monotonic_ns"`
}
type faultGate struct {
	mu        sync.Mutex
	dir, node string
	log       *os.File
	current   gateState
}

func newGate(dir, node string) (*faultGate, error) {
	f, err := os.OpenFile(filepath.Join(dir, "gate-events.jsonl"), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return nil, err
	}
	return &faultGate{dir: dir, node: node, log: f}, nil
}
func (g *faultGate) close() error     { g.mu.Lock(); defer g.mu.Unlock(); return g.log.Close() }
func (g *faultGate) state() gateState { g.mu.Lock(); defer g.mu.Unlock(); return g.current }
func (g *faultGate) record(v any) error {
	if err := json.NewEncoder(g.log).Encode(v); err != nil {
		return err
	}
	return g.log.Sync()
}
func (g *faultGate) set(enabled bool) (gateState, error) {
	g.mu.Lock()
	defer g.mu.Unlock()
	p := filepath.Join(g.dir, "gate.enabled")
	if enabled {
		f, err := os.OpenFile(p, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0600)
		if err != nil {
			return g.current, err
		}
		if err = f.Close(); err != nil {
			return g.current, err
		}
	} else if err := os.Remove(p); err != nil && !os.IsNotExist(err) {
		return g.current, err
	}
	stamp, err := readClock()
	if err != nil {
		return g.current, err
	}
	g.current = gateState{Enabled: enabled, ActualNS: stamp.UnixNS, MonotonicNS: stamp.MonotonicNS}
	err = g.record(map[string]any{"kind": "transition", "node": g.node, "enabled": enabled, "actual_unix_ns": g.current.ActualNS, "monotonic_ns": stamp.MonotonicNS, "clock": stamp})
	return g.current, err
}
func (g *faultGate) reject(events []rpcEvent) bool {
	g.mu.Lock()
	defer g.mu.Unlock()
	if !g.current.Enabled {
		return false
	}
	foreign := false
	for _, e := range events {
		if e.From != g.node {
			foreign = true
		}
	}
	if !foreign {
		return false
	}
	stamp, err := readClock()
	if err != nil {
		panic(err)
	}
	if err := g.record(map[string]any{"kind": "rejected_rpc", "node": g.node, "actual_unix_ns": stamp.UnixNS, "monotonic_ns": stamp.MonotonicNS, "events": events}); err != nil {
		panic(err)
	}
	return true
}
