package stub

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
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
	"github.com/ict/tide/pkg/server/handlers/stubhdlr"
	"github.com/ict/tide/pkg/stub/waiters"
	"github.com/ict/tide/proto/eventpb"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"
)

const issue7App = "issue7-native"
const issue7Source = "issue7-source"
const issue7Remote = "issue7-remote"

var issue7Kernel = revisionworkload.Config{Kind: "cpu", Bytes: 4096, Iterations: 20}

type issue7Executor struct {
	mu     sync.Mutex
	rtn    *routine.Routine
	proc   *processor.Processor
	events chan *interfaces.Event
}

func (e *issue7Executor) Id() string { return "issue7-controlled-executor" }
func (e *issue7Executor) GetRoutine() *routine.Routine {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.rtn
}
func (e *issue7Executor) GetProcessor() *processor.Processor {
	e.mu.Lock()
	defer e.mu.Unlock()
	return e.proc
}
func (e *issue7Executor) GetEventChan() <-chan *interfaces.Event { return e.events }
func (e *issue7Executor) BindRtn(r *routine.Routine) error {
	e.mu.Lock()
	e.rtn = r
	e.mu.Unlock()
	return nil
}
func (e *issue7Executor) BindProc(p *processor.Processor) error {
	e.mu.Lock()
	e.proc = p
	e.mu.Unlock()
	return nil
}
func (e *issue7Executor) UnbindRtn() (*routine.Routine, error)      { return e.GetRoutine(), nil }
func (e *issue7Executor) UnbindProc() (*processor.Processor, error) { return e.GetProcessor(), nil }
func (e *issue7Executor) SendEvt(*interfaces.Event) error           { return nil }
func (e *issue7Executor) Close() error                              { return nil }
func (e *issue7Executor) Execute() error {
	go func() {
		result, err := revisionworkload.Run(issue7Kernel)
		evt := &interfaces.Event{Type: interfaces.EXIT, Status: routine.EXIT_SUCC, Result: []byte(fmt.Sprintf("%d", result.Checksum))}
		if err != nil {
			evt.Status = routine.EXIT_FAIL
			evt.Error = err.Error()
		}
		e.events <- evt
	}()
	return nil
}

type issue7Pool struct{}

func (*issue7Pool) Get() interfaces.Executor {
	return &issue7Executor{events: make(chan *interfaces.Event, 1)}
}
func (*issue7Pool) Return(interfaces.Executor) {}
func (*issue7Pool) IncInstance(int)            {}
func (*issue7Pool) Close() error               { return nil }

type issue7Ready struct {
	Address string `json:"address"`
	StubID  string `json:"stub_id"`
}
type issue7Outcome struct {
	ID, Phase, RequestedRoute, ObservedReceiver, Status, Result string
	StartedNS, EndedNS                                          int64
}

func issue7Config(t *testing.T) {
	t.Helper()
	c := dev.DefaultConfig()
	c.TTLMS = 3600000
	c.ExecutionMS = 800
	c.QoSMS = 1000
	c.StateMaxAgeMS = 30000
	c.NodeExecutionMS = map[string]int{issue7Remote: 30}
	c.QoSByFunctionMS = map[string]int{"remote": 500, "local": 1000}
	c.C4Functions = map[string]bool{"remote": true, "local": true}
	if err := dev.SetConfig(c); err != nil {
		t.Fatal(err)
	}
}
func issue7FnStub(t *testing.T) (*stub, *procmgr.ProcessorManager) {
	t.Helper()
	pm := procmgr.NewProcMgr(&routine.Resource{CpuNum: 2})
	ctx := context.WithValue(context.Background(), clsstat.ApplicationId("appid"), issue7App)
	p, err := pm.Apply(ctx, &routine.Resource{CpuNum: 2})
	if err != nil {
		t.Fatal(err)
	}
	st := NewStub(&CreateStubOpts{AppId: issue7App, Mode: interfaces.StubFn, ResReq: &routine.Resource{CpuNum: 2}, ExecPool: &issue7Pool{}, Proc: p, ProcM: pm}).(*stub)
	return st, pm
}

