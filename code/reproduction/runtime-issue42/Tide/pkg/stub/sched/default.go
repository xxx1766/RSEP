package sched

import (
	"math"
	"math/rand"
	"sort"
	"time"

	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/routine"
	"github.com/ict/tide/pkg/schedpolicy"
)

func init() {
	rand.Seed(time.Now().UnixNano())
}

func defaultSortStub(rtn *routine.Routine, stStats []*clsstat.StubState) []*clsstat.StubState {
	now := time.Now()
	cfg := dev.CurrentConfig()
	deadline := rtn.SubmitTime + int64(dev.GetToleranceTimeFor(rtn.FnName))
	type candidate struct {
		stub  *clsstat.StubState
		score float64
	}
	var candidates []candidate
	for _, stStat := range stStats {
		status, end := stStat.SchedulingState()
		if stStat.Mode == interfaces.StubEntry || status != clsstat.STUB_RUNNING || stStat.Resource == nil || !stStat.Resource.GreaterOrEqual(rtn.ResReq) {
			continue
		}
		node := clsstat.Singleton().GetNode(stStat.NodeId)
		if node == nil {
			continue
		}
		total, remain, updated := node.Capacity()
		if total.CpuNum <= 0 || updated.IsZero() || now.Sub(updated) > time.Duration(cfg.StateMaxAgeMS)*time.Millisecond {
			continue
		}
		start := int64(end)
		if start < now.UnixNano() {
			start = now.UnixNano()
		}
		finish := start + int64(dev.GetEstExecTime(stStat.NodeId)) + dev.GetNetworkBudget(stStat.NodeId).Nanoseconds()
		if finish > deadline {
			continue
		}
		weight := 1.0
		if w, ok := cfg.DeviceWeights[stStat.NodeId]; ok {
			weight = w
		}
		score := schedpolicy.Score(float64(remain.CpuNum)/float64(total.CpuNum), float64(finish-now.UnixNano())/1e6, weight, cfg.Weights, float64(cfg.LatencyScaleMS))
		if math.IsInf(score, 0) || math.IsNaN(score) {
			continue
		}
		candidates = append(candidates, candidate{stStat, score})
	}
	sort.Slice(candidates, func(i, j int) bool {
		if candidates[i].score == candidates[j].score {
			return candidates[i].stub.Id < candidates[j].stub.Id
		}
		return candidates[i].score > candidates[j].score
	})
	filtered := make([]*clsstat.StubState, len(candidates))
	for i, c := range candidates {
		filtered[i] = c.stub
	}
	return filtered
}

func defaultSortNode(rtn *routine.Routine, ndStats []*clsstat.NodeState) []*clsstat.NodeState {
	cfg := dev.CurrentConfig()
	now := time.Now()
	deadline := rtn.SubmitTime + int64(dev.GetToleranceTimeFor(rtn.FnName))
	type candidate struct {
		node  *clsstat.NodeState
		score float64
	}
	var candidates []candidate
	for _, node := range ndStats {
		total, remain, updated := node.Capacity()
		if total.CpuNum <= 0 || !remain.GreaterOrEqual(dev.GetStubResource()) || !dev.GetStubResource().GreaterOrEqual(rtn.ResReq) || updated.IsZero() || now.Sub(updated) > time.Duration(cfg.StateMaxAgeMS)*time.Millisecond {
			continue
		}
		latency := int64(dev.GetEstExecTime(node.Id)) + dev.GetNetworkBudget(node.Id).Nanoseconds()
		if now.UnixNano()+latency > deadline {
			continue
		}
		weight := 1.0
		if w, ok := cfg.DeviceWeights[node.Id]; ok {
			weight = w
		}
		score := schedpolicy.Score(float64(remain.CpuNum)/float64(total.CpuNum), float64(latency)/1e6, weight, cfg.Weights, float64(cfg.LatencyScaleMS))
		if math.IsInf(score, 0) || math.IsNaN(score) {
			continue
		}
		candidates = append(candidates, candidate{node, score})
	}
	sort.Slice(candidates, func(i, j int) bool {
		if candidates[i].score == candidates[j].score {
			return candidates[i].node.Id < candidates[j].node.Id
		}
		return candidates[i].score > candidates[j].score
	})
	result := make([]*clsstat.NodeState, len(candidates))
	for i, c := range candidates {
		result[i] = c.node
	}
	return result
}

func cpuBalanceSortNode(rtn *routine.Routine, ndStats []*clsstat.NodeState) []*clsstat.NodeState {
	type pair struct {
		util float32
		node *clsstat.NodeState
	}

	var pairs []*pair
	for i := len(ndStats) - 1; i >= 0; i-- {
		ndStat := ndStats[i]
		if ndStat.ResTotal.CpuNum <= 0 || !ndStat.ResRemain.GreaterOrEqual(dev.GetStubResource()) {
			continue
		}
		util := float32(ndStat.ResRemain.CpuNum) / float32(ndStat.ResTotal.CpuNum)
		pairs = append(pairs, &pair{util, ndStat})
	}

	sort.Slice(pairs, func(i, j int) bool {
		return pairs[i].util > pairs[j].util
	})

	ret := make([]*clsstat.NodeState, len(pairs))
	for i, p := range pairs {
		ret[i] = p.node
	}
	return ret
}

func randomSortNode(rtn *routine.Routine, ndStats []*clsstat.NodeState) []*clsstat.NodeState {
	var filtered []*clsstat.NodeState
	for i := len(ndStats) - 1; i >= 0; i-- {
		ndStat := ndStats[i]
		if ndStat.ResTotal.CpuNum <= 0 || !ndStat.ResRemain.GreaterOrEqual(dev.GetStubResource()) {
			continue
		}
		filtered = append(filtered, ndStat)
	}

	rand.Shuffle(len(filtered), func(i, j int) { filtered[i], filtered[j] = filtered[j], filtered[i] })
	return filtered
}
