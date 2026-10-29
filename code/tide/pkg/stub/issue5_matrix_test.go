package stub

import (
	"bufio"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/cmnct"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/event"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/routine"
	"github.com/ict/tide/pkg/schedpolicy"
	"github.com/ict/tide/pkg/stub/waiters"
	"github.com/ict/tide/proto/eventpb"
	"google.golang.org/protobuf/proto"
)

type issue5RunSpec struct {
	ID           string     `json:"id"`
	Rate         int        `json:"rate"`
	Pattern      string     `json:"pattern"`
	Repeat       int        `json:"repeat"`
	Seed         int64      `json:"seed"`
	DurationMS   int        `json:"duration_ms"`
	PayloadBytes int        `json:"payload_bytes"`
	WeightName   string     `json:"weight_name"`
	Weights      [3]float64 `json:"weights"`
	FnName       string     `json:"fn_name,omitempty"`
	TracePath    string     `json:"trace_path,omitempty"`
}

func issue5ValidateSpecs(runs []issue5RunSpec) error {
	if len(runs) == 0 {
		return fmt.Errorf("no matrix runs")
	}
	validID := regexp.MustCompile(`^[A-Za-z0-9_-]+$`)
	type block struct {
		seed                     int64
		durationMS, payloadBytes int
		weights                  map[string]bool
	}
	blocks := map[string]*block{}
	ids := map[string]bool{}
	for _, run := range runs {
		if !validID.MatchString(run.ID) || ids[run.ID] {
			return fmt.Errorf("invalid or duplicate run ID %q", run.ID)
		}
		ids[run.ID] = true
		if run.Rate <= 0 || run.DurationMS <= 0 || run.PayloadBytes < 0 || run.Repeat < 1 || run.WeightName == "" || run.Pattern != "steady" && run.Pattern != "burst" {
			return fmt.Errorf("invalid run inputs %q", run.ID)
		}
		sum := 0.0
		for _, w := range run.Weights {
			if w < 0 || math.IsNaN(w) || math.IsInf(w, 0) {
				return fmt.Errorf("invalid weights for %q", run.ID)
			}
			sum += w
		}
		if sum <= 0 {
			return fmt.Errorf("zero weight sum for %q", run.ID)
		}
		key := fmt.Sprintf("%d/%s/%d", run.Rate, run.Pattern, run.Repeat)
		if old := blocks[key]; old != nil {
			if old.seed != run.Seed || old.durationMS != run.DurationMS || old.payloadBytes != run.PayloadBytes || old.weights[run.WeightName] {
				return fmt.Errorf("unpaired or duplicate weight for block %s", key)
			}
			old.weights[run.WeightName] = true
		} else {
			blocks[key] = &block{run.Seed, run.DurationMS, run.PayloadBytes, map[string]bool{run.WeightName: true}}
		}
	}
	return nil
}

type issue5CandidateScore struct {
	NodeID             string  `json:"node_id"`
	StubID             string  `json:"stub_id"`
	Eligible           bool    `json:"eligible"`
	FreeFraction       float64 `json:"free_fraction"`
	PredictedLatencyMS float64 `json:"predicted_latency_ms"`
	Score              float64 `json:"score"`
}

type issue5RawRequest struct {
	ID                string                 `json:"id"`
	FnName            string                 `json:"fn_name"`
	ScheduledOffsetNS int64                  `json:"scheduled_offset_ns"`
	SubmitUnixNS      int64                  `json:"submit_unix_ns"`
	ReceiveUnixNS     int64                  `json:"receive_unix_ns,omitempty"`
	ReplyUnixNS       int64                  `json:"reply_unix_ns"`
	Receiver          string                 `json:"receiver,omitempty"`
	Status            string                 `json:"status"`
	Error             string                 `json:"error,omitempty"`
	Checksum          string                 `json:"checksum,omitempty"`
	LatencyMS         float64                `json:"latency_ms"`
	OnTime            bool                   `json:"on_time"`
	Candidates        []issue5CandidateScore `json:"candidates"`
	InputSHA256       string                 `json:"input_sha256,omitempty"`
	InputBytes        int                    `json:"input_bytes,omitempty"`
}

