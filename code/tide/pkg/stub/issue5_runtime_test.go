package stub

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"sync"
	"syscall"
	"testing"
	"time"

	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/cmnct"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/event"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/processor"
	"github.com/ict/tide/pkg/procmgr"
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
)

const issue5App = "issue5-native"
const issue5OriginID = "issue5-origin"
const issue5OriginStubID = "issue5-origin-stub"

type issue5WorkerConfig struct {
	NodeID       string                  `json:"node_id"`
	Listen       string                  `json:"listen"`
	OriginAddr   string                  `json:"origin_addr"`
	TotalCPU     int                     `json:"total_cpu"`
	ExecutionMS  int                     `json:"execution_ms,omitempty"`
	QoSMS        int                     `json:"qos_ms,omitempty"`
	WarmupQoSMS  int                     `json:"warmup_qos_ms,omitempty"`
	Workload     revisionworkload.Config `json:"workload"`
	ReadyPath    string                  `json:"ready_path"`
	ResourcePath string                  `json:"resource_path,omitempty"`
}

type issue5Candidate struct {
	NodeID    string `json:"node_id"`
	Address   string `json:"address"`
	TotalCPU  int    `json:"total_cpu"`
	NetworkMS int    `json:"network_ms"`
}

type issue5OriginConfig struct {
	Listen          string                  `json:"listen"`
	AdvertiseAddr   string                  `json:"advertise_addr,omitempty"`
	Candidates      []issue5Candidate       `json:"candidates"`
	Workload        revisionworkload.Config `json:"workload"`
	OutputPath      string                  `json:"output_path"`
	OutputDir       string                  `json:"output_dir,omitempty"`
	Runs            []issue5RunSpec         `json:"runs,omitempty"`
	QoSMS           int                     `json:"qos_ms"`
	WarmupQoSMS     int                     `json:"warmup_qos_ms,omitempty"`
	ExecutionMS     int                     `json:"execution_ms"`
	NodeExecutionMS map[string]int          `json:"node_execution_ms,omitempty"`
	ResourcePath    string                  `json:"resource_path,omitempty"`
}

func issue5OriginAddress(c issue5OriginConfig) string {
	if c.AdvertiseAddr != "" {
		return c.AdvertiseAddr
	}
	return c.Listen
}

type issue5Outcome struct {
	WeightName string     `json:"weight_name"`
	Weights    [3]float64 `json:"weights"`
	Receiver   string     `json:"receiver"`
	Status     string     `json:"status"`
	Checksum   string     `json:"checksum"`
	LatencyMS  float64    `json:"latency_ms"`
}

func issue5StubID(node string) string { return "issue5-worker-" + node }

func issue5ReadJSON(t *testing.T, path string, dst any) {
	t.Helper()
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(body, dst); err != nil {
		t.Fatal(err)
	}
}

func issue5WriteJSON(t *testing.T, path string, v any) {
	t.Helper()
	body, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, append(body, '\n'), 0600); err != nil {
		t.Fatal(err)
	}
}

type issue5Executor struct {
	mu     sync.Mutex
	rtn    *routine.Routine
	proc   *processor.Processor
	events chan *interfaces.Event
	kernel revisionworkload.Config
}

func (e *issue5Executor) Id() string { return "issue5-controlled-executor" }
func (e *issue5Executor) GetRoutine() *routine.Routine {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.rtn
}
func (e *issue5Executor) GetProcessor() *processor.Processor {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.proc
}
func (e *issue5Executor) GetEventChan() <-chan *interfaces.Event { return e.events }
func (e *issue5Executor) BindRtn(r *routine.Routine) error {
	e.mu.Lock()
	e.rtn = r
	e.mu.Unlock()
	return nil
}
func (e *issue5Executor) BindProc(p *processor.Processor) error {
	e.mu.Lock()
	e.proc = p
	e.mu.Unlock()
	return nil
}
func (e *issue5Executor) UnbindRtn() (*routine.Routine, error)      { return e.GetRoutine(), nil }
func (e *issue5Executor) UnbindProc() (*processor.Processor, error) { return e.GetProcessor(), nil }
func (e *issue5Executor) SendEvt(*interfaces.Event) error           { return nil }
func (e *issue5Executor) Close() error                              { return nil }
func (e *issue5Executor) Execute() error {
	go func() {
		var result revisionworkload.Result
		var err error
		if endpoint := os.Getenv("TMC_ISSUE12_IMAGE_ENDPOINT"); endpoint != "" {
			result, err = issue12ExecuteImage(endpoint, e.GetRoutine())
		} else {
			result, err = revisionworkload.Run(e.kernel)
		}
		evt := &interfaces.Event{Type: interfaces.EXIT, Status: routine.EXIT_SUCC, Result: []byte(fmt.Sprint(result.Checksum))}
		if err != nil {
			evt.Status, evt.Error = routine.EXIT_FAIL, err.Error()
		}
		e.events <- evt
	}()
	return nil
}

