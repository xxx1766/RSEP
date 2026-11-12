package main

import (
	"context"
	"encoding/csv"
	"encoding/json"
	"flag"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"sync"
	"time"

	"github.com/containerd/containerd"
	"github.com/containerd/containerd/namespaces"
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
	"golang.org/x/sys/unix"
)

const appID = "revision-ttl-app"
const imageName = "docker.io/tide/revision-worker"

type options struct {
	config, out, work, idleGaps string
	ttl, burstRequests          int
	burstRPS                    float64
	timeout                     time.Duration
}

type requestRecord struct {
	ID, Burst, Status                                           int
	Arrive, RuntimeSubmit, Reply, Ensure, Create, Call, Service int64
	StubID, CreationKind, Error                                 string
}

type localRuntime struct {
	mu         sync.Mutex
	manager    *stubmgr.StubManager
	processors *procmgr.ProcessorManager
	dispatch   event.EventDispatcher
	observe    *observer
	client     *containerd.Client
	before     map[string]uint32
	owned      map[string]uint32
	current    interfaces.Stub
	state      *clsstat.StubState
	creations  int
}

func inventory(client *containerd.Client) (map[string]uint32, error) {
	ctx, cancel := context.WithTimeout(namespaces.WithNamespace(context.Background(), "tide"), 5*time.Second)
	defer cancel()
	containers, err := client.Containers(ctx)
	if err != nil {
		return nil, err
	}
	result := make(map[string]uint32, len(containers))
	for _, c := range containers {
		result[c.ID()] = 0
		if task, err := c.Task(ctx, nil); err == nil {
			result[c.ID()] = task.Pid()
		}
	}
	return result, nil
}

func (r *localRuntime) observeContainers(phase string) error {
	current, err := inventory(r.client)
	if err != nil {
		return err
	}
	owned := make(map[string]uint32)
	for id, pid := range current {
		if _, existed := r.before[id]; !existed {
			owned[id] = pid
		}
	}
	r.observe.emit("container_inventory", map[string]any{"phase": phase, "owned_container_pids": owned})
	if len(owned) != 1 {
		return fmt.Errorf("expected exactly one persistent experiment worker, observed %v", owned)
	}
	for id, pid := range owned {
		if pid == 0 {
			return fmt.Errorf("worker %s has no running task", id)
		}
		if len(r.owned) > 0 && r.owned[id] != pid {
			return fmt.Errorf("worker identity changed across TTL: %v -> %v", r.owned, owned)
		}
	}
	r.owned = owned
	return nil
}

func newLocalRuntime(o *observer) (*localRuntime, error) {
	var affinity unix.CPUSet
	if err := unix.SchedGetaffinity(0, &affinity); err != nil {
		return nil, err
	}
	if !affinity.IsSet(0) || !affinity.IsSet(1) {
		return nil, fmt.Errorf("experiment requires allowed logical CPUs 0 and 1")
	}
	client, err := containerd.New("/run/containerd/containerd.sock")
	if err != nil {
		return nil, err
	}
	before, err := inventory(client)
	if err != nil {
		client.Close()
		return nil, err
	}
	resources := &routine.Resource{CpuNum: 2}
	clsstat.Init("revision-ttl-node", "ttl-local-only", clsstat.EDGE, resources)
	containerxv2.InitCgroup()
	containerxv2.InitCombCgroup()
	pm := procmgr.NewProcMgr(resources)
	sm := stubmgr.NewStubMgr(pm)
	dispatch := &observedDispatcher{event.NewEventDispatcher(), o}
	cmnct.Init("revision-ttl-node", "ttl-local-only", dispatch)
	clsstathdlr.RegisterEventHandler(dispatch)
	stubmgrhdlr.RegisterEventHandler(dispatch, sm)
	clsstat.Singleton().AddApp(&clsstat.AddAppOpts{AppId: appID, ImageName: imageName, Commands: []string{"/worker", "--mode=paix"}})
	pm.StartHeartbeat()
	return &localRuntime{manager: sm, processors: pm, dispatch: dispatch, observe: o, client: client, before: before}, nil
}