func issue5Snapshot(r *routine.Routine, candidates []issue5Candidate) []issue5CandidateScore {
	cs := clsstat.Singleton()
	cfg := dev.CurrentConfig()
	now := time.Now()
	deadline := r.SubmitTime + int64(dev.GetToleranceTimeFor(r.FnName))
	out := make([]issue5CandidateScore, 0, len(candidates))
	for _, candidate := range candidates {
		row := issue5CandidateScore{NodeID: candidate.NodeID, StubID: issue5StubID(candidate.NodeID)}
		st := cs.GetStub(row.StubID)
		node := cs.GetNode(candidate.NodeID)
		if st == nil || node == nil {
			out = append(out, row)
			continue
		}
		status, end := st.SchedulingState()
		total, remain, updated := node.Capacity()
		if status != clsstat.STUB_RUNNING || st.Resource == nil || !st.Resource.GreaterOrEqual(r.ResReq) ||
			total.CpuNum <= 0 || updated.IsZero() || now.Sub(updated) > time.Duration(cfg.StateMaxAgeMS)*time.Millisecond {
			out = append(out, row)
			continue
		}
		start := int64(end)
		if start < now.UnixNano() {
			start = now.UnixNano()
		}
		finish := start + int64(dev.GetEstExecTime(candidate.NodeID)) + dev.GetNetworkBudget(candidate.NodeID).Nanoseconds()
		row.PredictedLatencyMS = float64(finish-now.UnixNano()) / 1e6
		row.FreeFraction = float64(remain.CpuNum) / float64(total.CpuNum)
		if finish > deadline {
			out = append(out, row)
			continue
		}
		weight := 1.0
		if w, ok := cfg.DeviceWeights[candidate.NodeID]; ok {
			weight = w
		}
		row.Score = schedpolicy.Score(row.FreeFraction, row.PredictedLatencyMS, weight, cfg.Weights, float64(cfg.LatencyScaleMS))
		row.Eligible = !math.IsInf(row.Score, 0) && !math.IsNaN(row.Score)
		out = append(out, row)
	}
	return out
}

func issue5SubmitObserved(origin *stub, run issue5RunSpec, index int, offset time.Duration,
	candidates []issue5Candidate, qosMS int, wantChecksum uint64, requestID string) issue5RawRequest {
	started := time.Now()
	id := fmt.Sprintf("%s-request-%06d", run.ID, index)
	if requestID != "" {
		id = requestID
	}
	fnName := run.FnName
	if fnName == "" {
		fnName = "cpu"
	}
	payload := make([]byte, run.PayloadBytes)
	inputSHA := ""
	if len(issue12ImagePayloads) > 0 {
		payload = issue12ImagePayloads[(index/2)%len(issue12ImagePayloads)]
		digest := sha256.Sum256(payload)
		inputSHA = fmt.Sprintf("%x", digest)
		wantChecksum = 0
		for i := 7; i >= 0; i-- {
			wantChecksum = wantChecksum<<8 | uint64(digest[i])
		}
	}
	r := routine.NewSubmittedRtn(id, fnName, payload, dev.GetReqResource())
	r.InitSid = origin.Id()
	r.SubmitTime = started.UnixNano()
	out := issue5RawRequest{ID: id, FnName: fnName, ScheduledOffsetNS: offset.Nanoseconds(), SubmitUnixNS: started.UnixNano(), Status: "no_receiver_timeout"}
	if inputSHA != "" {
		out.InputSHA256, out.InputBytes = inputSHA, len(payload)
	}
	out.Candidates = issue5Snapshot(r, candidates)
	origin.rtnStore.Record(r)
	origin.SubmitRoutine(r)
	select {
	case <-r.WaitReceive():
		out.ReceiveUnixNS = time.Now().UnixNano()
	case <-time.After(3 * time.Second):
		out.Error = "no receiver notification within 3 seconds"
		goto finish
	}
	{
		toCloud, receiver := r.Receiver()
		if toCloud || receiver == "" {
			out.Status = "invalid_receiver"
			out.Error = fmt.Sprintf("cloud=%v receiver=%q", toCloud, receiver)
			goto finish
		}
		out.Receiver = receiver
		origin.waiterStore.Push(&waiters.Waiter{RefIds: []string{r.Id}, Exec: &issue5Executor{events: make(chan *interfaces.Event, 1)}, WithData: true})
		wait := &eventpb.RoutineWaitGetEvent{RtnId: r.Id, ReqSid: origin.Id(), WithData: true}
		data, err := proto.Marshal(wait)
		if err != nil {
			out.Status, out.Error = "wait_marshal_error", err.Error()
			goto finish
		}
		if err := cmnct.Singleton().SendToStub(receiver, &event.Event{Type: interfaces.RtnWaitEvt(receiver), Content: data}); err != nil {
			out.Status, out.Error = "wait_send_error", err.Error()
			goto finish
		}
		select {
		case <-r.WaitData():
			out.Checksum = string(r.Result())
			if r.Status() == routine.EXIT_SUCC && out.Checksum == fmt.Sprint(wantChecksum) {
				out.Status = "success"
			} else {
				out.Status = "execution_failure"
				out.Error = fmt.Sprintf("status=%d checksum=%q error=%q", r.Status(), out.Checksum, r.Error())
			}
		case <-time.After(3 * time.Second):
			out.Status, out.Error = "result_timeout", "no result within 3 seconds after receiver notification"
		}
	}
finish:
	out.ReplyUnixNS = time.Now().UnixNano()
	out.LatencyMS = float64(out.ReplyUnixNS-out.SubmitUnixNS) / 1e6
	out.OnTime = out.Status == "success" && out.LatencyMS <= float64(qosMS)
	return out
}