type issue5Pool struct{ kernel revisionworkload.Config }

func (p *issue5Pool) Get() interfaces.Executor {
	return &issue5Executor{events: make(chan *interfaces.Event, 1), kernel: p.kernel}
}
func (*issue5Pool) Return(interfaces.Executor) {}
func (*issue5Pool) IncInstance(int)            {}
func (*issue5Pool) Close() error               { return nil }

func issue5NewStub(t *testing.T, totalCPU int, kernel revisionworkload.Config) (*stub, *procmgr.ProcessorManager) {
	t.Helper()
	pm := procmgr.NewProcMgr(&routine.Resource{CpuNum: totalCPU})
	ctx := context.WithValue(context.Background(), clsstat.ApplicationId("appid"), issue5App)
	p, err := pm.Apply(ctx, dev.GetStubResource())
	if err != nil || p == nil {
		t.Fatalf("reserve stub CPU: %v", err)
	}
	st := NewStub(&CreateStubOpts{AppId: issue5App, Mode: interfaces.StubFn,
		ResReq: dev.GetStubResource(), ExecPool: &issue5Pool{kernel: kernel}, Proc: p, ProcM: pm}).(*stub)
	deadline := time.Now().Add(time.Second)
	for {
		st.mu.Lock()
		ready := st.status == RUNNING
		st.mu.Unlock()
		if ready {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("function stub did not start")
		}
		time.Sleep(time.Millisecond)
	}
	return st, pm
}

func issue5BaseConfig(qos, execution int) dev.Config {
	c := dev.DefaultConfig()
	c.TTLMS = 3600000
	c.QoSMS = qos
	c.ExecutionMS = execution
	c.StateMaxAgeMS = 3600000
	return c
}

func issue5WithWarmupQoS(cfg dev.Config, warmupMS int) dev.Config {
	if warmupMS > 0 {
		cfg.QoSByFunctionMS = map[string]int{"cpu-warm": warmupMS}
	}
	return cfg
}

func issue5OriginScheduleConfig(c issue5OriginConfig) (dev.Config, error) {
	if c.QoSMS < 1 || c.ExecutionMS < 1 {
		return dev.Config{}, fmt.Errorf("positive QoS and execution estimate required")
	}
	cfg := issue5WithWarmupQoS(issue5BaseConfig(c.QoSMS, c.ExecutionMS), c.WarmupQoSMS)
	localEstimate := c.QoSMS
	if c.WarmupQoSMS > localEstimate {
		localEstimate = c.WarmupQoSMS
	}
	cfg.NodeExecutionMS = map[string]int{issue5OriginID: localEstimate + 1}
	cfg.NetworkMS = map[string]int{}
	for _, worker := range c.Candidates {
		if worker.NodeID == "" || worker.NetworkMS < 0 {
			return dev.Config{}, fmt.Errorf("invalid candidate network budget")
		}
		cfg.NetworkMS[worker.NodeID] = worker.NetworkMS
		ms := c.ExecutionMS
		if value, ok := c.NodeExecutionMS[worker.NodeID]; ok {
			ms = value
		}
		if ms < 1 {
			return dev.Config{}, fmt.Errorf("invalid execution estimate for %s", worker.NodeID)
		}
		cfg.NodeExecutionMS[worker.NodeID] = ms
	}
	return cfg, nil
}

