package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/signal"
	"sync/atomic"
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
)

const appID = "fig7-cluster-mixed-app"
const imageName = "docker.io/tide/fig6-cluster-20261014"

type nodeInfo struct {
	Name    string           `json:"name"`
	Address string           `json:"address"`
	Role    clsstat.NodeRole `json:"role"`
	CPUs    int              `json:"cpus"`
	Slots   int              `json:"slots"`
	Stub    string           `json:"stub"`
	PID     int              `json:"pid"`
}

func main() {
	name := flag.String("node", "", "physical node identifier")
	addr := flag.String("address", "", "advertised gRPC address")
	httpAddr := flag.String("http", "0.0.0.0:29801", "HTTP measurement ingress")
	roleArg := flag.String("role", "edge", "edge or cloud")
	cpus := flag.Int("cpus", 2, "application logical CPUs (IDs 0..N-1)")
	defaultIO := os.Getenv("RSEP_IO_DIR")
	if defaultIO == "" {
		defaultIO = "/var/lib/rsep/io-data"
	}
	ioDir := flag.String("io-dir", defaultIO, "host-backed I/O directory")
	logPath := flag.String("log", "runtime.jsonl", "native runtime log")
	configPath := flag.String("config", "", "scheduling config")
	flag.Parse()
	if *name == "" || *addr == "" || (*cpus != 2 && *cpus != 4) {
		panic("invalid node/CPU configuration")
	}
	role := clsstat.ParseRole(*roleArg)
	if role != clsstat.EDGE && role != clsstat.CLOUD {
		panic("invalid role")
	}
	if *configPath != "" {
		must(dev.LoadConfig(*configPath))
		must(validateClassConfig(dev.CurrentConfig()))
	}
	cfg := dev.CurrentConfig()
	cfg.TTLMS = 86400000
	must(dev.SetConfig(cfg))
	must(os.Setenv("TIDE_MQ_MESSAGE_BYTES", "8192"))
	must(os.Setenv("TIDE_MQ_MAX_MESSAGES", "10"))
	f, err := os.Create(*logPath)
	must(err)
	defer f.Close()
	logrus.SetOutput(f)
	logrus.SetFormatter(&logrus.JSONFormatter{})
	logrus.SetLevel(logrus.InfoLevel)
	must(os.MkdirAll(*ioDir, 0755))
	resources := &routine.Resource{CpuNum: *cpus}
	clsstat.Init(*name, *addr, role, resources)
	containerxv2.InitCgroup()
	if role == clsstat.EDGE {
		containerxv2.InitCombCgroup()
	}
	pm := procmgr.NewProcMgr(resources)
	sm := stubmgr.NewStubMgr(pm)
	dispatch := event.NewEventDispatcher()
	cmnct.Init(*name, *addr, dispatch)
	clsstathdlr.RegisterEventHandler(dispatch)
	stubmgrhdlr.RegisterEventHandler(dispatch, sm)
	commands := []string{"/worker", "--mode=paix", "--node=" + *name, "--tmpdir=/fig6-io"}
	clsstat.Singleton().AddApp(&clsstat.AddAppOpts{AppId: appID, ImageName: imageName, Commands: commands})
	st, err := sm.CreateStub(&stubmgr.CreateStubOpts{AppId: appID, ImgName: imageName, VolumePair: *ioDir + ":/fig6-io", Commands: commands, Mode: interfaces.StubFn, ResReq: dev.GetStubResource(), InitExecNo: *cpus / 2})
	must(err)
	if st == nil {
		panic("no native stub")
	}
	stubhdlr.RegisterEventHandler(dispatch, st)
	if role == clsstat.CLOUD {
		cloudhdlr.RegisterStubHandlers(dispatch, st)
	}
	if role != clsstat.CLOUD {
		clsstat.Singleton().AddStub(&clsstat.AddStubOpts{StubId: st.Id(), AppId: appID, NodeId: *name, Mode: interfaces.StubFn, CreateTime: time.Now()})
	}
	self := nodeInfo{*name, *addr, role, *cpus, *cpus / 2, st.Id(), os.Getpid()}
	lis, err := net.Listen("tcp", *addr)
	must(err)
	grpcServer := grpc.NewServer()
	eventpb.RegisterEventServiceServer(grpcServer, server.NewEventServer(dispatch))
	go func() { must(grpcServer.Serve(lis)) }()
	pm.StartHeartbeat()
	var sequence atomic.Uint64
	var joined atomic.Bool
	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) { writeJSON(w, self) })
	mux.HandleFunc("/peers", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" {
			http.Error(w, "POST peers exactly once", 409)
			return
		}
		var peers []nodeInfo
		if err := json.NewDecoder(io.LimitReader(r.Body, 32768)).Decode(&peers); err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		seen := map[string]bool{}
		for _, p := range peers {
			wantCPU, wantRole := 2, clsstat.EDGE
			if p.Name == "nj1" {
				wantCPU, wantRole = 4, clsstat.CLOUD
			}
			if (p.Name != "nj1" && p.Name != "nj2" && p.Name != "nj3") || seen[p.Name] || p.CPUs != wantCPU || p.Role != wantRole || p.Stub == "" || p.Address == "" {
				http.Error(w, "invalid three-node inventory", 400)
				return
			}
			if p.Name == self.Name && p.Stub != self.Stub {
				http.Error(w, "self stub mismatch", 400)
				return
			}
			seen[p.Name] = true
		}
		if len(peers) != 3 {
			http.Error(w, "three peers required", 400)
			return
		}
		if !joined.CompareAndSwap(false, true) {
			http.Error(w, "peers already registered", 409)
			return
		}
		for _, p := range peers {
			if p.Name == self.Name {
				continue
			}
			clsstat.Singleton().AddNode(&clsstat.AddNodeOpts{Nid: p.Name, Address: p.Address, Role: p.Role, ResTotal: &routine.Resource{CpuNum: p.CPUs}})
			if p.Role != clsstat.CLOUD {
				clsstat.Singleton().AddStub(&clsstat.AddStubOpts{StubId: p.Stub, AppId: appID, NodeId: p.Name, Mode: interfaces.StubFn, CreateTime: time.Now()})
			}
		}
		writeJSON(w, map[string]any{"peers": peers, "self": self})
	})
	mux.HandleFunc("/config", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "POST" {
			writeJSON(w, dev.CurrentConfig())
			return
		}
		var c dev.Config
		if err := json.NewDecoder(io.LimitReader(r.Body, 32768)).Decode(&c); err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		c.TTLMS = 86400000
		if err := validateClassConfig(c); err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		if err := dev.SetConfig(c); err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		writeJSON(w, dev.CurrentConfig())
	})
	mux.Handle("/run", mixedHandler(func(ctx context.Context, id, function string, data []byte) ([]byte, int, error) {
		rtn := routine.NewSubmittedRtn(id, function, data, dev.GetReqResource())
		rtn.InitSid = st.Id()
		rtn.SubmitTime = time.Now().UnixNano()
		if err := stub.SubmitExternal(ctx, st, rtn); err != nil {
			return nil, 503, err
		}
		select {
		case <-rtn.WaitData():
			if rtn.Status() != routine.EXIT_SUCC {
				return nil, 503, fmt.Errorf("%s", rtn.Error())
			}
			return rtn.Result(), 200, nil
		case <-ctx.Done():
			return nil, 504, fmt.Errorf("external native result wait expired")
		}
	}, func() string { return fmt.Sprintf("%s-%d-%d", self.Name, self.PID, sequence.Add(1)) }))
	hs := &http.Server{Addr: *httpAddr, Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	go func() {
		if err := hs.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			panic(err)
		}
	}()
	fmt.Println("READY", self.Name, self.Stub)
	signals := make(chan os.Signal, 1)
	signal.Notify(signals, syscall.SIGTERM, syscall.SIGINT)
	<-signals
	hs.Close()
	grpcServer.Stop()
	must(sm.Close())
	must(pm.Close())
	containerxv2.DeleteAllCgroup()
	if role == clsstat.EDGE {
		containerxv2.DeleteAllCombCgroup()
	}
}

func must(err error) {
	if err != nil {
		panic(err)
	}
}
func writeJSON(w http.ResponseWriter, x any) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(x)
}
