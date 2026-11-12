//go:build linux

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
	"github.com/ict/tide/pkg/server/handlers/clsstathdlr"
	"github.com/ict/tide/pkg/server/handlers/stubhdlr"
	"github.com/ict/tide/pkg/server/handlers/stubmgrhdlr"
	"github.com/ict/tide/pkg/stub"
	"github.com/ict/tide/pkg/stubmgr"
	"github.com/ict/tide/proto/eventpb"
	"github.com/sirupsen/logrus"
	"google.golang.org/grpc"
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
func errorString(err error) string {
	if err == nil {
		return ""
	}
	return err.Error()
}

func main() {
	name := flag.String("node", "", "physical native node A,B,or C")
	address := flag.String("address", "", "advertised native gRPC IP:34060")
	httpAddress := flag.String("http", "", "HTTP ingress/control IP:34061")
	initial := flag.String("initial", "warm", "warm or cold B function state")
	root := flag.String("root", "", "unique owned run root")
	config := flag.String("config", "", "immutable native configuration JSON")
	timeout := flag.Duration("request-timeout", 5*time.Second, "client observation timeout, separate from scheduling QoS")
	policy := flag.String("policy", "tide", "native tide policy")
	flag.Parse()
	if (*name != "A" && *name != "B" && *name != "C") || (*initial != "warm" && *initial != "cold") || *policy != "tide" || *root == "" || *config == "" || *timeout <= 0 {
		panic("invalid domain options")
	}
	_, _, err := net.SplitHostPort(*address)
	must(err)
	_, _, err = net.SplitHostPort(*httpAddress)
	must(err)
	dev.RSEPTest = false
	must(dev.LoadConfig(*config))
	cfg := dev.CurrentConfig()
	must(validateNativeConfig(cfg))
	must(os.Setenv("TIDE_MQ_MESSAGE_BYTES", "8192"))
	must(os.Setenv("TIDE_MQ_MAX_MESSAGES", "10"))
	absRoot, err := filepath.Abs(*root)
	must(err)
	base := filepath.Join(absRoot, "native")
	must(os.MkdirAll(base, 0755))
	dir := filepath.Join(base, *name)
	must(os.Mkdir(dir, 0755))
	f, err := os.OpenFile(filepath.Join(dir, "runtime.jsonl"), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	must(err)
	defer f.Close()
	logrus.SetOutput(f)
	logrus.SetFormatter(&logrus.JSONFormatter{TimestampFormat: time.RFC3339Nano})
	logrus.SetLevel(logrus.DebugLevel)
	own := newOwnership(dir, *name)
	must(own.persist())
	logrus.AddHook(own)
	role, cpus := clsstat.EDGE, 2
	if *name == "C" {
		role, cpus = clsstat.CLOUD, 0
	}
	resource := &routine.Resource{CpuNum: cpus}
	clsstat.Init(*name, *address, role, resource)
	containerxv2.InitCgroup()
	if role == clsstat.EDGE {
		containerxv2.InitCombCgroup()
	}
	pm := procmgr.NewProcMgr(resource)
	sm := stubmgr.NewStubMgr(pm)
	dispatch := event.NewEventDispatcher()
	cmnct.Init(*name, *address, dispatch)
	clsstathdlr.RegisterEventHandler(dispatch)
	stubmgrhdlr.RegisterEventHandler(dispatch, sm)
	commands := []string(nil)
	if role == clsstat.EDGE {
		commands = workerCommands(*name)
	}
	clsstat.Singleton().AddApp(&clsstat.AddAppOpts{AppId: appID, ImageName: workerImage, Commands: commands})
	var function interfaces.Stub
	if *name == "A" || (*name == "B" && *initial == "warm") {
		function, err = sm.CreateStub(&stubmgr.CreateStubOpts{AppId: appID, ImgName: workerImage, Commands: commands, Mode: interfaces.StubFn, ResReq: dev.GetStubResource(), InitExecNo: 1})
		must(err)
		if function == nil {
			panic("warm function missing")
		}
		stubhdlr.RegisterEventHandler(dispatch, function)
		clsstat.Singleton().AddStub(&clsstat.AddStubOpts{StubId: function.Id(), AppId: appID, NodeId: *name, Mode: interfaces.StubFn, CreateTime: time.Now()})
	}
	self := nodeInfo{Name: *name, Address: *address, Role: role.String(), CPUs: cpus, PID: os.Getpid(), Initial: *initial, Image: workerImage, Commands: commands, Root: dir}
	if function != nil {
		self.Stub = function.Id()
		self.Slots = 1
		if snapshot := own.snapshot(); len(snapshot.Executors) != 1 || !snapshot.Executors[0].Ready {
			panic("warm initial executor not actually ready")
		}
	}
	install := func(peers []nodeInfo) error {
		for _, peer := range peers {
			if peer.Name == self.Name {
				continue
			}
			clsstat.Singleton().AddNode(&clsstat.AddNodeOpts{Nid: peer.Name, Address: peer.Address, Role: clsstat.ParseRole(peer.Role), ResTotal: &routine.Resource{CpuNum: peer.CPUs}})
			if peer.Stub != "" {
				clsstat.Singleton().AddStub(&clsstat.AddStubOpts{StubId: peer.Stub, AppId: appID, NodeId: peer.Name, Mode: interfaces.StubFn, CreateTime: time.Now()})
				own.registerInitialStub(peer.Stub, peer.Name)
			}
		}
		return own.persist()
	}
	submit := func(ctx context.Context, id string, body []byte) (pendingRequest, error) {
		if self.Name != "A" || function == nil {
			return nil, fmt.Errorf("genuine Fn A required")
		}
		r := routine.NewSubmittedRtn(id, "cpu", body, dev.GetReqResource())
		r.SubmitTime = time.Now().UnixNano()
		if err := stub.SubmitExternal(ctx, function, r); err != nil {
			return nil, err
		}
		return &nativePending{r: r}, nil
	}
	snapshot := func() map[string]any {
		nodes := []map[string]any{}
		for _, node := range []string{"A", "B", "C"} {
			if state := clsstat.Singleton().GetNode(node); state != nil {
				total, remaining, updated := state.Capacity()
				nodes = append(nodes, map[string]any{"node": node, "total_cpus": total.CpuNum, "remaining_cpus": remaining.CpuNum, "updated_unix_ns": updated.UnixNano(), "valid": !updated.IsZero()})
			}
		}
		return map[string]any{"processor": pm.Snapshot(), "nodes": nodes, "owned": own.snapshot(), "native_config": cfg,
			"application_allocation_cpus": cpus, "request_cpus": dev.GetReqResource().CpuNum,
			"executor_inventory_note":      "Cold native prefork count is unchanged; several idle executors may share one two-CPU application allocation.",
			"journal_path_inside_executor": "/tmp/domain/requests.jsonl", "node_label_authoritative": false}
	}
	api := newAPI(self, *timeout, submit, install, snapshot)
	listener, err := net.Listen("tcp", *address)
	must(err)
	gs := grpc.NewServer()
	eventpb.RegisterEventServiceServer(gs, server.NewEventServer(dispatch))
	go func() {
		if err := gs.Serve(listener); err != nil {
			logrus.WithError(err).Error("domain native gRPC stopped")
		}
	}()
	pm.StartHeartbeat()
	hs := &http.Server{Addr: *httpAddress, Handler: api.handler(), ReadHeaderTimeout: 5 * time.Second}
	go func() {
		if err := hs.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			logrus.WithError(err).Error("domain HTTP stopped")
		}
	}()
	startup := map[string]any{"self": self, "native_config": cfg, "request_timeout_ns": int64(*timeout), "mq_message_bytes": 8192, "mq_max_messages": 10, "rsep_test": false, "argv": os.Args,
		"application_resource_profile": "A/B each reserve two CPUs per request/stub; C native application CPUs=0 and no function.",
		"resource_enforcement":         "Parent must constrain /tide CPU,cpuset,and memory BEFORE this command starts; core creates no memory limit.",
		"cold_creation":                "Unmodified Scheduler.requestNewStub and native StubCreate handler; command node label may remain A on physical B; no VolumePair.",
		"cloud_fallback":               "C has no application handler/executor; native failures/timeouts remain observed outcomes."}
	data, err := json.MarshalIndent(startup, "", "  ")
	must(err)
	must(os.WriteFile(filepath.Join(dir, "startup.json"), append(data, '\n'), 0600))
	fmt.Println("READY", self.Name, self.Stub)
	signals := make(chan os.Signal, 1)
	signal.Notify(signals, syscall.SIGTERM, syscall.SIGINT)
	<-signals
	ctx, cancel := context.WithTimeout(context.Background(), *timeout+5*time.Second)
	defer cancel()
	httpErr := hs.Shutdown(ctx)
	smErr := sm.Close()
	pmErr := pm.Close()
	gs.Stop()
	for _, path := range own.snapshot().Cgroups {
		containerxv2.DeleteCgroup(path)
	}
	remaining := []string{}
	for _, path := range own.snapshot().Cgroups {
		if _, err := os.Stat(filepath.Join("/sys/fs/cgroup", path)); err == nil {
			remaining = append(remaining, path)
		}
	}
	shutdown := map[string]any{"observed_unix_ns": time.Now().UnixNano(), "unresolved_requests": api.submitted.Load() - api.completed.Load(), "http_error": errorString(httpErr), "stub_manager_error": errorString(smErr), "processor_manager_error": errorString(pmErr), "remaining_owned_cgroups": remaining}
	data, err = json.MarshalIndent(shutdown, "", "  ")
	must(err)
	must(os.WriteFile(filepath.Join(dir, "shutdown.json"), append(data, '\n'), 0600))
}
