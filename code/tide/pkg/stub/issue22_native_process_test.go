package stub

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/cmnct"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/event"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/processor"
	"github.com/ict/tide/pkg/revisionworkload"
	"github.com/ict/tide/pkg/routine"
	"github.com/ict/tide/pkg/server"
	"github.com/ict/tide/pkg/server/handlers/clsstathdlr"
	"github.com/ict/tide/pkg/server/handlers/stubhdlr"
	"github.com/ict/tide/pkg/stub/waiters"
	"github.com/ict/tide/proto/eventpb"
	"github.com/sirupsen/logrus"
	"google.golang.org/grpc"
	"google.golang.org/protobuf/proto"
	"net"
	"os"
	"os/signal"
	"path/filepath"
	"sync"
	"syscall"
	"testing"
	"time"
)

type issue22Spans struct {
	mu      sync.Mutex
	file    *os.File
	enc     *json.Encoder
	node    string
	started time.Time
}

func issue22OpenSpans(t *testing.T, path, node string) *issue22Spans {
	if path == "" {
		return nil
	}
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0644)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { f.Close() })
	return &issue22Spans{file: f, enc: json.NewEncoder(f), node: node, started: time.Now()}
}
func (s *issue22Spans) emit(stage, rid string, extra map[string]any) {
	if s == nil {
		return
	}
	row := map[string]any{"stage": stage, "request_id": rid, "node": s.node, "unix_ns": time.Now().UnixNano(), "monotonic_ns": time.Since(s.started).Nanoseconds()}
	for k, v := range extra {
		row[k] = v
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if err := s.enc.Encode(row); err != nil {
		panic(err)
	}
}

type issue22ObservedStub struct {
	interfaces.Stub
	spans *issue22Spans
}

type issue22Dispatcher struct {
	event.EventDispatcher
	spans  *issue22Spans
	mu     sync.Mutex
	counts map[string]int
}

func issue22DispatcherFor(spans *issue22Spans) event.EventDispatcher {
	d := event.NewEventDispatcher()
	if spans == nil {
		return d
	}
	return &issue22Dispatcher{EventDispatcher: d, spans: spans, counts: map[string]int{}}
}
func (d *issue22Dispatcher) DispatchEvent(evt *event.Event) {
	if evt.Type == interfaces.NodeSyncEvt || evt.Type == interfaces.StubSyncEvt {
		key := evt.From + evt.Type
		d.mu.Lock()
		d.counts[key]++
		count := d.counts[key]
		d.mu.Unlock()
		if count == 1 || count%20 == 0 {
			d.spans.emit("native_state_sync_ingress", "", map[string]any{"from": evt.From, "event_type": evt.Type, "count": count})
		}
	}
	d.EventDispatcher.DispatchEvent(evt)
}

func (s *issue22ObservedStub) SubmitRoutine(r *routine.Routine) {
	s.spans.emit("submit_handler_enter", r.Id, nil)
	s.Stub.SubmitRoutine(r)
	s.spans.emit("submit_handler_return", r.Id, nil)
}
func (s *issue22ObservedStub) SetReceiver(rid string, cloud bool, receiver string) {
	s.spans.emit("receiver_event_ingress", rid, map[string]any{"receiver": receiver})
	s.Stub.SetReceiver(rid, cloud, receiver)
}
func (s *issue22ObservedStub) NoticeRtnData(rid string, status routine.Status, result []byte, err string) {
	s.spans.emit("result_event_ingress", rid, nil)
	s.Stub.NoticeRtnData(rid, status, result, err)
}
func (s *issue22ObservedStub) WaitRtnDone(rid string) <-chan *routine.Routine {
	s.spans.emit("wait_get_ingress", rid, nil)
	original := s.Stub.WaitRtnDone(rid)
	out := make(chan *routine.Routine, 1)
	go func() { r := <-original; s.spans.emit("result_available_to_handler", rid, nil); out <- r; close(out) }()
	return out
}
func issue22Register(d event.EventDispatcher, st *stub, spans *issue22Spans) {
	var observed interfaces.Stub = st
	if spans != nil {
		observed = &issue22ObservedStub{Stub: st, spans: spans}
	}
	stubhdlr.RegisterEventHandler(d, observed)
}

type issue22Pool struct {
	kernel revisionworkload.Config
	spans  *issue22Spans
}

func (p *issue22Pool) Get() interfaces.Executor {
	return &issue22Executor{issue5Executor: &issue5Executor{events: make(chan *interfaces.Event, 1), kernel: p.kernel}, spans: p.spans}
}
func (p *issue22Pool) Return(e interfaces.Executor) {
	r := e.GetRoutine()
	p.spans.emit("executor_returned", r.Id, nil)
}
func (*issue22Pool) IncInstance(int) {}
func (*issue22Pool) Close() error    { return nil }

type issue22Executor struct {
	*issue5Executor
	spans *issue22Spans
}

func (e *issue22Executor) BindRtn(r *routine.Routine) error {
	err := e.issue5Executor.BindRtn(r)
	p := e.GetProcessor()
	e.spans.emit("processor_bound", r.Id, map[string]any{"processor_id": p.Id, "logical_cpu": p.LogicRes.CpuNum})
	return err
}
func (e *issue22Executor) BindProc(p *processor.Processor) error { return e.issue5Executor.BindProc(p) }
func (e *issue22Executor) Execute() error {
	r := e.GetRoutine()
	e.spans.emit("execute_start", r.Id, nil)
	go func() {
		var result revisionworkload.Result
		var err error
		e.spans.emit("adapter_begin", r.Id, nil)
		if endpoint := os.Getenv("TMC_ISSUE12_IMAGE_ENDPOINT"); endpoint != "" {
			result, err = issue12ExecuteImage(endpoint, r)
		} else {
			result, err = revisionworkload.Run(e.kernel)
		}
		e.spans.emit("adapter_return", r.Id, nil)
		evt := &interfaces.Event{Type: interfaces.EXIT, Status: routine.EXIT_SUCC, Result: []byte(fmt.Sprint(result.Checksum))}
		if err != nil {
			evt.Status = routine.EXIT_FAIL
			evt.Error = err.Error()
		}
		e.events <- evt
	}()
	return nil
}

const issue22ProbeEvent = "/issue22/readiness_probe"
const issue22AckEvent = "/issue22/readiness_ack"

func issue22WaitPeers(ctx context.Context, peers []string, send func(string) error, ready <-chan string) error {
	pending := map[string]bool{}
	for _, peer := range peers {
		pending[peer] = true
	}
	timer := time.NewTicker(100 * time.Millisecond)
	defer timer.Stop()
	for len(pending) > 0 {
		for peer := range pending {
			_ = send(peer)
		}
		select {
		case peer := <-ready:
			delete(pending, peer)
		case <-timer.C:
		case <-ctx.Done():
			return fmt.Errorf("bidirectional readiness failed for %v: %w", pending, ctx.Err())
		}
	}
	return nil
}

func TestIssue22ReadinessRequiresReverseAck(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 250*time.Millisecond)
	defer cancel()
	ready := make(chan string, 1)
	calls := 0
	err := issue22WaitPeers(ctx, []string{"worker"}, func(peer string) error {
		calls++
		if calls == 2 {
			ready <- peer
		}
		return nil
	}, ready)
	if err != nil || calls != 2 {
		t.Fatalf("readiness calls=%d error=%v", calls, err)
	}
	ctx2, cancel2 := context.WithTimeout(context.Background(), 10*time.Millisecond)
	defer cancel2()
	if issue22WaitPeers(ctx2, []string{"worker"}, func(string) error { return nil }, make(chan string)) == nil {
		t.Fatal("qualified without reverse acknowledgement")
	}
}

