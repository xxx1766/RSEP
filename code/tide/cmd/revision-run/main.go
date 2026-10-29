// revision-run drives the actual SubmitRoutine admission/execution path.
// It is an experiment driver, not a simulated scheduler or a new runtime.
package main

import (
	"encoding/csv"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"sync"
	"time"

	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/cmnct"
	"github.com/ict/tide/pkg/containerxv2"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/event"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/procmgr"
	"github.com/ict/tide/pkg/revisionworkload"
	"github.com/ict/tide/pkg/routine"
	"github.com/ict/tide/pkg/server/handlers/clsstathdlr"
	"github.com/ict/tide/pkg/server/handlers/stubhdlr"
	"github.com/ict/tide/pkg/server/handlers/stubmgrhdlr"
	"github.com/ict/tide/pkg/stubmgr"
	"github.com/sirupsen/logrus"
)

type record struct {
	id                                 int
	submit, reply, submitCall, service int64
	status                             int
	err                                string
	fn                                 string
	qosMS                              int
}

func main() {
	n := flag.Int("requests", 1, "number of offered requests")
	rps := flag.Float64("rps", 1, "open-loop arrival rate")
	cpus := flag.Int("cpus", 2, "logical CPU budget, IDs 0..N-1")
	work := flag.String("work", `{"kind":"cpu","bytes":65536,"iterations":100}`, "workload JSON")
	out := flag.String("out", "results-normal-smoke", "new result directory")
	timeout := flag.Duration("timeout", 5*time.Second, "external response wait, distinct from QoS")
	configPath := flag.String("config", "", "scheduling configuration JSON")
	classFlag := flag.String("qos-classes-ms", "", "alternating deadline classes, e.g.100,500; no priority ordering")
	flag.Parse()
	if *configPath != "" {
		if err := dev.LoadConfig(*configPath); err != nil {
			panic(err)
		}
	}
	if *n < 1 || *rps <= 0 || *cpus < 2 || *timeout <= 0 {
		panic("invalid experiment arguments")
	}
	if err := os.Mkdir(*out, 0755); err != nil {
		panic(err)
	}
	logfile, err := os.Create(filepath.Join(*out, "runtime.jsonl"))
	if err != nil {
		panic(err)
	}
	defer logfile.Close()
	logrus.SetOutput(logfile)
	logrus.SetLevel(logrus.InfoLevel)
	logrus.SetFormatter(&logrus.JSONFormatter{})
	for key, value := range map[string]string{"TIDE_MQ_MESSAGE_BYTES": "8192", "TIDE_MQ_MAX_MESSAGES": "10"} {
		if os.Getenv(key) == "" {
			if err := os.Setenv(key, value); err != nil {
				panic(err)
			}
		}
	}
	var workload revisionworkload.Config
	if err := json.Unmarshal([]byte(*work), &workload); err != nil {
		panic(err)
	}
	classes, err := revisionworkload.ParseDeadlineClasses(*classFlag)
	if err != nil {
		panic(err)
	}
	cfg := dev.CurrentConfig()
	if len(classes) > 0 {
		if cfg.QoSByFunctionMS == nil {
			cfg.QoSByFunctionMS = make(map[string]int)
		}
		for i := range classes {
			fn, ms := classes.ForRequest(i, workload.Kind, cfg.QoSMS)
			cfg.QoSByFunctionMS[fn] = ms
		}
		if err := dev.SetConfig(cfg); err != nil {
			panic(err)
		}
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
	createNs := time.Since(createStart).Nanoseconds()
	stubhdlr.RegisterEventHandler(dispatch, st)
	clsstat.Singleton().AddStub(&clsstat.AddStubOpts{StubId: st.Id(), AppId: appID, NodeId: selfID, Mode: interfaces.StubFn, CreateTime: createStart})
	procM.StartHeartbeat()
	// Allow the existing asynchronous processFnLoop to enter RUNNING. This is
	// setup time, recorded separately and outside the measured arrival window.
	time.Sleep(100 * time.Millisecond)
	t0 := time.Now()
	endNs := int64(float64(*n) / *rps * float64(time.Second))
	ch := make(chan record, *n)
	var wg sync.WaitGroup
	for i := 0; i < *n; i++ {
		at := t0.Add(time.Duration(float64(i) / *rps * float64(time.Second)))
		if wait := time.Until(at); wait > 0 {
			time.Sleep(wait)
		}
		wg.Add(1)
		go func(id int) {
			defer wg.Done()
			fn, qosMS := classes.ForRequest(id, workload.Kind, dev.GetToleranceTimeFor(workload.Kind)/int(time.Millisecond))
			r := routine.NewSubmittedRtn(fmt.Sprintf("revision-%d", id), fn, []byte(*work), dev.GetReqResource())
			r.InitSid = st.Id()
			r.SubmitTime = time.Now().UnixNano()
			rec := record{id: id, submit: time.Since(t0).Nanoseconds(), fn: fn, qosMS: qosMS}
			call := time.Now()
			st.SubmitRoutine(r)
			rec.submitCall = time.Since(call).Nanoseconds()
			select {
			case <-r.WaitData():
				rec.status = 200
				if r.Status() != routine.EXIT_SUCC {
					rec.status = 500
					rec.err = r.Error()
				} else {
					var reply struct {
						ServiceNs int64 `json:"service_ns"`
					}
					if err := json.Unmarshal(r.Result(), &reply); err != nil {
						rec.status = 500
						rec.err = err.Error()
					} else {
						rec.service = reply.ServiceNs
					}
				}
			case <-time.After(*timeout):
				rec.status = 504
				rec.err = "external response wait expired"
			}
			rec.reply = time.Since(t0).Nanoseconds()
			ch <- rec
		}(i)
	}
	wg.Wait()
	close(ch)
	f, err := os.Create(filepath.Join(*out, "raw.csv"))
	if err != nil {
		panic(err)
	}
	w := csv.NewWriter(f)
	w.Write([]string{"req_id", "t_submit_ns", "t_reply_ns", "latency_ms", "status", "qos_ms", "submit_call_ns", "worker_service_ns", "error", "fn_name"})
	ok := 0
	for r := range ch {
		if r.status == 200 {
			ok++
		}
		w.Write([]string{strconv.Itoa(r.id), strconv.FormatInt(r.submit, 10), strconv.FormatInt(r.reply, 10), fmt.Sprintf("%.6f", float64(r.reply-r.submit)/1e6), strconv.Itoa(r.status), strconv.Itoa(r.qosMS), strconv.FormatInt(r.submitCall, 10), strconv.FormatInt(r.service, 10), r.err, r.fn})
	}
	w.Flush()
	if err := w.Error(); err != nil {
		panic(err)
	}
	f.Close()
	meta := map[string]any{"status": "completed", "system": "Tide normal SubmitRoutine path", "requests": *n, "successes": ok, "rps": *rps, "cpus": *cpus, "workload": workload, "qos_ms": float64(dev.GetToleranceTime()) / 1e6, "exec_estimate_ms": float64(dev.GetEstExecTime(selfID)) / 1e6, "measurement_start_ns": 0, "measurement_end_ns": endNs, "external_timeout_ns": timeout.Nanoseconds(), "stub_with_initial_executor_create_ns": createNs, "pid": os.Getpid(), "started_utc": t0.UTC(), "argv": os.Args, "mq_message_bytes": os.Getenv("TIDE_MQ_MESSAGE_BYTES"), "mq_max_messages": os.Getenv("TIDE_MQ_MAX_MESSAGES")}
	meta["effective_config"] = dev.CurrentConfig()
	meta["qos_classes_ms"] = classes
	meta["measurement_start_unix_ns"] = t0.UnixNano()
	meta["qos_ms"] = float64(dev.GetToleranceTimeFor(workload.Kind)) / 1e6
	data, _ := json.MarshalIndent(meta, "", "  ")
	if err := os.WriteFile(filepath.Join(*out, "run.json"), data, 0644); err != nil {
		panic(err)
	}
	fmt.Println(string(data))
	// Result notification precedes legacy postProcess resource return. The
	// integration smoke checks actual cleanup after a bounded drain interval.
	time.Sleep(500 * time.Millisecond)
	if err := stubM.Close(); err != nil {
		panic(err)
	}
	procM.Close()
	containerxv2.DeleteAllCgroup()
	containerxv2.DeleteAllCombCgroup()
}