func issue5RunMatrix(t *testing.T, origin *stub, c issue5OriginConfig, cfg dev.Config, wantChecksum uint64) {
	t.Helper()
	if c.OutputDir == "" {
		t.Fatal("matrix requires output_dir")
	}
	if err := issue5ValidateSpecs(c.Runs); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(c.OutputDir, 0755); err != nil {
		t.Fatalf("new output directory required: %v", err)
	}
	defer issue5StartResourceSampler(t, c.ResourcePath)()
	issue5WriteJSON(t, filepath.Join(c.OutputDir, "plan.json"), c)
	if os.Getenv("TMC_ISSUE12_PRECONNECT") == "1" {
		preconnect := make([]map[string]any, 0, len(c.Candidates))
		for _, worker := range c.Candidates {
			started := time.Now()
			err := cmnct.Singleton().SendToAddress(worker.Address)
			row := map[string]any{"node_id": worker.NodeID, "address": worker.Address,
				"start_unix_ns": started.UnixNano(), "duration_ns": time.Since(started).Nanoseconds(),
				"empty_event_rpc": true}
			if err != nil {
				row["error"] = err.Error()
			}
			preconnect = append(preconnect, row)
			issue5WriteJSON(t, filepath.Join(c.OutputDir, "preconnect.json"), preconnect)
			if err != nil {
				t.Fatalf("preconnect %s: %v", worker.NodeID, err)
			}
		}
	}
	warmups := make([]issue5RawRequest, 0, len(c.Candidates))
	for _, target := range c.Candidates {
		warmCfg := cfg
		warmCfg.Weights = [3]float64{1, 0, 0}
		if err := dev.SetConfig(warmCfg); err != nil {
			t.Fatal(err)
		}
		cs := clsstat.Singleton()
		for _, candidate := range c.Candidates {
			free := 0
			if candidate.NodeID == target.NodeID {
				free = candidate.TotalCPU
			}
			cs.UpdateNode(&clsstat.UpdateNodeOpts{Nid: candidate.NodeID, ResRemain: &routine.Resource{CpuNum: free}})
			cs.UpdateStub(issue5StubID(candidate.NodeID), clsstat.SyncEstWaitTime(0, time.Now()))
		}
		run := issue5RunSpec{ID: "warmup-" + target.NodeID, PayloadBytes: c.Runs[0].PayloadBytes, WeightName: "warmup", Weights: warmCfg.Weights}
		if c.WarmupQoSMS > 0 {
			run.FnName = "cpu-warm"
		}
		row := issue5SubmitObserved(origin, run, 0, 0, c.Candidates, c.QoSMS, wantChecksum, "")
		warmups = append(warmups, row)
		issue5WriteJSON(t, filepath.Join(c.OutputDir, "warmups.json"), warmups)
		if row.Status != "success" || row.Receiver != issue5StubID(target.NodeID) {
			t.Fatalf("warmup of %s failed: status=%s receiver=%s", target.NodeID, row.Status, row.Receiver)
		}
	}
	for _, run := range c.Runs {
		cfg.Weights = run.Weights
		if err := dev.SetConfig(cfg); err != nil {
			t.Fatal(err)
		}
		cs := clsstat.Singleton()
		for _, worker := range c.Candidates {
			cs.UpdateNode(&clsstat.UpdateNodeOpts{Nid: worker.NodeID, ResRemain: &routine.Resource{CpuNum: worker.TotalCPU - 2}})
			cs.UpdateStub(issue5StubID(worker.NodeID), clsstat.SyncEstWaitTime(0, time.Now()))
		}
		trace := make([]issue5TraceEntry, 0)
		traceSHA := ""
		if run.TracePath != "" {
			var err error
			trace, traceSHA, err = issue5LoadFrozenTrace(run.TracePath, run.DurationMS)
			if err != nil {
				t.Fatal(err)
			}
		} else {
			for _, offset := range issue5GenerateTrace(run.Seed, run.Rate, time.Duration(run.DurationMS)*time.Millisecond, run.Pattern) {
				trace = append(trace, issue5TraceEntry{OffsetNS: offset.Nanoseconds()})
			}
		}
		rows := make([]issue5RawRequest, len(trace))
		start := time.Now()
		var group sync.WaitGroup
		for i, entry := range trace {
			offset := time.Duration(entry.OffsetNS)
			if pause := time.Until(start.Add(offset)); pause > 0 {
				time.Sleep(pause)
			}
			group.Add(1)
			go func(i int, offset time.Duration, id string) {
				defer group.Done()
				rows[i] = issue5SubmitObserved(origin, run, i, offset, c.Candidates, c.QoSMS, wantChecksum, id)
			}(i, offset, entry.ID)
		}
		group.Wait()
		time.Sleep(100 * time.Millisecond)
		folder := filepath.Join(c.OutputDir, run.ID)
		if err := os.Mkdir(folder, 0755); err != nil {
			t.Fatal(err)
		}
		raw, err := os.OpenFile(filepath.Join(folder, "requests.jsonl"), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0644)
		if err != nil {
			t.Fatal(err)
		}
		enc := json.NewEncoder(raw)
		onTime, success, noReceiver := 0, 0, 0
		for _, row := range rows {
			if err := enc.Encode(row); err != nil {
				t.Fatal(err)
			}
			if row.Status == "success" {
				success++
			}
			if row.Status == "no_receiver_timeout" {
				noReceiver++
			}
			if row.OnTime {
				onTime++
			}
		}
		if err := raw.Close(); err != nil {
			t.Fatal(err)
		}
		issue5WriteJSON(t, filepath.Join(folder, "run.json"), map[string]any{
			"spec": run, "configuration": cfg, "candidates": c.Candidates, "trace_sha256": traceSHA,
			"start_unix_ns": start.UnixNano(), "end_unix_ns": time.Now().UnixNano(),
			"submitted": len(rows), "success": success, "on_time": onTime,
			"no_receiver_timeout":     noReceiver,
			"on_time_goodput_rps":     float64(onTime) * 1000 / float64(run.DurationMS),
			"cohort_on_time_fraction": float64(onTime) / float64(len(rows)),
		})
	}
}