func TestNativeIssue7Child(t *testing.T) {
	if os.Getenv("TMC_ISSUE7_CHILD") != "1" {
		t.Skip("helper process")
	}
	issue7Config(t)
	lis, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer lis.Close()
	selfAddr := lis.Addr().String()
	cs := clsstat.Singleton()
	clsstat.Init(issue7Remote, selfAddr, clsstat.EDGE, &routine.Resource{CpuNum: 2})
	cs.AddNode(&clsstat.AddNodeOpts{Nid: issue7Source, Address: os.Getenv("TMC_ISSUE7_SOURCE_ADDR"), Role: clsstat.THINGS, ResTotal: &routine.Resource{CpuNum: 2}})
	cs.AddApp(&clsstat.AddAppOpts{AppId: issue7App})
	cs.AddStub(&clsstat.AddStubOpts{StubId: os.Getenv("TMC_ISSUE7_SOURCE_STUB"), AppId: issue7App, NodeId: issue7Source, Mode: interfaces.StubFn})
	d := event.NewEventDispatcher()
	cmnct.Init(issue7Remote, selfAddr, d)
	st, _ := issue7FnStub(t)
	defer st.Close()
	stubhdlr.RegisterEventHandler(d, st)
	faultPath := os.Getenv("TMC_ISSUE7_FAULT_PATH")
	remoteEvents, err := os.Create(os.Getenv("TMC_ISSUE7_REMOTE_EVENTS"))
	if err != nil {
		t.Fatal(err)
	}
	defer remoteEvents.Close()
	var remoteEventsMu sync.Mutex
	svr := grpc.NewServer(grpc.UnaryInterceptor(func(ctx context.Context, req any, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (any, error) {
		_, faultErr := os.Stat(faultPath)
		blocked := faultErr == nil
		if batch, ok := req.(*eventpb.EventList); ok {
			for _, e := range batch.Events {
				if strings.Contains(e.Type, "submit_routine") {
					submitted := &eventpb.RoutineSubmitEvent{}
					_ = proto.Unmarshal(e.Content, submitted)
					line, _ := json.Marshal(map[string]any{"time_ns": time.Now().UnixNano(), "request_id": submitted.RtnId, "blocked": blocked, "critical": submitted.RequireLocalCapacity, "local_node_id": submitted.LocalNodeId})
					remoteEventsMu.Lock()
					_, _ = remoteEvents.Write(append(line, '\n'))
					_ = remoteEvents.Sync()
					remoteEventsMu.Unlock()
				}
			}
		}
		if blocked {
			return nil, status.Error(codes.Unavailable, "issue7 remote endpoint unavailable")
		}
		return handler(ctx, req)
	}))
	eventpb.RegisterEventServiceServer(svr, server.NewEventServer(d))
	go func() { _ = svr.Serve(lis) }()
	defer svr.Stop()
	body, _ := json.Marshal(issue7Ready{Address: selfAddr, StubID: st.Id()})
	if err := os.WriteFile(os.Getenv("TMC_ISSUE7_READY_PATH"), body, 0600); err != nil {
		t.Fatal(err)
	}
	<-time.After(20 * time.Second)
}

func TestNativeIssue7C4Path(t *testing.T) {
	if os.Getenv("TMC_ISSUE7_PARENT") != "" || os.Getenv("TMC_ISSUE7_CHILD") != "" {
		t.Skip("outer test only")
	}
	cmd := exec.Command(os.Args[0], "-test.run=^TestNativeIssue7Parent$")
	cmd.Env = append(os.Environ(), "TMC_ISSUE7_PARENT=1")
	output, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("isolated native path failed: %v\n%s", err, output)
	}
}

