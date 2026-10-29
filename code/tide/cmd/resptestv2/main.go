// resptestv2 — cgroup v2 port of cmd/resptest for the RSEP single-machine
// ablation (cells C3–C6 where EI=on). Identical CLI surface to resptest plus:
//
//	-outfile   path to write per-request latency CSV (req_id, t_submit_ns,
//	           t_reply_ns, latency_ms, status)
//	-deadline  per-request hard timeout (default 30s) — caps hung routines
//	           so a single stuck request doesn't hang the whole run
//	-warmup    fraction/duration we still *record* (analyze.py trims)
//
// The switch from pkg/containerx → pkg/containerxv2 is via import alias so
// exp.go / the containerd calls stay byte-identical to the v1 harness.
package main

import (
	"flag"
	"log"
	"net/http"
	_ "net/http/pprof"
	"os"
	"runtime"
	"time"

	"github.com/ict/tide/pkg/clsstat"
	containerx "github.com/ict/tide/pkg/containerxv2"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/procmgr"
	"github.com/ict/tide/pkg/routine"
	"github.com/ict/tide/pkg/stringx"
	"github.com/ict/tide/pkg/stubmgr"
	"github.com/sirupsen/logrus"
	"golang.org/x/sys/unix"
)

var (
	fps                      = flag.Int("fps", 30, "submit frequency (RPS)")
	duration                 = flag.Duration("duration", time.Second, "run length")
	enableCpusets            = flag.Bool("cpusets", true, "PI: enable cpuset pinning")
	enableConcurrencyControl = flag.Bool("concurr-ctrl", true, "CC: enable concurrency control (queue)")
	outfile                  = flag.String("outfile", "", "per-request latency CSV path (required)")
	deadline                 = flag.Duration("deadline", 30*time.Second, "per-request hard timeout")
	warmup                   = flag.Bool("warmup", true, "run an unmeasured inference warmup before the measured window")
	resCpuNum                = flag.Int("cpu-num", 6, "cores available to Tide (matches paper's 3 physical / 6 logical)")
	appLogPath               = flag.String("log", "/tmp/tide/rsep.log", "logrus output file")
)

func init() {
	go func() {
		log.Println(http.ListenAndServe(":9998", nil))
	}()

	var rLim unix.Rlimit
	if err := unix.Getrlimit(unix.RLIMIT_MSGQUEUE, &rLim); err != nil {
		panic(err)
	}
	logrus.Info("Rlimit init: ", rLim)
	if err := unix.Setrlimit(unix.RLIMIT_MSGQUEUE,
		&unix.Rlimit{Cur: unix.RLIM_INFINITY, Max: unix.RLIM_INFINITY}); err != nil {
		panic(err)
	}
	if err := unix.Getrlimit(unix.RLIMIT_MSGQUEUE, &rLim); err != nil {
		panic(err)
	}
	logrus.Info("Rlimit final: ", rLim)

	logrus.SetLevel(logrus.DebugLevel)
	logrus.SetFormatter(&logrus.JSONFormatter{
		TimestampFormat: "2006-01-02T15:04:05.000000000",
	})
}

func setupLog() *os.File {
	if err := os.MkdirAll("/tmp/tide", 0o777); err != nil {
		logrus.Fatal("mkdir /tmp/tide failed, ", err)
	}
	logFile, err := os.OpenFile(*appLogPath, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o666)
	if err != nil {
		logrus.Fatal("open log file failed, ", err)
	}
	logrus.SetOutput(logFile)
	return logFile
}

func main() {
	flag.Parse()
	if *outfile == "" {
		logrus.Fatal("-outfile is required (per-request CSV path)")
	}
	logFile := setupLog()
	defer logFile.Close()

	dev.RSEPTest = true
	dev.EnableConcurrencyControl = *enableConcurrencyControl
	dev.EnableCpuset = *enableCpusets
	dev.EnableContainerPerRoutine = true // v1 executor path doesn't support false

	logrus.WithFields(logrus.Fields{
		"cgroupVersion":             "v2",
		"RSEPTest":                  dev.RSEPTest,
		"EnableConcurrencyControl":  dev.EnableConcurrencyControl,
		"EnableCpuset":              dev.EnableCpuset,
		"EnableContainerPerRoutine": dev.EnableContainerPerRoutine,
		"fps":                       *fps,
		"duration":                  *duration,
		"outfile":                   *outfile,
		"warmup":                    *warmup,
	}).Info("resptestv2 config")

	cpuNum := runtime.NumCPU()
	runtime.GOMAXPROCS(cpuNum)

	selfId := stringx.GenerateId()
	selfRes := &routine.Resource{CpuNum: *resCpuNum, GpuNum: 0}
	selfRole := clsstat.EDGE // PI requires EDGE; THINGS skips cpuset limits
	logrus.WithFields(logrus.Fields{
		"SelfID":     selfId,
		"SelfCpuNum": cpuNum,
		"SelfResCpu": selfRes.CpuNum,
		"SelfRole":   selfRole,
	}).Info("resptestv2 self")

	clsstat.Init(selfId, "", selfRole, selfRes)
	containerx.InitCgroup()
	containerx.InitCombCgroup()

	procM := procmgr.NewProcMgr(selfRes)
	stubM := stubmgr.NewStubMgr(procM)

	runExp(stubM, procM, *fps, *duration, *outfile, *deadline)

	onErr := func(err error) {
		if err != nil {
			logrus.Error(err)
		}
	}
	onErr(stubM.Close())
	onErr(procM.Close())
	containerx.DeleteAllCgroup()
	containerx.DeleteAllCombCgroup()
}