func TestIssue5WorkerProcess(t *testing.T) {
	path := os.Getenv("TMC_ISSUE5_WORKER_CONFIG")
	if path == "" {
		t.Skip("helper process")
	}
	logrus.SetLevel(logrus.ErrorLevel)
	if os.Getenv("TMC_ISSUE12_DEBUG") == "1" {
		logrus.SetLevel(logrus.DebugLevel)
	}
	var c issue5WorkerConfig
	issue5ReadJSON(t, path, &c)
	if c.NodeID == "" || c.TotalCPU < 2 || c.ReadyPath == "" {
		t.Fatal("invalid worker configuration")
	}
	defer issue5StartResourceSampler(t, c.ResourcePath)()
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
	d := event.NewEventDispatcher()
	cmnct.Init(c.NodeID, c.Listen, d)
	st, pm := issue5NewStub(t, c.TotalCPU, c.Workload)
	st.id = issue5StubID(c.NodeID)
	defer st.Close()
	defer pm.Close()
	stubhdlr.RegisterEventHandler(d, st)
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

func TestIssue5OriginProcess(t *testing.T) {
	path := os.Getenv("TMC_ISSUE5_ORIGIN_CONFIG")
	if path == "" {
		t.Skip("helper process")
	}
	logrus.SetLevel(logrus.ErrorLevel)
	if os.Getenv("TMC_ISSUE12_DEBUG") == "1" {
		logrus.SetLevel(logrus.DebugLevel)
	}
	var c issue5OriginConfig
	issue5ReadJSON(t, path, &c)
	if err := issue12LoadImagePayloads(); err != nil {
		t.Fatal(err)
	}
	if len(c.Candidates) != 2 || c.QoSMS < 1 || c.ExecutionMS < 1 {
		t.Fatal("origin requires two candidates and positive timing values")
	}
	cfg, err := issue5OriginScheduleConfig(c)
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
	clsstat.Init(issue5OriginID, issue5OriginAddress(c), clsstat.THINGS, &routine.Resource{CpuNum: 2})
	cs.AddApp(&clsstat.AddAppOpts{AppId: issue5App})
	d := event.NewEventDispatcher()
	cmnct.Init(issue5OriginID, issue5OriginAddress(c), d)
	origin, pm := issue5NewStub(t, 2, c.Workload)
	origin.id = issue5OriginStubID
	defer origin.Close()
	defer pm.Close()
	stubhdlr.RegisterEventHandler(d, origin)
	clsstathdlr.RegisterEventHandler(d)
	svr := grpc.NewServer()
	eventpb.RegisterEventServiceServer(svr, server.NewEventServer(d))
	go func() { _ = svr.Serve(lis) }()
	defer svr.Stop()
	for _, worker := range c.Candidates {
		cs.AddNode(&clsstat.AddNodeOpts{Nid: worker.NodeID, Address: worker.Address, Role: clsstat.EDGE, ResTotal: &routine.Resource{CpuNum: worker.TotalCPU}})
		cs.UpdateNode(&clsstat.UpdateNodeOpts{Nid: worker.NodeID, ResRemain: &routine.Resource{CpuNum: worker.TotalCPU - 2}})
		cs.AddStub(&clsstat.AddStubOpts{StubId: issue5StubID(worker.NodeID), AppId: issue5App, NodeId: worker.NodeID, Mode: interfaces.StubFn})
	}
	want, err := revisionworkload.Run(c.Workload)
	if err != nil {
		t.Fatal(err)
	}
	if len(c.Runs) > 0 {
		issue5RunMatrix(t, origin, c, cfg, want.Checksum)
		return
	}
	var outcomes []issue5Outcome
	for _, choice := range []struct {
		name    string
		weights [3]float64
	}{
		{"equal", [3]float64{1, 1, 1}}, {"latency", [3]float64{0, .9, .1}},
	} {
		cfg.Weights = choice.weights
		if err := dev.SetConfig(cfg); err != nil {
			t.Fatal(err)
		}
		for _, worker := range c.Candidates {
			cs.UpdateNode(&clsstat.UpdateNodeOpts{Nid: worker.NodeID, ResRemain: &routine.Resource{CpuNum: worker.TotalCPU - 2}})
			cs.UpdateStub(issue5StubID(worker.NodeID), clsstat.SyncEstWaitTime(0, time.Now()))
		}
		outcomes = append(outcomes, issue5Submit(t, origin, choice.name, choice.weights, want.Checksum))
	}
	issue5WriteJSON(t, c.OutputPath, outcomes)
}

func issue5Submit(t *testing.T, origin *stub, name string, weights [3]float64, want uint64) issue5Outcome {
	t.Helper()
	started := time.Now()
	r := routine.NewSubmittedRtn("issue5-"+name, "cpu", nil, dev.GetReqResource())
	r.InitSid = origin.Id()
	r.SubmitTime = started.UnixNano()
	origin.rtnStore.Record(r)
	origin.SubmitRoutine(r)
	out := issue5Outcome{WeightName: name, Weights: weights}
	select {
	case <-r.WaitReceive():
	case <-time.After(3 * time.Second):
		t.Fatalf("%s: no remote receiver", name)
	}
	toCloud, receiver := r.Receiver()
	if toCloud || receiver == "" {
		t.Fatalf("%s: invalid receiver cloud=%v id=%q", name, toCloud, receiver)
	}
	out.Receiver = receiver
	origin.waiterStore.Push(&waiters.Waiter{RefIds: []string{r.Id}, Exec: &issue5Executor{events: make(chan *interfaces.Event, 1)}, WithData: true})
	wait := &eventpb.RoutineWaitGetEvent{RtnId: r.Id, ReqSid: origin.Id(), WithData: true}
	data, err := proto.Marshal(wait)
	if err != nil {
		t.Fatal(err)
	}
	if err := cmnct.Singleton().SendToStub(receiver, &event.Event{Type: interfaces.RtnWaitEvt(receiver), Content: data}); err != nil {
		t.Fatal(err)
	}
	select {
	case <-r.WaitData():
	case <-time.After(3 * time.Second):
		t.Fatalf("%s: result timeout", name)
	}
	out.LatencyMS = float64(time.Since(started).Microseconds()) / 1000
	out.Checksum = string(r.Result())
	out.Status = fmt.Sprint(r.Status())
	if r.Status() != routine.EXIT_SUCC || out.Checksum != fmt.Sprint(want) {
		t.Fatalf("%s: status=%v checksum=%q want=%d error=%q", name, r.Status(), out.Checksum, want, r.Error())
	}
	return out
}

func issue5FreeAddr(t *testing.T) string {
	t.Helper()
	lis, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	addr := lis.Addr().String()
	_ = lis.Close()
	return addr
}

func issue5WaitReady(t *testing.T, path string) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for {
		if _, err := os.Stat(path); err == nil {
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("worker not ready: %s", path)
		}
		time.Sleep(20 * time.Millisecond)
	}
}