func (r *localRuntime) ensureStub() (interfaces.Stub, string, int64, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.current != nil {
		status, _ := r.state.SchedulingState()
		if status != clsstat.STUB_STOPPED {
			return r.current, "existing_stub", 0, nil
		}
	}
	kind, initialExecutors := "stub_rebind_existing_executor", 0
	if r.creations == 0 {
		kind, initialExecutors = "initial_executor_and_stub", 1
	}
	begin := time.Now()
	r.observe.emit("stub_create_begin", map[string]any{"creation_kind": kind})
	st, err := r.manager.CreateStub(&stubmgr.CreateStubOpts{AppId: appID, ImgName: imageName, Commands: []string{"/worker", "--mode=paix"}, Mode: interfaces.StubFn, ResReq: dev.GetStubResource(), InitExecNo: initialExecutors})
	createNS := time.Since(begin).Nanoseconds()
	if err != nil {
		return nil, kind, createNS, err
	}
	if st == nil {
		return nil, kind, createNS, fmt.Errorf("CreateStub returned nil")
	}
	stubhdlr.RegisterEventHandler(r.dispatch, st)
	clsstat.Singleton().AddStub(&clsstat.AddStubOpts{StubId: st.Id(), AppId: appID, NodeId: "revision-ttl-node", Mode: interfaces.StubFn, CreateTime: begin})
	r.current, r.state = st, clsstat.Singleton().GetStub(st.Id())
	r.creations++
	r.observe.state("stub_created", st.Id(), true)
	r.observe.emit("stub_create_end", map[string]any{"stub_id": st.Id(), "creation_kind": kind, "create_ns": createNS})
	time.Sleep(5 * time.Millisecond)
	if err := r.observeContainers("after_stub_create"); err != nil {
		return nil, kind, createNS, err
	}
	return st, kind, createNS, nil
}

func (r *localRuntime) close() error {
	err := r.manager.Close()
	if e := r.processors.Close(); err == nil {
		err = e
	}
	containerxv2.DeleteAllCgroup()
	containerxv2.DeleteAllCombCgroup()
	current, e := inventory(r.client)
	if err == nil {
		err = e
	}
	for id := range r.owned {
		if _, exists := current[id]; exists && err == nil {
			err = fmt.Errorf("owned container %s survived cleanup", id)
		}
	}
	r.client.Close()
	return err
}

func submit(r *localRuntime, opt options, work revisionworkload.Config, id, burst int, epoch time.Time) (rec requestRecord) {
	arrive := time.Now()
	rec.ID, rec.Burst, rec.Arrive = id, burst, time.Since(epoch).Nanoseconds()
	rec.Status = 500
	defer func() { rec.Reply = time.Since(epoch).Nanoseconds() }()
	start := time.Now()
	st, kind, createNS, err := r.ensureStub()
	rec.Ensure, rec.Create, rec.CreationKind = time.Since(start).Nanoseconds(), createNS, kind
	if err != nil {
		rec.Error = err.Error()
		return
	}
	rec.StubID = st.Id()
	remaining := time.Until(arrive.Add(opt.timeout))
	if remaining <= 0 {
		rec.Status, rec.Error = 504, "external deadline expired during local recreation"
		return
	}
	rtn := routine.NewSubmittedRtn(fmt.Sprintf("ttl-%d", id), work.Kind, []byte(opt.work), dev.GetReqResource())
	rtn.InitSid, rtn.SubmitTime = st.Id(), arrive.UnixNano()
	rec.RuntimeSubmit = time.Since(epoch).Nanoseconds()
	call := time.Now()
	st.SubmitRoutine(rtn)
	rec.Call = time.Since(call).Nanoseconds()
	timer := time.NewTimer(max(time.Until(arrive.Add(opt.timeout)), 0))
	defer timer.Stop()
	select {
	case <-rtn.WaitData():
		if rtn.Status() != routine.EXIT_SUCC {
			rec.Error = rtn.Error()
			return
		}
		var reply struct {
			Result    revisionworkload.Result `json:"result"`
			ServiceNS int64                   `json:"service_ns"`
			Error     string                  `json:"error"`
		}
		if err := json.Unmarshal(rtn.Result(), &reply); err != nil {
			rec.Error = err.Error()
			return
		}
		if reply.Error != "" || reply.ServiceNS <= 0 || reply.Result.BytesProcessed != int64(work.Bytes)*int64(work.Iterations) {
			rec.Error = "worker reply invalid: " + reply.Error
			return
		}
		rec.Status, rec.Service = 200, reply.ServiceNS
	case <-timer.C:
		rec.Status, rec.Error = 504, "external response wait expired; kernel is not cancelled"
	}
	return
}

