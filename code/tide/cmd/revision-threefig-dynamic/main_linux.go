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
	"sort"
	"strings"
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
	"google.golang.org/protobuf/proto"
)

type observedDispatcher struct {
	event.EventDispatcher
	ledger *witnessLedger
	node   string
}

func (d *observedDispatcher) RegisterHandler(kind string, h event.EventHandler) {
	d.EventDispatcher.RegisterHandler(kind, func(e *event.Event) {
		if e.Type == interfaces.StubNewEvt {
			d.observe(e)
		}
		h(e)
		if e.Type != interfaces.StubNewEvt {
			d.observe(e)
		}
	})
}
func (d *observedDispatcher) observe(e *event.Event) {
	now := time.Now().UnixNano()
	switch e.Type {
	case interfaces.StubNewEvt:
		var p eventpb.StubNewEvent
		if proto.Unmarshal(e.Content, &p) != nil {
			return
		}
		if p.AppId != campaignID {
			return
		}
		mode := "fn"
		if p.Mode == eventpb.StubMode_ENTRY {
			mode = "entry"
		}
		d.ledger.addStub(stubWitness{StubID: p.StubId, Node: e.From, Role: "edge", Mode: mode, Status: "running", CreatedNS: p.CreateTime, UpdatedNS: now})
		logrus.WithFields(logrus.Fields{"observed_unix_ns": now, "stub_id": p.StubId, "node": e.From, "mode": mode, "create_unix_ns": p.CreateTime}).Info("threefig native stub creation observed")
	case interfaces.StubStopEvt:
		var p eventpb.StubStopEvent
		if proto.Unmarshal(e.Content, &p) != nil {
			return
		}
		d.ledger.stopStub(p.StubId, now)
		logrus.WithFields(logrus.Fields{"observed_unix_ns": now, "stub_id": p.StubId, "observer": d.node}).Info("threefig native stub stop observed")
	case interfaces.StubSyncEvt:
		var p eventpb.StubSyncEvent
		if proto.Unmarshal(e.Content, &p) != nil {
			return
		}
		d.ledger.syncStub(p.StubId, p.UpdateTime, p.EstWaitTime)
	}
}

type nativePending struct {
	r      *routine.Routine
	ledger *witnessLedger
}

func (p *nativePending) waitData() <-chan struct{} { return p.r.WaitData() }
func (p *nativePending) receiver() destination {
	cloud, id := p.r.Receiver()
	return p.ledger.destination(cloud, id)
}
func (p *nativePending) outcome() nativeResult {
	return nativeResult{Status: int(p.r.Status()), Success: p.r.Status() == routine.EXIT_SUCC, Result: p.r.Result(), Error: p.r.Error()}
}

type cgroupOwnership struct {
	mu       sync.Mutex
	paths    map[string]bool
	manifest string
}

func (o *cgroupOwnership) Levels() []logrus.Level { return logrus.AllLevels }
func (o *cgroupOwnership) Fire(e *logrus.Entry) error {
	e.Data["trace_unix_ns"] = e.Time.UnixNano()
	if e.Message != "Containerxv2: finish creating cgroup" {
		return nil
	}
	path, ok := e.Data["CgroupPath"].(string)
	if !ok || !strings.HasPrefix(path, "/tide/") {
		return nil
	}
	o.mu.Lock()
	defer o.mu.Unlock()
	o.paths[path] = true
	data, err := json.Marshal(o.sorted())
	if err != nil {
		return err
	}
	return os.WriteFile(o.manifest, append(data, '\n'), 0600)
}
func (o *cgroupOwnership) sorted() []string {
	paths := make([]string, 0, len(o.paths))
	for p := range o.paths {
		paths = append(paths, p)
	}
	sort.Strings(paths)
	return paths
}
func (o *cgroupOwnership) cleanup() {
	o.mu.Lock()
	paths := o.sorted()
	o.mu.Unlock()
	for _, p := range paths {
		containerxv2.DeleteCgroup(p)
	}
}

