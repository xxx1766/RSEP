package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/cmnct"
	"github.com/ict/tide/pkg/containerxv2"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/event"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/procmgr"
	"github.com/ict/tide/pkg/routine"
	"github.com/ict/tide/pkg/server/handlers/clsstathdlr"
	"github.com/ict/tide/pkg/server/handlers/stubhdlr"
	"github.com/ict/tide/pkg/server/handlers/stubmgrhdlr"
	"github.com/ict/tide/pkg/stubmgr"
	"github.com/sirupsen/logrus"
	"io"
	"net/http"
	"os"
	"os/signal"
	"sync/atomic"
	"syscall"
	"time"
)

func main() {
	cpus := flag.Int("cpus", 2, "application logical CPU budget")
	addr := flag.String("listen", "127.0.0.1:19280", "HTTP endpoint")
	flag.Parse()
	os.Setenv("TIDE_MQ_MESSAGE_BYTES", "8192")
	os.Setenv("TIDE_MQ_MAX_MESSAGES", "10")
	logrus.SetFormatter(&logrus.JSONFormatter{})
	cfg := dev.DefaultConfig()
	cfg.ExecutionMS = 22
	cfg.TTLMS = 60000
	cfg.QoSByFunctionMS = map[string]int{"cpu-qos50": 50, "cpu-qos200": 200, "cpu-qos1000": 1000}
	if err := dev.SetConfig(cfg); err != nil {
		panic(err)
	}
	selfID, appID := "revision-node", "revision-app"
	address := "127.0.0.1:19129"
	resources := &routine.Resource{CpuNum: *cpus}
	clsstat.Init(selfID, address, clsstat.EDGE, resources)
	containerxv2.InitCgroup()
	containerxv2.InitCombCgroup()
	procM := procmgr.NewProcMgr(resources)
	stubM := stubmgr.NewStubMgr(procM)
	dispatch := event.NewEventDispatcher()
	cmnct.Init(selfID, address, dispatch)
	clsstathdlr.RegisterEventHandler(dispatch)
	stubmgrhdlr.RegisterEventHandler(dispatch, stubM)
	image := "docker.io/tide/revision-worker"
	commands := []string{"/worker", "--mode=paix"}
	clsstat.Singleton().AddApp(&clsstat.AddAppOpts{AppId: appID, ImageName: image, Commands: commands})
	createStart := time.Now()
	st, err := stubM.CreateStub(&stubmgr.CreateStubOpts{AppId: appID, ImgName: image, Commands: commands, Mode: interfaces.StubFn, ResReq: dev.GetStubResource(), InitExecNo: 1})
	if err != nil || st == nil {
		panic(fmt.Sprintf("create stub: %v", err))
	}
	stubhdlr.RegisterEventHandler(dispatch, st)
	clsstat.Singleton().AddStub(&clsstat.AddStubOpts{StubId: st.Id(), AppId: appID, NodeId: selfID, Mode: interfaces.StubFn, CreateTime: createStart})
	procM.StartHeartbeat()
	time.Sleep(100 * time.Millisecond)

	var seq atomic.Uint64
	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) { fmt.Fprintln(w, "ready") })
	mux.HandleFunc("/run", func(w http.ResponseWriter, req *http.Request) {
		fn := "cpu-qos" + req.Header.Get("X-Qos-Ms")
		if _, ok := cfg.QoSByFunctionMS[fn]; !ok {
			http.Error(w, "invalid deadline", 400)
			return
		}
		data, err := io.ReadAll(http.MaxBytesReader(w, req.Body, 8192))
		if err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		r := routine.NewSubmittedRtn(fmt.Sprintf("control-%d", seq.Add(1)), fn, data, dev.GetReqResource())
		r.InitSid = st.Id()
		r.SubmitTime = time.Now().UnixNano()
		st.SubmitRoutine(r)
		select {
		case <-r.WaitData():
			if r.Status() != routine.EXIT_SUCC {
				http.Error(w, r.Error(), 503)
				return
			}
			w.Header().Set("Content-Type", "application/json")
			w.Write(r.Result())
		case <-time.After(30 * time.Second):
			http.Error(w, "Tide response timeout", 504)
		}
	})
	server := &http.Server{Addr: *addr, Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	go func() {
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			panic(err)
		}
	}()
	json.NewEncoder(os.Stdout).Encode(map[string]any{"status": "ready", "pid": os.Getpid(), "config": cfg})
	done := make(chan os.Signal, 1)
	signal.Notify(done, syscall.SIGTERM, syscall.SIGINT)
	<-done
	ctx, cancel := context.WithTimeout(context.Background(), 35*time.Second)
	defer cancel()
	if err := server.Shutdown(ctx); err != nil {
		panic(err)
	}
	time.Sleep(500 * time.Millisecond)
	if err := stubM.Close(); err != nil {
		panic(err)
	}
	procM.Close()
	containerxv2.DeleteAllCgroup()
	containerxv2.DeleteAllCombCgroup()
}