func issue5RunSmoke(t *testing.T) {
	t.Helper()
	if os.Getenv("TMC_ISSUE5_WORKER_CONFIG") != "" || os.Getenv("TMC_ISSUE5_ORIGIN_CONFIG") != "" {
		t.Skip("helper process")
	}
	root := t.TempDir()
	originAddr := issue5FreeAddr(t)
	kernel := revisionworkload.Config{Kind: "cpu", Bytes: 65536, Iterations: 30}
	workers := []issue5Candidate{
		{NodeID: "near", Address: issue5FreeAddr(t), TotalCPU: 2, NetworkMS: 1},
		{NodeID: "far", Address: issue5FreeAddr(t), TotalCPU: 4, NetworkMS: 40},
	}
	for _, worker := range workers {
		ready := filepath.Join(root, worker.NodeID+"-ready.json")
		config := filepath.Join(root, worker.NodeID+"-config.json")
		issue5WriteJSON(t, config, issue5WorkerConfig{NodeID: worker.NodeID, Listen: worker.Address, OriginAddr: originAddr, TotalCPU: worker.TotalCPU, Workload: kernel, ReadyPath: ready})
		cmd := exec.Command(os.Args[0], "-test.run=^TestIssue5WorkerProcess$")
		cmd.Env = append(os.Environ(), "TMC_ISSUE5_WORKER_CONFIG="+config)
		log, err := os.Create(filepath.Join(root, worker.NodeID+".log"))
		if err != nil {
			t.Fatal(err)
		}
		cmd.Stdout, cmd.Stderr = log, log
		if err := cmd.Start(); err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { _ = cmd.Process.Kill(); _ = cmd.Wait(); _ = log.Close() })
		issue5WaitReady(t, ready)
	}
	output := filepath.Join(root, "outcomes.json")
	config := filepath.Join(root, "origin-config.json")
	issue5WriteJSON(t, config, issue5OriginConfig{Listen: originAddr, Candidates: workers, Workload: kernel, OutputPath: output, QoSMS: 500, ExecutionMS: 10})
	cmd := exec.Command(os.Args[0], "-test.run=^TestIssue5OriginProcess$", "-test.v")
	cmd.Env = append(os.Environ(), "TMC_ISSUE5_ORIGIN_CONFIG="+config)
	if body, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("origin failed: %v\n%s\nlogs=%s", err, body, root)
	}
	var outcomes []issue5Outcome
	issue5ReadJSON(t, output, &outcomes)
	if len(outcomes) != 2 || outcomes[0].Receiver != issue5StubID("far") || outcomes[1].Receiver != issue5StubID("near") {
		t.Fatalf("native ranking did not switch with weights: %+v", outcomes)
	}
}