func run(opt options) (runErr error) {
	if opt.config == "" {
		return fmt.Errorf("--config is required: use the frozen CPU calibration")
	}
	if err := dev.LoadConfig(opt.config); err != nil {
		return err
	}
	cfg := dev.CurrentConfig()
	cfg.TTLMS = opt.ttl
	if err := dev.SetConfig(cfg); err != nil {
		return err
	}
	if opt.burstRequests < 1 || opt.burstRPS <= 0 || math.IsNaN(opt.burstRPS) || math.IsInf(opt.burstRPS, 0) || opt.timeout <= 0 || opt.ttl < 20 {
		return fmt.Errorf("require positive counts/rate/timeout and TTL>=20ms")
	}
	gaps, err := parseIdleGaps(opt.idleGaps, opt.ttl)
	if err != nil {
		return err
	}
	var work revisionworkload.Config
	if err := json.Unmarshal([]byte(opt.work), &work); err != nil {
		return err
	}
	if work.Kind != "cpu" || work.Bytes < 8 || work.Bytes > 256<<20 || work.Iterations < 1 || work.Iterations > 100000 {
		return fmt.Errorf("TTL experiment requires a valid real CPU workload")
	}
	if dev.GetToleranceTimeFor(work.Kind) != int(500*time.Millisecond) {
		return fmt.Errorf("TTL comparison requires calibrated CPU QoS 500ms")
	}
	if err := os.Mkdir(opt.out, 0755); err != nil {
		return err
	}
	files := make(map[string]*os.File)
	for _, name := range []string{"runtime.jsonl", "events.jsonl", "resources.jsonl", "raw.csv"} {
		f, err := os.Create(filepath.Join(opt.out, name))
		if err != nil {
			return err
		}
		files[name] = f
		defer f.Close()
	}
	logrus.SetOutput(files["runtime.jsonl"])
	logrus.SetFormatter(&logrus.JSONFormatter{})
	logrus.SetLevel(logrus.InfoLevel)
	for key, value := range map[string]string{"TIDE_MQ_MESSAGE_BYTES": "8192", "TIDE_MQ_MAX_MESSAGES": "10"} {
		if err := os.Setenv(key, value); err != nil {
			return err
		}
	}
	epoch := time.Now()
	observe := newObserver(epoch, files["events.jsonl"], files["resources.jsonl"])
	w := csv.NewWriter(files["raw.csv"])
	w.Write([]string{"req_id", "burst", "t_submit_ns", "runtime_submit_ns", "t_reply_ns", "latency_ms", "status", "qos_ms", "stub_id", "creation_kind", "ensure_stub_ns", "stub_create_ns", "submit_call_ns", "worker_service_ns", "error"})
	w.Flush()
	rows := []requestRecord{}
	var accounting allocationStats
	finished := false
	var runtime *localRuntime
	defer func() {
		if !finished {
			var err error
			accounting, err = observe.finish()
			if runErr == nil {
				runErr = err
			}
		}
		w.Flush()
		if runErr == nil {
			runErr = w.Error()
		}
		status, errorText := "completed", ""
		if runErr != nil {
			status, errorText = "failed", runErr.Error()
		}
		successes := 0
		for _, row := range rows {
			if row.Status == 200 {
				successes++
			}
		}
		creations := 0
		if runtime != nil {
			creations = runtime.creations
		}
		meta := map[string]any{"status": status, "error": errorText, "pid": os.Getpid(), "argv": os.Args, "system": "Tide local TTL recreation", "cpus": 2, "workload": work, "effective_config": cfg, "bursts": len(gaps), "burst_requests": opt.burstRequests, "burst_rps": opt.burstRPS, "idle_gaps_ns": gaps, "observation_margin_ns": observationMargin.Nanoseconds(), "expected_requests": len(gaps) * opt.burstRequests, "requests": len(rows), "successes": successes, "stub_creations": creations, "allocation": accounting, "measurement_start_unix_ns": epoch.UnixNano(), "scope": "local-only driver creates/recreates used function stubs via StubManager.CreateStub and submits real kernels via SubmitRoutine; no distributed originating-stub result", "allocation_semantics": "integral of observed high-priority CPU reservation snapshots from existing 5ms runtime heartbeat; phase boundaries carry last observation; not CPU consumption or exact internal transition time", "idle_semantics": "fixed post-burst interval after all request outcomes; any external timeout invalidates run", "executor_semantics": "one initial container/executor, retained app pool reused with InitExecNo=0 after stub TTL; TTL releases logical CPU reservation, not container memory", "cold_latency_semantics": "request arrival precedes local creation; arrival-to-reply includes creation,5ms settle,and scheduling; SubmitTime uses original arrival for QoS", "creation_kind_semantics": "ensureStub action owned by this request; existing_stub may wait behind another request creating it; inspect ensure_stub_ns", "startup_settle_ns": int64(5 * time.Millisecond), "external_timeout_ns": opt.timeout.Nanoseconds()}
		data, err := json.MarshalIndent(meta, "", "  ")
		if err == nil {
			err = os.WriteFile(filepath.Join(opt.out, "run.json"), data, 0644)
		}
		if runErr == nil {
			runErr = err
		}
	}()
	runtime, err = newLocalRuntime(observe)
	if err != nil {
		return err
	}
	defer func() {
		if err := runtime.close(); runErr == nil {
			runErr = err
		}
	}()
	deadline := time.Now().Add(time.Second)
	for !observe.released() {
		if time.Now().After(deadline) {
			return fmt.Errorf("initial free-capacity heartbeat missing")
		}
		time.Sleep(time.Millisecond)
	}
	for burst, gap := range gaps {
		observe.phase(false, burst)
		start := time.Now()
		results := make(chan requestRecord, opt.burstRequests)
		for index := 0; index < opt.burstRequests; index++ {
			if wait := time.Until(start.Add(time.Duration(float64(index) / opt.burstRPS * float64(time.Second)))); wait > 0 {
				time.Sleep(wait)
			}
			id := burst*opt.burstRequests + index
			go func(id int) { results <- submit(runtime, opt, work, id, burst, epoch) }(id)
		}
		burstRows := make([]requestRecord, 0, opt.burstRequests)
		for index := 0; index < opt.burstRequests; index++ {
			burstRows = append(burstRows, <-results)
		}
		sort.Slice(burstRows, func(i, j int) bool { return burstRows[i].ID < burstRows[j].ID })
		timedOut, successes := false, 0
		for _, row := range burstRows {
			rows = append(rows, row)
			w.Write([]string{strconv.Itoa(row.ID), strconv.Itoa(row.Burst), strconv.FormatInt(row.Arrive, 10), strconv.FormatInt(row.RuntimeSubmit, 10), strconv.FormatInt(row.Reply, 10), fmt.Sprintf("%.6f", float64(row.Reply-row.Arrive)/1e6), strconv.Itoa(row.Status), strconv.Itoa(cfg.QoSMS), row.StubID, row.CreationKind, strconv.FormatInt(row.Ensure, 10), strconv.FormatInt(row.Create, 10), strconv.FormatInt(row.Call, 10), strconv.FormatInt(row.Service, 10), row.Error})
			if row.Status == 504 {
				timedOut = true
			}
			if row.Status == 200 {
				successes++
			}
		}
		w.Flush()
		if err := w.Error(); err != nil {
			return err
		}
		if timedOut {
			return fmt.Errorf("external timeout leaves kernel state unknown; idle allocation window not claimed")
		}
		observe.emit("burst_outcomes_recorded", map[string]any{"burst": burst, "requests": len(burstRows), "successes": successes})
		if successes == 0 {
			return fmt.Errorf("burst %d never successfully used its stub; cannot claim used-stub idle interval", burst)
		}
		observe.phase(true, burst)
		time.Sleep(gap)
		if err := observe.gapState(burst, gap, time.Duration(opt.ttl)*time.Millisecond); err != nil {
			return err
		}
		if err := runtime.observeContainers("after_idle_gap"); err != nil {
			return err
		}
	}
	accounting, err = observe.finish()
	finished = true
	if err != nil {
		return err
	}
	used := false
	for _, row := range rows {
		if row.Status == 200 {
			used = true
		}
	}
	if !used {
		return fmt.Errorf("no successful real requests; cannot claim used-stub TTL evidence")
	}
	return nil
}

func main() {
	var opt options
	flag.StringVar(&opt.config, "config", "", "frozen calibrated scheduler configuration JSON")
	flag.StringVar(&opt.out, "out", "results-ttl", "new output directory")
	flag.StringVar(&opt.work, "work", `{"kind":"cpu","bytes":65536,"iterations":100}`, "real CPU kernel JSON")
	flag.IntVar(&opt.ttl, "ttl-ms", 2000, "fixed stub idle TTL milliseconds")
	flag.IntVar(&opt.burstRequests, "burst-requests", 4, "offered requests per burst")
	flag.Float64Var(&opt.burstRPS, "burst-rps", 20, "open-loop request rate inside each burst")
	flag.StringVar(&opt.idleGaps, "idle-gaps-ms", "250,1000,2500", "three post-outcome idle gaps; final gap must exceed every TTL plus50ms")
	flag.DurationVar(&opt.timeout, "timeout", 5*time.Second, "external response deadline including local recreation")
	flag.Parse()
	if err := run(opt); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