type issue5TraceEntry struct {
	ID       string `json:"id"`
	OffsetNS int64  `json:"scheduled_offset_ns"`
}

func issue5LoadFrozenTrace(path string, windowMS int) ([]issue5TraceEntry, string, error) {
	body, err := os.ReadFile(path)
	if err != nil {
		return nil, "", err
	}
	hash := fmt.Sprintf("%x", sha256.Sum256(body))
	scanner := bufio.NewScanner(strings.NewReader(string(body)))
	var entries []issue5TraceEntry
	seen := map[string]bool{}
	last := int64(-1)
	for scanner.Scan() {
		var row issue5TraceEntry
		if err := json.Unmarshal(scanner.Bytes(), &row); err != nil {
			return nil, "", err
		}
		if row.ID == "" || seen[row.ID] || row.OffsetNS < last || row.OffsetNS > int64(windowMS)*int64(time.Millisecond) {
			return nil, "", fmt.Errorf("invalid frozen trace row %d", len(entries))
		}
		seen[row.ID] = true
		last = row.OffsetNS
		entries = append(entries, row)
	}
	if err := scanner.Err(); err != nil {
		return nil, "", err
	}
	if len(entries) == 0 {
		return nil, "", fmt.Errorf("empty frozen trace")
	}
	return entries, hash, nil
}

func TestIssue5MatrixKeepsPairedTracesAndUniqueRuns(t *testing.T) {
	base := issue5RunSpec{ID: "steady-r1-equal", Rate: 8, Pattern: "steady", Repeat: 1, Seed: 42, DurationMS: 2000, PayloadBytes: 65536, WeightName: "equal", Weights: [3]float64{1, 1, 1}}
	paired := base
	paired.ID = "steady-r1-latency"
	paired.WeightName = "latency"
	paired.Weights = [3]float64{0, .9, .1}
	if err := issue5ValidateSpecs([]issue5RunSpec{base, paired}); err != nil {
		t.Fatalf("valid paired runs rejected: %v", err)
	}
	wrongSeed := paired
	wrongSeed.Seed++
	if err := issue5ValidateSpecs([]issue5RunSpec{base, wrongSeed}); err == nil {
		t.Fatal("same block with different seeds must fail")
	}
	duplicate := paired
	duplicate.ID = base.ID
	if err := issue5ValidateSpecs([]issue5RunSpec{base, duplicate}); err == nil {
		t.Fatal("duplicate run ID must fail")
	}
}

func TestIssue5UsesMeasuredPerNodeExecutionEstimates(t *testing.T) {
	c := issue5OriginConfig{QoSMS: 500, ExecutionMS: 10,
		Candidates:      []issue5Candidate{{NodeID: "near", NetworkMS: 1}, {NodeID: "far", NetworkMS: 22}},
		NodeExecutionMS: map[string]int{"near": 2, "far": 7}}
	cfg, err := issue5OriginScheduleConfig(c)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.NodeExecutionMS[issue5OriginID] <= c.QoSMS || cfg.NodeExecutionMS["near"] != 2 || cfg.NodeExecutionMS["far"] != 7 {
		t.Fatalf("per-node estimates not applied: %+v", cfg.NodeExecutionMS)
	}
}