func issue5RunMatrixSmoke(t *testing.T) {
	t.Helper()
	if os.Getenv("TMC_ISSUE5_WORKER_CONFIG") != "" || os.Getenv("TMC_ISSUE5_ORIGIN_CONFIG") != "" {
		t.Skip("helper process")
	}
	root := t.TempDir()
	originAddr := issue5FreeAddr(t)
	kernel := revisionworkload.Config{Kind: "cpu", Bytes: 65536, Iterations: 30}
	workers := []issue5Candidate{
		{NodeID: "near", Address: issue5FreeAddr(t), TotalCPU: 2, NetworkMS: 1},
		{NodeID: "far", Address: issue5FreeAddr(t), TotalCPU: 4, NetworkMS: 40},
	}
	for _, worker := range workers {
		ready := filepath.Join(root, worker.NodeID+"-ready.json")
		config := filepath.Join(root, worker.NodeID+"-config.json")
		issue5WriteJSON(t, config, issue5WorkerConfig{NodeID: worker.NodeID, Listen: worker.Address, OriginAddr: originAddr, TotalCPU: worker.TotalCPU, Workload: kernel, ReadyPath: ready})
		cmd := exec.Command(os.Args[0], "-test.run=^TestIssue5WorkerProcess$")
		cmd.Env = append(os.Environ(), "TMC_ISSUE5_WORKER_CONFIG="+config)
		log, err := os.Create(filepath.Join(root, worker.NodeID+".log"))
		if err != nil {
			t.Fatal(err)
		}
		cmd.Stdout, cmd.Stderr = log, log
		if err := cmd.Start(); err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { _ = cmd.Process.Kill(); _ = cmd.Wait(); _ = log.Close() })
		issue5WaitReady(t, ready)
	}
	first := issue5RunSpec{ID: "steady-r1-equal", Rate: 8, Pattern: "steady", Repeat: 1, Seed: 42,
		DurationMS: 1000, PayloadBytes: 65536, WeightName: "equal", Weights: [3]float64{1, 1, 1}}
	second := first
	second.ID, second.WeightName, second.Weights = "steady-r1-latency", "latency", [3]float64{0, .9, .1}
	output := filepath.Join(root, "matrix")
	config := filepath.Join(root, "origin-config.json")
	issue5WriteJSON(t, config, issue5OriginConfig{Listen: originAddr, Candidates: workers, Workload: kernel,
		OutputDir: output, Runs: []issue5RunSpec{first, second}, QoSMS: 500, ExecutionMS: 10})
	cmd := exec.Command(os.Args[0], "-test.run=^TestIssue5OriginProcess$", "-test.v")
	cmd.Env = append(os.Environ(), "TMC_ISSUE5_ORIGIN_CONFIG="+config)
	if body, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("matrix origin failed: %v\n%s\nlogs=%s", err, body, root)
	}
	var warmups []issue5RawRequest
	issue5ReadJSON(t, filepath.Join(output, "warmups.json"), &warmups)
	if len(warmups) != len(workers) {
		t.Fatalf("expected one warmup per candidate, got %d", len(warmups))
	}
	for i, row := range warmups {
		if row.Status != "success" || row.Receiver != issue5StubID(workers[i].NodeID) {
			t.Fatalf("candidate %s was not warmed: %+v", workers[i].NodeID, row)
		}
	}
	for _, run := range []issue5RunSpec{first, second} {
		var meta struct {
			Submitted int `json:"submitted"`
			Success   int `json:"success"`
			OnTime    int `json:"on_time"`
		}
		issue5ReadJSON(t, filepath.Join(output, run.ID, "run.json"), &meta)
		if meta.Submitted != 8 || meta.Success != 8 || meta.OnTime != 8 {
			t.Fatalf("run %s incomplete: %+v", run.ID, meta)
		}
		body, err := os.ReadFile(filepath.Join(output, run.ID, "requests.jsonl"))
		if err != nil {
			t.Fatal(err)
		}
		if len(bytes.Split(bytes.TrimSpace(body), []byte("\n"))) != 8 {
			t.Fatalf("run %s missing raw request records", run.ID)
		}
	}
}