type issue22WorkerConfig struct {
	issue5WorkerConfig
	SpansPath string `json:"spans_path"`
	Heartbeat bool   `json:"heartbeat"`
}
type issue22OriginConfig struct {
	issue5OriginConfig
	SpansPath   string `json:"spans_path"`
	Entry       string `json:"entry"`
	MaxInflight int    `json:"max_inflight"`
}

func TestIssue22WorkerProcess(t *testing.T) {
	path := os.Getenv("TMC_ISSUE5_WORKER_CONFIG")
	if path == "" {
		t.Skip("helper process")
	}
	logrus.SetLevel(logrus.ErrorLevel)
	if os.Getenv("TMC_ISSUE12_DEBUG") == "1" {
		logrus.SetLevel(logrus.DebugLevel)
	}
	var c issue22WorkerConfig
	issue5ReadJSON(t, path, &c)
	if c.NodeID == "" || c.TotalCPU < 2 || c.ReadyPath == "" {
		t.Fatal("invalid worker configuration")
	}
	defer issue5StartResourceSampler(t, c.ResourcePath)()
	spans := issue22OpenSpans(t, c.SpansPath, c.NodeID)
	qos, execution := c.QoSMS, c.ExecutionMS
	if qos == 0 {
		qos = 500
	}
	if execution == 0 {
		execution = 10
	}
	if err := dev.SetConfig(issue5WithWarmupQoS(issue5BaseConfig(qos, execution), c.WarmupQoSMS)); err != nil {
		t.Fatal(err)
	}
	lis, err := net.Listen("tcp", c.Listen)
	if err != nil {
		t.Fatal(err)
	}
	defer lis.Close()
	cs := clsstat.Singleton()
	clsstat.Init(c.NodeID, c.Listen, clsstat.EDGE, &routine.Resource{CpuNum: c.TotalCPU})
	cs.AddNode(&clsstat.AddNodeOpts{Nid: issue5OriginID, Address: c.OriginAddr, Role: clsstat.THINGS, ResTotal: &routine.Resource{CpuNum: 2}})
	cs.AddApp(&clsstat.AddAppOpts{AppId: issue5App})
	cs.AddStub(&clsstat.AddStubOpts{StubId: issue5OriginStubID, AppId: issue5App, NodeId: issue5OriginID, Mode: interfaces.StubFn})
	d := issue22DispatcherFor(spans)
	cmnct.Init(c.NodeID, c.Listen, d)
	st, pm := issue5NewStub(t, c.TotalCPU, c.Workload)
	st.id = issue5StubID(c.NodeID)
	st.execPool = &issue22Pool{kernel: c.Workload, spans: spans}
	defer st.Close()
	defer pm.Close()
	issue22Register(d, st, spans)
	d.RegisterHandler(issue22ProbeEvent, func(evt *event.Event) {
		_ = cmnct.Singleton().SendToAddress(c.OriginAddr, &event.Event{Type: issue22AckEvent, Content: []byte(c.NodeID)})
	})
	if c.Heartbeat {
		clsstathdlr.RegisterEventHandler(d)
		pm.StartHeartbeat()
	}
	svr := grpc.NewServer()
	eventpb.RegisterEventServiceServer(svr, server.NewEventServer(d))
	go func() { _ = svr.Serve(lis) }()
	defer svr.Stop()
	issue5WriteJSON(t, c.ReadyPath, map[string]any{"node_id": c.NodeID, "address": c.Listen, "stub_id": st.Id(), "total_cpu": c.TotalCPU, "remaining_cpu": c.TotalCPU - 2})
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGTERM, os.Interrupt)
	defer signal.Stop(sig)
	select {
	case <-sig:
	case <-time.After(45 * time.Minute):
	}
}

