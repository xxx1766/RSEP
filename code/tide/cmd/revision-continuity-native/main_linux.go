//go:build linux

// Measurement ingress only: execution, queueing and placement use native Tide.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"sync"
	"syscall"
	"time"

	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/cmnct"
	"github.com/ict/tide/pkg/containerxv2"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/event"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/procmgr"
	"github.com/ict/tide/pkg/routine"
	"github.com/ict/tide/pkg/server"
	"github.com/ict/tide/pkg/server/handlers/cloudhdlr"
	"github.com/ict/tide/pkg/server/handlers/clsstathdlr"
	"github.com/ict/tide/pkg/server/handlers/stubhdlr"
	"github.com/ict/tide/pkg/server/handlers/stubmgrhdlr"
	"github.com/ict/tide/pkg/stub"
	"github.com/ict/tide/pkg/stubmgr"
	"github.com/ict/tide/proto/eventpb"
	"github.com/sirupsen/logrus"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type nativePending struct{ r *routine.Routine }

func (p *nativePending) waitData() <-chan struct{} { return p.r.WaitData() }
func (p *nativePending) outcome() nativeResult {
	return nativeResult{Status: int(p.r.Status()), Success: p.r.Status() == routine.EXIT_SUCC, Result: p.r.Result(), Error: p.r.Error()}
}
func (p *nativePending) receiver() destination {
	cloud, id := p.r.Receiver()
	return destination{Cloud: cloud, ID: id}
}
func must(err error) {
	if err != nil {
		panic(err)
	}
}