func TestNativeIssue7Parent(t *testing.T) {
	if os.Getenv("TMC_ISSUE7_PARENT") != "1" {
		t.Skip("isolated helper process")
	}

	oldConfig := dev.CurrentConfig()
	t.Cleanup(func() { _ = dev.SetConfig(oldConfig) })
	issue7Config(t)
	lis, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer lis.Close()
	sourceAddr := lis.Addr().String()
	cs := clsstat.Singleton()
	clsstat.Init(issue7Source, sourceAddr, clsstat.THINGS, &routine.Resource{CpuNum: 2})
	cs.AddApp(&clsstat.AddAppOpts{AppId: issue7App})
	d := event.NewEventDispatcher()
	cmnct.Init(issue7Source, sourceAddr, d)
	origin, _ := issue7FnStub(t)
	defer origin.Close()
	stubhdlr.RegisterEventHandler(d, origin)
	svr := grpc.NewServer()
	eventpb.RegisterEventServiceServer(svr, server.NewEventServer(d))
	go func() { _ = svr.Serve(lis) }()
	defer svr.Stop()
	root := t.TempDir()
	readyPath := filepath.Join(root, "ready.json")
	faultPath := filepath.Join(root, "remote-unavailable")
	cmd := exec.Command(os.Args[0], "-test.run=^TestNativeIssue7Child$", "-test.v")
	cmd.Env = append(os.Environ(), "TMC_ISSUE7_CHILD=1", "TMC_ISSUE7_SOURCE_ADDR="+sourceAddr, "TMC_ISSUE7_SOURCE_STUB="+origin.Id(), "TMC_ISSUE7_READY_PATH="+readyPath, "TMC_ISSUE7_FAULT_PATH="+faultPath, "TMC_ISSUE7_REMOTE_EVENTS="+filepath.Join(root, "remote-events.jsonl"))
	childLog, err := os.Create(filepath.Join(root, "child.log"))
	if err != nil {
		t.Fatal(err)
	}
	defer childLog.Close()
	cmd.Stdout = childLog
	cmd.Stderr = childLog
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { _ = cmd.Process.Kill(); _ = cmd.Wait() }()
	var ready issue7Ready
	deadline := time.Now().Add(5 * time.Second)
	for {
		body, err := os.ReadFile(readyPath)
		if err == nil && json.Unmarshal(body, &ready) == nil && ready.StubID != "" {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("remote child not ready; log=%s", root)
		}
		time.Sleep(10 * time.Millisecond)
	}
	cs.AddNode(&clsstat.AddNodeOpts{Nid: issue7Remote, Address: ready.Address, Role: clsstat.EDGE, ResTotal: &routine.Resource{CpuNum: 2}})
	cs.UpdateNode(&clsstat.UpdateNodeOpts{Nid: issue7Remote, ResRemain: &routine.Resource{CpuNum: 2}})
	cs.AddStub(&clsstat.AddStubOpts{StubId: ready.StubID, AppId: issue7App, NodeId: issue7Remote, Mode: interfaces.StubFn})
	expected, _ := revisionworkload.Run(issue7Kernel)
	want := fmt.Sprintf("%d", expected.Checksum)
	var outcomes []issue7Outcome
	transitions := []map[string]any{{"phase": "connected", "time_ns": time.Now().UnixNano()}}
	submit := func(id, phase, fn string, demand int, wantRoute string) {
		t.Helper()
		started := time.Now()
		r := routine.NewSubmittedRtn(id, fn, nil, &routine.Resource{CpuNum: demand})
		r.InitSid = origin.Id()
		r.SubmitTime = started.UnixNano()
		origin.rtnStore.Record(r)
		origin.SubmitRoutine(r)
		result := issue7Outcome{ID: id, Phase: phase, StartedNS: started.UnixNano(), RequestedRoute: wantRoute}
		if wantRoute == "remote-rejected" {
			select {
			case <-r.WaitReceive():
			case <-time.After(2 * time.Second):
				t.Fatalf("%s did not receive terminal rejection during fault", id)
			}
			select {
			case <-r.WaitData():
			case <-time.After(2 * time.Second):
				t.Fatalf("%s did not receive failure data during fault", id)
			}
			if r.Status() != routine.EXIT_FAIL || !strings.Contains(r.Error(), "no feasible execution destination") {
				t.Fatalf("%s wrong terminal result during fault: status=%d error=%q", id, r.Status(), r.Error())
			}
			result.Status = "rejected-no-destination"
			result.ObservedReceiver = "rejected-at-origin"
		} else {
			if wantRoute == "remote" {
				origin.waiterStore.Push(&waiters.Waiter{RefIds: []string{r.Id}, Exec: &issue7Executor{events: make(chan *interfaces.Event, 1)}, WithData: true})
				select {
				case <-r.WaitReceive():
				case <-time.After(2 * time.Second):
					t.Fatalf("%s not received remotely", id)
				}
				wait := &eventpb.RoutineWaitGetEvent{RtnId: r.Id, ReqSid: origin.Id(), WithData: true}
				payload, err := proto.Marshal(wait)
				if err != nil {
					t.Fatal(err)
				}
				if err := cmnct.Singleton().SendToStub(ready.StubID, &event.Event{Type: interfaces.RtnWaitEvt(ready.StubID), Content: payload}); err != nil {
					t.Fatal(err)
				}
			}
			select {
			case <-r.WaitData():
			case <-time.After(2 * time.Second):
				t.Fatalf("%s did not return", id)
			}
			result.Result = string(r.Result())
			result.Status = fmt.Sprint(r.Status())
			if wantRoute == "C4-rejected" {
				result.ObservedReceiver = "rejected-at-origin"
				if r.Status() != routine.EXIT_FAIL || !strings.Contains(r.Error(), "C4:") {
					t.Fatalf("%s should be rejected by C4: %s", id, r.Error())
				}
			} else {
				if r.Status() != routine.EXIT_SUCC || result.Result != want {
					t.Fatalf("%s wrong result: status %d value %q want %q", id, r.Status(), result.Result, want)
				}
				toCloud, receiver := r.Receiver()
				result.ObservedReceiver = receiver
				if toCloud || (wantRoute == "remote" && receiver != ready.StubID) || (wantRoute == "local" && receiver != origin.Id()) {
					t.Fatalf("%s wrong receiver: cloud=%v id=%s", id, toCloud, receiver)
				}
			}
		}
		result.EndedNS = time.Now().UnixNano()
		outcomes = append(outcomes, result)
	}
	for run := 1; run <= 3; run++ {
		submit(fmt.Sprintf("%d-connected", run), "connected", "remote", 2, "remote")
		if err := os.WriteFile(faultPath, []byte("unavailable"), 0600); err != nil {
			t.Fatal(err)
		}
		transitions = append(transitions, map[string]any{"phase": "disconnected", "time_ns": time.Now().UnixNano(), "cycle": run})
		submit(fmt.Sprintf("%d-local", run), "disconnected", "local", 2, "local")
		submit(fmt.Sprintf("%d-remote-fault", run), "disconnected", "remote", 2, "remote-rejected")
		if err := os.Remove(faultPath); err != nil {
			t.Fatal(err)
		}
		transitions = append(transitions, map[string]any{"phase": "recovered", "time_ns": time.Now().UnixNano(), "cycle": run})
		submit(fmt.Sprintf("%d-recovered", run), "recovered", "remote", 2, "remote")
	}
	submit("insufficient-local-CPU", "C4-screen", "remote", 3, "C4-rejected")
	if len(outcomes) != 13 {
		t.Fatalf("outcome count=%d", len(outcomes))
	}
	if out := os.Getenv("TMC_ISSUE7_OUT"); out != "" {
		if err := os.MkdirAll(out, 0755); err != nil {
			t.Fatal(err)
		}
		executable, err := os.ReadFile(os.Args[0])
		if err != nil {
			t.Fatal(err)
		}
		digest := sha256.Sum256(executable)
		body, err := json.MarshalIndent(map[string]any{"scope": "native Tide scheduler and gRPC events on one host; endpoint gate; controlled CPU executor", "origin": issue7Source, "remote": issue7Remote, "origin_address": sourceAddr, "remote_address": ready.Address, "remote_stub_id": ready.StubID, "source_stub_id": origin.Id(), "go_version": runtime.Version(), "test_binary_sha256": fmt.Sprintf("%x", digest), "workload": issue7Kernel, "config": dev.CurrentConfig(), "transitions": transitions, "outcomes": outcomes}, "", "  ")
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(out, "outcomes.json"), body, 0644); err != nil {
			t.Fatal(err)
		}
		child, err := os.ReadFile(filepath.Join(root, "child.log"))
		if err == nil {
			_ = os.WriteFile(filepath.Join(out, "remote.log"), child, 0644)
		}
		remoteTrace, err := os.ReadFile(filepath.Join(root, "remote-events.jsonl"))
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(out, "remote-events.jsonl"), remoteTrace, 0644); err != nil {
			t.Fatal(err)
		}
	}
}