func TestIssue22OriginProcess(t *testing.T) {
	path := os.Getenv("TMC_ISSUE5_ORIGIN_CONFIG")
	if path == "" {
		t.Skip("helper process")
	}
	logrus.SetLevel(logrus.ErrorLevel)
	if os.Getenv("TMC_ISSUE12_DEBUG") == "1" {
		logrus.SetLevel(logrus.DebugLevel)
	}
	var c issue22OriginConfig
	issue5ReadJSON(t, path, &c)
	if err := issue12LoadImagePayloads(); err != nil {
		t.Fatal(err)
	}
	if len(c.Candidates) < 1 || len(c.Candidates) > 3 || c.QoSMS < 1 || c.ExecutionMS < 1 {
		t.Fatal("domain origin requires three candidates and positive timing values")
	}
	cfg, err := issue5OriginScheduleConfig(c.issue5OriginConfig)
	if err != nil {
		t.Fatal(err)
	}
	if err := dev.SetConfig(cfg); err != nil {
		t.Fatal(err)
	}
	lis, err := net.Listen("tcp", c.Listen)
	if err != nil {
		t.Fatal(err)
	}
	defer lis.Close()
	cs := clsstat.Singleton()
	clsstat.Init(issue5OriginID, issue5OriginAddress(c.issue5OriginConfig), clsstat.THINGS, &routine.Resource{CpuNum: 2})
	cs.AddApp(&clsstat.AddAppOpts{AppId: issue5App})
	spans := issue22OpenSpans(t, c.SpansPath, "origin")
	d := issue22DispatcherFor(spans)
	cmnct.Init(issue5OriginID, issue5OriginAddress(c.issue5OriginConfig), d)
	origin, pm := issue5NewStub(t, 2, c.Workload)
	origin.id = issue5OriginStubID
	defer origin.Close()
	defer pm.Close()
	issue22Register(d, origin, spans)
	clsstathdlr.RegisterEventHandler(d)
	ready := make(chan string, 100)
	d.RegisterHandler(issue22AckEvent, func(evt *event.Event) {
		select {
		case ready <- string(evt.Content):
		default:
		}
	})
	svr := grpc.NewServer()
	eventpb.RegisterEventServiceServer(svr, server.NewEventServer(d))
	go func() { _ = svr.Serve(lis) }()
	defer svr.Stop()
	for _, worker := range c.Candidates {
		cs.AddNode(&clsstat.AddNodeOpts{Nid: worker.NodeID, Address: worker.Address, Role: clsstat.EDGE, ResTotal: &routine.Resource{CpuNum: worker.TotalCPU}})
		cs.UpdateNode(&clsstat.UpdateNodeOpts{Nid: worker.NodeID, ResRemain: &routine.Resource{CpuNum: worker.TotalCPU - 2}})
		cs.AddStub(&clsstat.AddStubOpts{StubId: issue5StubID(worker.NodeID), AppId: issue5App, NodeId: worker.NodeID, Mode: interfaces.StubFn})
	}
	peerAddresses := map[string]string{}
	peers := []string{}
	for _, worker := range c.Candidates {
		peers = append(peers, worker.NodeID)
		peerAddresses[worker.NodeID] = worker.Address
	}
	readyCtx, readyCancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer readyCancel()
	if err := issue22WaitPeers(readyCtx, peers, func(peer string) error {
		return cmnct.Singleton().SendToAddress(peerAddresses[peer], &event.Event{Type: issue22ProbeEvent})
	}, ready); err != nil {
		t.Fatal(err)
	}
	spans.emit("bidirectional_readiness_pass", "", nil)
	want, err := revisionworkload.Run(c.Workload)
	if err != nil {
		t.Fatal(err)
	}
	if len(c.Runs) > 0 {
		issue22RunMatrix(t, origin, c, cfg, want.Checksum, spans)
		return
	}
	t.Fatal("domain experiment requires explicit frozen run specs")
}

