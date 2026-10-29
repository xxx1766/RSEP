package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"sync"

	"github.com/sirupsen/logrus"
)

var ownedLeaf = regexp.MustCompile(`^/tide/[A-Za-z0-9][A-Za-z0-9-]*$`)

type executorOwnership struct {
	ID     string `json:"id"`
	Cgroup string `json:"cgroup"`
	Ready  bool   `json:"ready"`
}
type ownershipSnapshot struct {
	Cgroups   []string            `json:"cgroups"`
	Executors []executorOwnership `json:"executors"`
}
type ownership struct {
	mu        sync.Mutex
	dir       string
	paths     map[string]bool
	executors map[string]executorOwnership
}

func newOwnership(dir string) *ownership {
	return &ownership{dir: dir, paths: map[string]bool{}, executors: map[string]executorOwnership{}}
}
func (o *ownership) Levels() []logrus.Level { return logrus.AllLevels }
func (o *ownership) Fire(e *logrus.Entry) error {
	e.Data["trace_unix_ns"] = e.Time.UnixNano()
	o.mu.Lock()
	defer o.mu.Unlock()
	changed := false
	switch e.Message {
	case "Containerxv2: finish creating cgroup":
		if p, ok := e.Data["CgroupPath"].(string); ok && ownedLeaf.MatchString(p) {
			o.paths[p] = true
			changed = true
		}
	case "Containerxv2: start creating new container":
		id, _ := e.Data["ExecutorId"].(string)
		p, _ := e.Data["CgroupPath"].(string)
		if id != "" && o.paths[p] {
			o.executors[id] = executorOwnership{ID: id, Cgroup: p}
			changed = true
		}
	case "StackPool: received one INIT event":
		if id, ok := e.Data["ExecutorId"].(string); ok {
			if v, exists := o.executors[id]; exists {
				v.Ready = true
				o.executors[id] = v
				changed = true
			}
		}
	}
	if !changed {
		return nil
	}
	data, err := json.MarshalIndent(o.copyLocked(), "", "  ")
	if err != nil {
		return err
	}
	tmp := filepath.Join(o.dir, "owned.json.partial")
	if err = os.WriteFile(tmp, append(data, '\n'), 0600); err != nil {
		return err
	}
	return os.Rename(tmp, filepath.Join(o.dir, "owned.json"))
}
func (o *ownership) copyLocked() ownershipSnapshot {
	s := ownershipSnapshot{Cgroups: []string{}, Executors: []executorOwnership{}}
	for p := range o.paths {
		s.Cgroups = append(s.Cgroups, p)
	}
	for _, e := range o.executors {
		s.Executors = append(s.Executors, e)
	}
	sort.Strings(s.Cgroups)
	sort.Slice(s.Executors, func(i, j int) bool { return s.Executors[i].ID < s.Executors[j].ID })
	return s
}
func (o *ownership) snapshot() ownershipSnapshot {
	o.mu.Lock()
	defer o.mu.Unlock()
	return o.copyLocked()
}
