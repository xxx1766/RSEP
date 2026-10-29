package clsstat

import (
	"sync"
	"time"

	"github.com/ict/tide/pkg/routine"
	"github.com/sirupsen/logrus"
)

// NodeState represents the state of a node.
type NodeState struct {
	mu      sync.RWMutex
	Id      string
	Address string
	Role    NodeRole

	ResTotal  *routine.Resource
	ResRemain *routine.Resource

	UpdateTime time.Time
}

// Capacity returns one consistent resource/heartbeat snapshot.
func (ns *NodeState) Capacity() (total, remaining *routine.Resource, updated time.Time) {
	ns.mu.RLock()
	defer ns.mu.RUnlock()
	return ns.ResTotal.Copy(), ns.ResRemain.Copy(), ns.UpdateTime
}

// AddNodeOpts represents the options for adding a node.
type AddNodeOpts struct {
	Nid      string
	Address  string
	ResTotal *routine.Resource
	AppList  []string
	Role     NodeRole
}

// UpdateNodeOpts represents the options for updating a node.
type UpdateNodeOpts struct {
	Nid       string
	ResRemain *routine.Resource
}

// AddNode adds a new node to the cluster.
func (cs *ClusterState) AddNode(opts *AddNodeOpts) *NodeState {
	cs.nodeMu.RLock()
	if idx, ok := cs.nid2idx[opts.Nid]; ok {
		cs.nodeMu.RUnlock()
		logrus.Warn("ClsStat: repeated joining of the same node")
		return cs.nodes[idx]
	}
	cs.nodeMu.RUnlock()

	ns := &NodeState{
		Id:        opts.Nid,
		Address:   opts.Address,
		Role:      opts.Role,
		ResTotal:  opts.ResTotal,
		ResRemain: routine.EmptyResourse,
	}
	cs.nodeMu.Lock()
	defer cs.nodeMu.Unlock()
	cs.nodes = append(cs.nodes, ns)
	cs.nid2idx[ns.Id] = len(cs.nodes) - 1
	return ns
}

// UpdateNode updates the node with the given UpdateNodeOpts.
func (cs *ClusterState) UpdateNode(opts *UpdateNodeOpts) {
	ns := cs.GetNode(opts.Nid)

	// TODO: 不优雅
	if ns == nil {
		return
	}
	ns.mu.Lock()
	defer ns.mu.Unlock()
	ns.ResRemain = opts.ResRemain.Copy()
	ns.UpdateTime = time.Now()
}

// ContainNode checks if the cluster contains a node with the given Id.
func (cs *ClusterState) ContainNode(nid string) bool {
	cs.nodeMu.RLock()
	defer cs.nodeMu.RUnlock()
	_, ok := cs.nid2idx[nid]
	return ok
}

// GetNode returns the node with the given Id.
func (cs *ClusterState) GetNode(nid string) *NodeState {
	cs.nodeMu.RLock()
	defer cs.nodeMu.RUnlock()
	if idx, ok := cs.nid2idx[nid]; ok {
		return cs.nodes[idx]
	}
	return nil
}

// GetIdx returns the index of the node with the given Id.
func (cs *ClusterState) GetIdx(sid string) int {
	cs.nodeMu.RLock()
	defer cs.nodeMu.RUnlock()
	return cs.sid2idx[sid]
}

// ListNode returns all the nodes in the cluster.
func (cs *ClusterState) ListNode() []*NodeState {
	return cs.nodes
}

// GetSelf returns the self node.
func (cs *ClusterState) GetSelf() *NodeState {
	return cs.self
}

// ListStub returns all the stubs in the cluster.
func (cs *ClusterState) ListStub() []*StubState {
	return cs.stubs
}

// ListEdgeNode returns all the edge nodes in the cluster.
func (cs *ClusterState) ListEdgeNode() []*NodeState {
	return cs.ListNodeByRole(EDGE)
}

// ListCloudNode returns all the cloud nodes in the cluster.
func (cs *ClusterState) ListCloudNode() []*NodeState {
	return cs.ListNodeByRole(CLOUD)
}

// ListNodeByRole returns all the nodes with the given role in the cluster.
func (cs *ClusterState) ListNodeByRole(r NodeRole) []*NodeState {
	cs.nodeMu.RLock()
	defer cs.nodeMu.RUnlock()
	nodes := make([]*NodeState, 0, 2)
	for _, nd := range cs.nodes {
		if nd.Role == r {
			nodes = append(nodes, nd)
		}
	}
	return nodes
}