func issue22Submit(origin *stub, run issue5RunSpec, index int, entry issue5TraceEntry, candidates []issue5Candidate, qos int, started time.Time, public bool, spans *issue22Spans) issue5RawRequest {
	payload := issue12ImagePayloads[(index/2)%len(issue12ImagePayloads)]
	hash := sha256.Sum256(payload)
	want := uint64(0)
	for i := 7; i >= 0; i-- {
		want = want<<8 | uint64(hash[i])
	}
	r := routine.NewSubmittedRtn(entry.ID, "cpu", payload, dev.GetReqResource())
	r.InitSid = origin.Id()
	r.SubmitTime = started.UnixNano()
	out := issue5RawRequest{ID: entry.ID, FnName: "cpu", ScheduledOffsetNS: entry.OffsetNS, SubmitUnixNS: started.UnixNano(), Status: "no_receiver_timeout", InputSHA256: fmt.Sprintf("%x", hash), InputBytes: len(payload), Candidates: issue5Snapshot(r, candidates)}
	spans.emit("submit_start", r.Id, map[string]any{"public_entry": public})
	ctx, cancel := context.WithTimeout(context.Background(), 6*time.Second)
	defer cancel()
	if public {
		if err := SubmitExternal(ctx, origin, r); err != nil {
			out.Error = err.Error()
			goto finish
		}
	} else {
		origin.rtnStore.Record(r)
		origin.SubmitRoutine(r)
	}
	select {
	case <-r.WaitReceive():
		out.ReceiveUnixNS = time.Now().UnixNano()
	case <-ctx.Done():
		out.Error = "no receiver before diagnostic timeout"
		goto finish
	}
	{
		cloud, receiver := r.Receiver()
		out.Receiver = receiver
		if cloud || receiver == "" {
			out.Status = "invalid_receiver"
			out.Error = r.Error()
			goto finish
		}
		if !public {
			origin.waiterStore.Push(&waiters.Waiter{RefIds: []string{r.Id}, Exec: &issue5Executor{events: make(chan *interfaces.Event, 1)}, WithData: true})
			wait := &eventpb.RoutineWaitGetEvent{RtnId: r.Id, ReqSid: origin.Id(), WithData: true}
			data, err := proto.Marshal(wait)
			if err != nil {
				out.Status = "wait_marshal_error"
				out.Error = err.Error()
				goto finish
			}
			spans.emit("wait_get_send_begin", r.Id, nil)
			if err := cmnct.Singleton().SendToStub(receiver, &event.Event{Type: interfaces.RtnWaitEvt(receiver), Content: data}); err != nil {
				out.Status = "wait_send_error"
				out.Error = err.Error()
				goto finish
			}
			spans.emit("wait_get_send_return", r.Id, nil)
		}
		select {
		case <-r.WaitData():
			out.Checksum = string(r.Result())
			if r.Status() == routine.EXIT_SUCC && out.Checksum == fmt.Sprint(want) {
				out.Status = "success"
			} else {
				out.Status = "execution_failure"
				out.Error = r.Error()
			}
		case <-ctx.Done():
			out.Status = "result_timeout"
			out.Error = "result deadline exceeded diagnostic timeout"
		}
	}
finish:
	out.ReplyUnixNS = time.Now().UnixNano()
	out.LatencyMS = float64(out.ReplyUnixNS-out.SubmitUnixNS) / 1e6
	out.OnTime = out.Status == "success" && out.LatencyMS <= float64(qos)
	spans.emit("caller_return", r.Id, map[string]any{"status": out.Status})
	return out
}

