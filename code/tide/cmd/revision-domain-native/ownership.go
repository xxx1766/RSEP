package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"

	"github.com/sirupsen/logrus"
)

var ownedLeaf = regexp.MustCompile(`^/tide/[A-Za-z0-9][A-Za-z0-9-]*$`)

type executorOwnership struct {
	ID              string `json:"id"`
	Cgroup          string `json:"cgroup"`
	Ready           bool   `json:"ready"`
	HostPIDs        []int  `json:"host_pids"`
	ActiveRequestID string `json:"active_request_id,omitempty"`
}
type stubOwnership struct {
	ID    string `json:"id"`
	Node  string `json:"node"`
	Local bool   `json:"local"`
}
type ownershipSnapshot struct {
	PhysicalNode string              `json:"physical_node"`
	Cgroups      []string            `json:"cgroups"`
	Executors    []executorOwnership `json:"executors"`
	Stubs        []stubOwnership     `json:"stubs"`
}
type ownership struct {
	mu        sync.Mutex
	dir, node string
	paths     map[string]bool
	executors map[string]executorOwnership
	stubs     map[string]stubOwnership
}

func newOwnership(dir, node string) *ownership {
	return &ownership{dir: dir, node: node, paths: map[string]bool{}, executors: map[string]executorOwnership{}, stubs: map[string]stubOwnership{}}
}
func (o *ownership) Levels() []logrus.Level { return logrus.AllLevels }
func (o *ownership) Fire(entry *logrus.Entry) error {
	entry.Data["trace_unix_ns"] = entry.Time.UnixNano()
	entry.Data["physical_node"] = o.node
	o.mu.Lock()
	defer o.mu.Unlock()
	changed := false
	switch entry.Message {
	case "Containerxv2: finish creating cgroup":
		if path, ok := entry.Data["CgroupPath"].(string); ok && ownedLeaf.MatchString(path) {
			o.paths[path] = true
			changed = true
		}
	case "Containerxv2: start creating new container":
		id, _ := entry.Data["ExecutorId"].(string)
		path, _ := entry.Data["CgroupPath"].(string)
		if id != "" && o.paths[path] {
			o.executors[id] = executorOwnership{ID: id, Cgroup: path}
			changed = true
		}
	case "StackPool: received one INIT event":
		id, _ := entry.Data["ExecutorId"].(string)
		if executor, ok := o.executors[id]; ok {
			executor.Ready = true
			o.executors[id] = executor
			changed = true
		}
	case "Exec: bind routine", "Exec: unbind routine":
		id, _ := entry.Data["ExecutorId"].(string)
		if executor, ok := o.executors[id]; ok {
			executor.ActiveRequestID, _ = entry.Data["RoutineId"].(string)
			if entry.Message == "Exec: unbind routine" {
				executor.ActiveRequestID = ""
			}
			o.executors[id] = executor
			changed = true
		}
	case "Stub: finish creating":
		id, _ := entry.Data["StubId"].(string)
		if id != "" {
			o.stubs[id] = stubOwnership{ID: id, Node: o.node, Local: true}
			changed = true
		}
	case "ClsStatHdlr: receive a StubNewEvent":
		id, _ := entry.Data["StubId"].(string)
		node, _ := entry.Data["NodeId"].(string)
		if id != "" {
			o.stubs[id] = stubOwnership{ID: id, Node: node, Local: node == o.node}
			changed = true
		}
	case "ClsStat: remove stub over":
		id, _ := entry.Data["StubId"].(string)
		delete(o.stubs, id)
		changed = true
	}
	if !changed {
		return nil
	}
	data, err := json.MarshalIndent(o.copyLocked(), "", "  ")
	if err != nil {
		return err
	}
	tmp := filepath.Join(o.dir, "owned.json.partial")
	if err := os.WriteFile(tmp, append(data, '\n'), 0600); err != nil {
		return err
	}
	return os.Rename(tmp, filepath.Join(o.dir, "owned.json"))
}
func (o *ownership) copyLocked() ownershipSnapshot {
	s := ownershipSnapshot{PhysicalNode: o.node, Cgroups: []string{}, Executors: []executorOwnership{}, Stubs: []stubOwnership{}}
	for path := range o.paths {
		s.Cgroups = append(s.Cgroups, path)
	}
	for _, executor := range o.executors {
		executor.HostPIDs = append([]int(nil), executor.HostPIDs...)
		s.Executors = append(s.Executors, executor)
	}
	for _, st := range o.stubs {
		s.Stubs = append(s.Stubs, st)
	}
	sort.Strings(s.Cgroups)
	sort.Slice(s.Executors, func(i, j int) bool { return s.Executors[i].ID < s.Executors[j].ID })
	sort.Slice(s.Stubs, func(i, j int) bool { return s.Stubs[i].ID < s.Stubs[j].ID })
	return s
}
func (o *ownership) snapshot() ownershipSnapshot {
	o.mu.Lock()
	snapshot := o.copyLocked()
	o.mu.Unlock()
	for i := range snapshot.Executors {
		executor := &snapshot.Executors[i]
		data, _ := os.ReadFile(filepath.Join("/sys/fs/cgroup", executor.Cgroup, "cgroup.procs"))
		for _, field := range strings.Fields(string(data)) {
			if pid, err := strconv.Atoi(field); err == nil {
				executor.HostPIDs = append(executor.HostPIDs, pid)
			}
		}
	}
	return snapshot
}

// An installed peer advertises its actually created warm stub. This records
// bootstrap inventory without manufacturing a native StubNew event in traces.
func (o *ownership) registerInitialStub(id, node string) {
	o.mu.Lock()
	defer o.mu.Unlock()
	o.stubs[id] = stubOwnership{ID: id, Node: node, Local: node == o.node}
}

func (o *ownership) persist() error {
	o.mu.Lock()
	defer o.mu.Unlock()
	data, err := json.MarshalIndent(o.copyLocked(), "", "  ")
	if err != nil {
		return err
	}
	tmp := filepath.Join(o.dir, "owned.json.partial")
	if err := os.WriteFile(tmp, append(data, '\n'), 0600); err != nil {
		return err
	}
	return os.Rename(tmp, filepath.Join(o.dir, "owned.json"))
}