func main() {
	name := flag.String("node", "", "ru-local or ru-cloud")
	addr := flag.String("address", "", "advertised native gRPC IP:port")
	httpAddr := flag.String("http", "", "HTTP measurement/control IP:port")
	policy := flag.String("policy", "tide", "tide or native-cloud-only")
	root := flag.String("root", "", "unique owned run directory")
	config := flag.String("config", "", "immutable native config JSON for selected kernel")
	kernel := flag.String("kernel", "cpu", "cpu or memory; immutable per-process workload")
	timeout := flag.Duration("request-timeout", 5*time.Second, "result observation timeout; separate from 500ms deadline")
	flag.Parse()
	if (*name != "ru-local" && *name != "ru-cloud") || (*policy != "tide" && *policy != "native-cloud-only") || *addr == "" || *httpAddr == "" || *root == "" || *config == "" || *timeout <= 0 || (*kernel != "cpu" && *kernel != "memory") {
		panic("invalid R/U options")
	}
	_, _, err := net.SplitHostPort(*addr)
	must(err)
	_, _, err = net.SplitHostPort(*httpAddr)
	must(err)
	dev.RSEPTest = false
	must(dev.LoadConfig(*config))
	cfg := dev.CurrentConfig()
	if cfg.QoSMS != 240000 || cfg.TTLMS != 86400000 || len(cfg.QoSByFunctionMS) != 0 || !cfg.C4Functions["cpu"] || !cfg.C4Functions["memory"] || cfg.NodeExecutionMS["ru-local"] <= 0 || cfg.NodeExecutionMS["ru-cloud"] <= 0 {
		panic("frozen 240s/24h/C4/per-node estimates required")
	}
	must(os.Setenv("TIDE_MQ_MESSAGE_BYTES", "8192"))
	must(os.Setenv("TIDE_MQ_MAX_MESSAGES", "10"))
	absRoot, err := filepath.Abs(*root)
	must(err)
	base := filepath.Join(absRoot, "native")
	must(os.MkdirAll(base, 0755))
	dir := filepath.Join(base, *name)
	must(os.Mkdir(dir, 0755))
	ioDir := filepath.Join(dir, "worker-data")
	must(os.Mkdir(ioDir, 0755))
	f, err := os.OpenFile(filepath.Join(dir, "runtime.jsonl"), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	must(err)
	defer f.Close()
	logrus.SetOutput(f)
	logrus.SetFormatter(&logrus.JSONFormatter{TimestampFormat: time.RFC3339Nano})
	logrus.SetLevel(logrus.DebugLevel)
	own := newOwnership(dir)
	logrus.AddHook(own)
	gate, err := newGate(dir, *name)
	must(err)
	defer gate.close()
	role := clsstat.EDGE
	if *name == "ru-cloud" {
		role = clsstat.CLOUD
	}
	res := &routine.Resource{CpuNum: 2}
	clsstat.Init(*name, *addr, role, res)
	containerxv2.InitCgroup()
	if role == clsstat.EDGE {
		containerxv2.InitCombCgroup()
	}
	pm := procmgr.NewProcMgr(res)
	sm := stubmgr.NewStubMgr(pm)
	dispatch := event.NewEventDispatcher()
	cmnct.Init(*name, *addr, dispatch)
	clsstathdlr.RegisterEventHandler(dispatch)
	stubmgrhdlr.RegisterEventHandler(dispatch, sm)
	commands := []string{"/worker", "--mode=paix", "--node=" + *name, "--tmpdir=/owned"}
	clsstat.Singleton().AddApp(&clsstat.AddAppOpts{AppId: appID, ImageName: workerImage, Commands: commands})
	st, err := sm.CreateStub(&stubmgr.CreateStubOpts{AppId: appID, ImgName: workerImage, VolumePair: ioDir + ":/owned", Commands: commands, Mode: interfaces.StubFn, ResReq: dev.GetStubResource(), InitExecNo: 1})
	must(err)
	if st == nil {
		panic("native function missing")
	}
	stubhdlr.RegisterEventHandler(dispatch, st)
	if role == clsstat.CLOUD {
		cloudhdlr.RegisterStubHandlers(dispatch, st)
	} else {
		clsstat.Singleton().AddStub(&clsstat.AddStubOpts{StubId: st.Id(), AppId: appID, NodeId: *name, Mode: interfaces.StubFn, CreateTime: time.Now()})
	}
	ownership := own.snapshot()
	if len(ownership.Executors) != 1 || !ownership.Executors[0].Ready {
		panic("exactly one actual warm native executor required")
	}
	self := nodeInfo{Name: *name, Address: *addr, Role: role.String(), CPUs: 2, Slots: 1, Stub: st.Id(), PID: os.Getpid(), Policy: *policy, Image: workerImage, Commands: commands, Root: dir}
	var peerMu sync.RWMutex
	peers := []nodeInfo{self}
	install := func(p []nodeInfo) error {
		peerMu.Lock()
		defer peerMu.Unlock()
		for _, n := range p {
			if n.Name == self.Name {
				continue
			}
			clsstat.Singleton().AddNode(&clsstat.AddNodeOpts{Nid: n.Name, Address: n.Address, Role: clsstat.ParseRole(n.Role), ResTotal: &routine.Resource{CpuNum: 2}})
			if n.Role == "edge" {
				clsstat.Singleton().AddStub(&clsstat.AddStubOpts{StubId: n.Stub, AppId: appID, NodeId: n.Name, Mode: interfaces.StubFn, CreateTime: time.Now()})
			}
		}
		peers = append([]nodeInfo(nil), p...)
		return nil
	}
	submit := func(ctx context.Context, id string, body []byte) (pendingRequest, error) {
		var work struct {
			Kind string `json:"kind"`
		}
		if err := json.Unmarshal(body, &work); err != nil {
			return nil, err
		}
		if work.Kind != *kernel {
			return nil, fmt.Errorf("process frozen for %s", *kernel)
		}
		r := routine.NewSubmittedRtn(id, work.Kind, body, dev.GetReqResource())
		r.SubmitTime = time.Now().UnixNano()
		if role == clsstat.CLOUD { // Native local execution for excluded standalone calibration.
			r.InitSid = st.Id()
			r.RequireLocalCapacity = true
			r.LocalNodeID = "ru-local"
			st.CloudSubmitRoutine(r)
			r.SetReceiver(false, st.Id())
		} else if *policy == "native-cloud-only" {
			if err := stub.SubmitExternalCloud(ctx, st, r, "ru-cloud"); err != nil {
				return nil, err
			}
		} else {
			if err := stub.SubmitExternal(ctx, st, r); err != nil {
				return nil, err
			}
		}
		return &nativePending{r: r}, nil
	}
	snapshot := func() map[string]any {
		peerMu.RLock()
		defer peerMu.RUnlock()
		nodes := []map[string]any{}
		for _, p := range peers {
			n := clsstat.Singleton().GetNode(p.Name)
			if n == nil {
				continue
			}
			total, remain, updated := n.Capacity()
			nodes = append(nodes, map[string]any{"node": p.Name, "total_cpus": total.CpuNum, "remaining_cpus": remain.CpuNum, "updated_unix_ns": updated.UnixNano(), "valid": !updated.IsZero()})
		}
		return map[string]any{"processor": pm.Snapshot(), "nodes": nodes, "owned": own.snapshot(), "native_config": cfg, "kernel": *kernel}
	}
	api := newAPI(self, *timeout, submit, install, snapshot, gate)
	lis, err := net.Listen("tcp", *addr)
	must(err)
	gs := grpc.NewServer(grpc.UnaryInterceptor(func(ctx context.Context, req any, info *grpc.UnaryServerInfo, handler grpc.UnaryHandler) (any, error) {
		if batch, ok := req.(*eventpb.EventList); ok {
			if gate.reject(rpcRecords(batch)) {
				return nil, status.Error(codes.Unavailable, "owned R/U native event gate enabled")
			}
		}
		return handler(ctx, req)
	}))
	eventpb.RegisterEventServiceServer(gs, server.NewEventServer(dispatch))
	go func() {
		if err := gs.Serve(lis); err != nil {
			logrus.WithError(err).Error("R/U native gRPC stopped")
		}
	}()
	pm.StartHeartbeat()
	hs := &http.Server{Addr: *httpAddr, Handler: api.handler(), ReadHeaderTimeout: 5 * time.Second}
	go func() {
		if err := hs.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			logrus.WithError(err).Error("R/U HTTP stopped")
		}
	}()
	startup := map[string]any{"self": self, "kernel": *kernel, "native_config": cfg, "request_timeout_ns": int64(*timeout), "mq_message_bytes": 8192, "mq_max_messages": 10, "rsep_test": false, "argv": os.Args}
	data, err := json.MarshalIndent(startup, "", "  ")
	must(err)
	must(os.WriteFile(filepath.Join(dir, "startup.json"), append(data, '\n'), 0600))
	fmt.Println("READY", self.Name, self.Stub)
	signals := make(chan os.Signal, 1)
	signal.Notify(signals, syscall.SIGTERM, syscall.SIGINT)
	<-signals
	// Stop ingress first; retained unresolved counters distinguish observation from drain.
	ctx, cancel := context.WithTimeout(context.Background(), *timeout+5*time.Second)
	defer cancel()
	httpErr := hs.Shutdown(ctx)
	smErr := sm.Close()
	pmErr := pm.Close()
	gs.Stop()
	for _, p := range own.snapshot().Cgroups {
		containerxv2.DeleteCgroup(p)
	}
	remaining := []string{}
	for _, p := range own.snapshot().Cgroups {
		if _, err := os.Stat(filepath.Join("/sys/fs/cgroup", p)); err == nil {
			remaining = append(remaining, p)
		}
	}
	shutdown := map[string]any{"observed_unix_ns": time.Now().UnixNano(), "unresolved_requests": api.submitted.Load() - api.completed.Load(), "http_error": errorString(httpErr), "stub_manager_error": errorString(smErr), "processor_manager_error": errorString(pmErr), "remaining_owned_cgroups": remaining}
	data, err = json.MarshalIndent(shutdown, "", "  ")
	must(err)
	must(os.WriteFile(filepath.Join(dir, "shutdown.json"), append(data, '\n'), 0600))
}
func errorString(e error) string {
	if e == nil {
		return ""
	}
	return e.Error()
}