func main() {
	name := flag.String("node", "", "campaign node ID (threefig-nj1, threefig-nj2, threefig-nj3)")
	kind := flag.String("kind", "", "origin, edge, or cloud")
	address := flag.String("address", "", "advertised gRPC IP:31840")
	httpAddr := flag.String("http", "0.0.0.0:31841", "HTTP measurement endpoint")
	cpus := flag.Int("cpus", 0, "native logical CPU budget; even 2..12 per node; formal=12")
	defaultRoot := os.Getenv("RSEP_NATIVE_ROOT")
	if defaultRoot == "" {
		defaultRoot = "/var/lib/rsep/threefig"
	}
	root := flag.String("root", defaultRoot, "owned campaign directory")
	image := flag.String("image", "docker.io/tide/threefig-20261019", "OCI image prefix (native appends GOARCH)")
	commandsJSON := flag.String("commands-json", `["env","TIDE_DETECTIONS_PER_REQ=10","ORT_INTRA_THREADS=2","ORT_INTER_THREADS=1","python3","-u","/app/threefig_worker.py"]`, "identical worker argv on every node")
	configPath := flag.String("config", "", "frozen native configuration JSON")
	mqBytes := flag.Int("mq-message-bytes", 262144, "POSIX MQ bytes; must fit JPEG plus protobuf framing")
	mqMessages := flag.Int("mq-max-messages", 10, "POSIX MQ queue depth (host limit must permit)")
	timeout := flag.Duration("request-timeout", 15*time.Second, "HTTP result observation timeout; independent of 350ms QoS")
	flag.Parse()
	if !strings.HasPrefix(*name, "threefig-") || *address == "" || *image == "" || *timeout <= 0 || *mqBytes < 4096 || *mqBytes > 1<<24 || *mqMessages < 1 || *mqMessages > 65536 {
		panic("invalid campaign options")
	}
	if _, _, err := net.SplitHostPort(*address); err != nil {
		panic(err)
	}
	must(validateCPUBudget(*cpus))
	if *kind != "origin" && *kind != "edge" && *kind != "cloud" {
		panic("kind must be origin, edge, or cloud")
	}
	var commands []string
	must(json.Unmarshal([]byte(*commandsJSON), &commands))
	if len(commands) == 0 {
		panic("nonempty command list required")
	}
	dev.RSEPTest = false
	cfg := dev.DefaultConfig()
	cfg.QoSMS = 350
	cfg.TTLMS = 2000
	must(dev.SetConfig(cfg))
	if *configPath != "" {
		must(dev.LoadConfig(*configPath))
	}
	cfg = dev.CurrentConfig()
	if cfg.TTLMS != 2000 || cfg.QoSMS != 350 || len(cfg.QoSByFunctionMS) != 0 {
		panic("campaign requires finite TTL2000ms and QoS350ms without per-function overrides")
	}
	must(os.Setenv("TIDE_MQ_MESSAGE_BYTES", fmt.Sprint(*mqBytes)))
	must(os.Setenv("TIDE_MQ_MAX_MESSAGES", fmt.Sprint(*mqMessages)))
	dir := filepath.Join(*root, "native", *name)
	must(os.MkdirAll(dir, 0755))
	lock, err := os.OpenFile(filepath.Join(dir, "runtime.lock"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	must(err)
	fmt.Fprintln(lock, os.Getpid())
	must(lock.Close())
	defer os.Remove(filepath.Join(dir, "runtime.lock"))
	logfile, err := os.OpenFile(filepath.Join(dir, "runtime.jsonl"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	must(err)
	defer logfile.Close()
	logrus.SetOutput(logfile)
	logrus.SetFormatter(&logrus.JSONFormatter{TimestampFormat: time.RFC3339Nano})
	logrus.SetLevel(logrus.DebugLevel)
	owned := &cgroupOwnership{paths: map[string]bool{}, manifest: filepath.Join(dir, "owned-cgroups.json")}
	logrus.AddHook(owned)
	role := clsstat.EDGE
	if *kind == "cloud" {
		role = clsstat.CLOUD
	}
	res := &routine.Resource{CpuNum: *cpus}
	clsstat.Init(*name, *address, role, res)
	containerxv2.InitCgroup()
	if role == clsstat.EDGE {
		containerxv2.InitCombCgroup()
	}
	pm := procmgr.NewProcMgr(res)
	sm := stubmgr.NewStubMgr(pm)
	ledger := newWitnessLedger()
	dispatch := &observedDispatcher{EventDispatcher: event.NewEventDispatcher(), ledger: ledger, node: *name}
	cmnct.Init(*name, *address, dispatch)
	clsstathdlr.RegisterEventHandler(dispatch)
	stubmgrhdlr.RegisterEventHandler(dispatch, sm)
	clsstat.Singleton().AddApp(&clsstat.AddAppOpts{AppId: campaignID, ImageName: *image, Commands: commands})
	self := nodeInfo{Campaign: campaignID, Name: *name, Address: *address, Role: role.String(), Kind: *kind, CPUs: *cpus, PID: os.Getpid(), Image: *image, Commands: commands}
	var entry interfaces.Stub
	if *kind != "edge" {
		mode, initial := interfaces.StubFn, 2
		if *kind == "origin" {
			mode, initial = interfaces.StubEntry, 0
		}
		st, err := sm.CreateStub(&stubmgr.CreateStubOpts{AppId: campaignID, ImgName: *image, Commands: commands, Mode: mode, ResReq: dev.GetStubResource(), InitExecNo: initial})
		must(err)
		if st == nil {
			panic("native initial stub missing")
		}
		self.Stub, self.StubMode = st.Id(), mode.String()
		stubhdlr.RegisterEventHandler(dispatch, st)
		ledger.addStub(stubWitness{StubID: st.Id(), Node: *name, Role: role.String(), Mode: mode.String(), Status: "running", CreatedNS: time.Now().UnixNano(), UpdatedNS: time.Now().UnixNano()})
		if *kind == "cloud" {
			cloudhdlr.RegisterStubHandlers(dispatch, st)
		} else {
			entry = st
			clsstat.Singleton().AddStub(&clsstat.AddStubOpts{StubId: st.Id(), AppId: campaignID, NodeId: *name, Mode: interfaces.StubEntry, CreateTime: time.Now()})
		}
	}
	ledger.setPeers([]nodeInfo{self})
	install := func(peers []nodeInfo) error {
		for _, p := range peers {
			if p.Name != self.Name {
				clsstat.Singleton().AddNode(&clsstat.AddNodeOpts{Nid: p.Name, Address: p.Address, Role: clsstat.ParseRole(p.Role), ResTotal: &routine.Resource{CpuNum: p.CPUs}})
			}
			if p.Kind == "origin" && p.Name != self.Name {
				clsstat.Singleton().AddStub(&clsstat.AddStubOpts{StubId: p.Stub, AppId: campaignID, NodeId: p.Name, Mode: interfaces.StubEntry, CreateTime: time.Now()})
				ledger.addStub(stubWitness{StubID: p.Stub, Node: p.Name, Role: p.Role, Mode: p.StubMode, Status: "running", CreatedNS: time.Now().UnixNano(), UpdatedNS: time.Now().UnixNano()})
			}
			if p.Kind == "cloud" && p.Name != self.Name {
				ledger.addStub(stubWitness{StubID: p.Stub, Node: p.Name, Role: p.Role, Mode: p.StubMode, Status: "running", CreatedNS: time.Now().UnixNano(), UpdatedNS: time.Now().UnixNano()})
			}
		}
		ledger.setPeers(peers)
		return nil
	}
	submit := func(ctx context.Context, id string, body []byte) (pendingRequest, error) {
		if entry == nil {
			return nil, fmt.Errorf("only origin owns a native entry")
		}
		r := routine.NewSubmittedRtn(id, "proce_img", body, dev.GetReqResource())
		r.InitSid = entry.Id()
		r.SubmitTime = time.Now().UnixNano()
		wire, err := interfaces.MarshalEvent(&interfaces.Event{Type: interfaces.SUBMIT_ROUTINE, Routine: r})
		if err != nil {
			return nil, err
		}
		if len(wire) > *mqBytes {
			return nil, fmt.Errorf("PAIX message %d exceeds frozen MQ size %d", len(wire), *mqBytes)
		}
		if err := stub.SubmitExternal(ctx, entry, r); err != nil {
			return nil, err
		}
		return &nativePending{r: r, ledger: ledger}, nil
	}
	snapshot := func() stateResponse {
		now := time.Now()
		s := stateResponse{QoSMS: cfg.QoSMS, TTLMS: cfg.TTLMS, Stubs: ledger.snapshot(), Nodes: []nodeCapacity{}}
		local := pm.Snapshot()
		s.LocalProcessors = &processorObservation{Node: self.Name, TotalCPUs: local.TotalCPUs, ReservedCPUs: local.ReservedCPUs, CommittedCPUs: local.CommittedCPUs, BorrowedCPUs: local.BorrowedCPUs, PhysicalOccupiedCPUs: local.PhysicalOccupiedCPUs, ObservedNS: local.ObservedNS}
		ledger.mu.RLock()
		peers := make([]nodeInfo, 0, len(ledger.peers))
		for _, p := range ledger.peers {
			peers = append(peers, p)
		}
		ledger.mu.RUnlock()
		sort.Slice(peers, func(i, j int) bool { return peers[i].Name < peers[j].Name })
		for _, p := range peers {
			n := nodeCapacity{Node: p.Name, Role: p.Role, TotalCPUs: p.CPUs, AgeMS: -1}
			if nd := clsstat.Singleton().GetNode(p.Name); nd != nil {
				total, remain, updated := nd.Capacity()
				n.TotalCPUs = total.CpuNum
				n.RemainingCPUs = remain.CpuNum
				n.ReservedCPUs = total.CpuNum - remain.CpuNum
				n.Valid = !updated.IsZero() && n.RemainingCPUs >= 0 && n.RemainingCPUs <= n.TotalCPUs
				if !updated.IsZero() {
					n.UpdatedNS = updated.UnixNano()
					n.AgeMS = float64(now.Sub(updated)) / float64(time.Millisecond)
				}
			}
			s.Nodes = append(s.Nodes, n)
		}
		return s
	}
	api := newAPI(self, *timeout, submit, install, snapshot)
	lis, err := net.Listen("tcp", *address)
	must(err)
	gs := grpc.NewServer()
	eventpb.RegisterEventServiceServer(gs, server.NewEventServer(dispatch))
	go func() {
		if err := gs.Serve(lis); err != nil {
			logrus.WithError(err).Error("threefig gRPC stopped")
		}
	}()
	pm.StartHeartbeat()
	hs := &http.Server{Addr: *httpAddr, Handler: api.handler(), ReadHeaderTimeout: 5 * time.Second}
	go func() {
		if err := hs.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			logrus.WithError(err).Fatal("threefig HTTP failed")
		}
	}()
	startup := map[string]any{"self": self, "native_config": cfg, "request_timeout": timeout.String(), "mq_message_bytes": *mqBytes, "mq_max_messages": *mqMessages, "rsep_test": dev.RSEPTest, "dir": dir}
	raw, err := json.MarshalIndent(startup, "", "  ")
	must(err)
	must(os.WriteFile(filepath.Join(dir, "startup.json"), append(raw, '\n'), 0600))
	fmt.Println("READY", self.Name, self.Stub)
	signals := make(chan os.Signal, 1)
	signal.Notify(signals, syscall.SIGTERM, syscall.SIGINT)
	<-signals
	shutdown, cancel := context.WithTimeout(context.Background(), *timeout+5*time.Second)
	defer cancel()
	if err := hs.Shutdown(shutdown); err != nil {
		logrus.WithError(err).Warn("HTTP shutdown incomplete")
	}
	must(sm.Close())
	must(pm.Close())
	gs.Stop()
	owned.cleanup()
}
func must(err error) {
	if err != nil {
		panic(err)
	}
}