func issue22RunMatrix(t *testing.T, origin *stub, c issue22OriginConfig, cfg dev.Config, want uint64, spans *issue22Spans) {
	if err := os.Mkdir(c.OutputDir, 0755); err != nil {
		t.Fatal(err)
	}
	defer issue5StartResourceSampler(t, c.ResourcePath)()
	for _, worker := range c.Candidates {
		if err := cmnct.Singleton().SendToAddress(worker.Address); err != nil {
			t.Fatal(err)
		}
	}
	warmups := []issue5RawRequest{}
	for _, target := range c.Candidates {
		warmCfg := cfg
		warmCfg.Weights = [3]float64{1, 0, 0}
		warmCfg.NodeExecutionMS = map[string]int{}
		for node, estimate := range cfg.NodeExecutionMS {
			warmCfg.NodeExecutionMS[node] = estimate
		}
		for _, worker := range c.Candidates {
			if worker.NodeID != target.NodeID {
				warmCfg.NodeExecutionMS[worker.NodeID] = c.WarmupQoSMS + 1
			}
		}
		if err := dev.SetConfig(warmCfg); err != nil {
			t.Fatal(err)
		}
		for _, worker := range c.Candidates {
			free := 0
			if worker.NodeID == target.NodeID {
				free = worker.TotalCPU
			}
			clsstat.Singleton().UpdateNode(&clsstat.UpdateNodeOpts{Nid: worker.NodeID, ResRemain: &routine.Resource{CpuNum: free}})
			clsstat.Singleton().UpdateStub(issue5StubID(worker.NodeID), clsstat.SyncEstWaitTime(0, time.Now()))
		}
		run := issue5RunSpec{ID: "warmup-" + target.NodeID, FnName: "cpu-warm", WeightName: "warmup", Weights: warmCfg.Weights}
		row := issue5SubmitObserved(origin, run, 0, 0, c.Candidates, c.QoSMS, want, "")
		warmups = append(warmups, row)
		if row.Status != "success" || row.Receiver != issue5StubID(target.NodeID) {
			t.Fatalf("warmup failed: %+v", row)
		}
	}
	issue5WriteJSON(t, filepath.Join(c.OutputDir, "warmups.json"), warmups)
	limit := c.MaxInflight
	if limit == 0 {
		limit = 48
	}
	for _, run := range c.Runs {
		cfg.Weights = run.Weights
		if err := dev.SetConfig(cfg); err != nil {
			t.Fatal(err)
		}
		for _, worker := range c.Candidates {
			clsstat.Singleton().UpdateNode(&clsstat.UpdateNodeOpts{Nid: worker.NodeID, ResRemain: &routine.Resource{CpuNum: worker.TotalCPU - 2}})
			clsstat.Singleton().UpdateStub(issue5StubID(worker.NodeID), clsstat.SyncEstWaitTime(0, time.Now()))
		}
		trace, hash, err := issue5LoadFrozenTrace(run.TracePath, run.DurationMS)
		if err != nil {
			t.Fatal(err)
		}
		rows := make([]issue5RawRequest, len(trace))
		sem := make(chan struct{}, limit)
		var group sync.WaitGroup
		start := time.Now()
		for i, entry := range trace {
			if pause := time.Until(start.Add(time.Duration(entry.OffsetNS))); pause > 0 {
				time.Sleep(pause)
			}
			emitted := time.Now()
			group.Add(1)
			go func(i int, entry issue5TraceEntry, emitted time.Time) {
				defer group.Done()
				sem <- struct{}{}
				defer func() { <-sem }()
				rows[i] = issue22Submit(origin, run, i, entry, c.Candidates, c.QoSMS, emitted, c.Entry == "external", spans)
			}(i, entry, emitted)
		}
		group.Wait()
		time.Sleep(200 * time.Millisecond)
		folder := filepath.Join(c.OutputDir, run.ID)
		if err := os.Mkdir(folder, 0755); err != nil {
			t.Fatal(err)
		}
		file, err := os.OpenFile(filepath.Join(folder, "requests.jsonl"), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0644)
		if err != nil {
			t.Fatal(err)
		}
		encoder := json.NewEncoder(file)
		onTime, success := 0, 0
		for _, row := range rows {
			if err := encoder.Encode(row); err != nil {
				t.Fatal(err)
			}
			if row.OnTime {
				onTime++
			}
			if row.Status == "success" {
				success++
			}
		}
		file.Close()
		issue5WriteJSON(t, filepath.Join(folder, "run.json"), map[string]any{"spec": run, "configuration": cfg, "candidates": c.Candidates, "trace_sha256": hash, "entry": c.Entry, "max_inflight": limit, "start_unix_ns": start.UnixNano(), "end_unix_ns": time.Now().UnixNano(), "submitted": len(rows), "success": success, "on_time": onTime, "on_time_goodput_rps": float64(onTime) * 1000 / float64(run.DurationMS), "cohort_on_time_fraction": float64(onTime) / float64(len(rows))})
	}
}
