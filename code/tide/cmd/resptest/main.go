package main

import (
	"flag"
	"log"
	"os"
	"runtime"
	"time"

	"net/http"
	_ "net/http/pprof"

	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/containerx"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/procmgr"
	"github.com/ict/tide/pkg/routine"
	"github.com/ict/tide/pkg/stringx"
	"github.com/ict/tide/pkg/stubmgr"
	"github.com/sirupsen/logrus"
	"golang.org/x/sys/unix"
)

var (
	fps                      = flag.Int("fps", 30, "submit frequency")
	duration                 = flag.Duration("duration", time.Second, "the duration test")
	enableCpusets            = flag.Bool("cpusets", true, "enable cpusets")
	enableConcurrencyControl = flag.Bool("concurr-ctrl", true, "enable concurrency control")
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
	if err := unix.Setrlimit(unix.RLIMIT_MSGQUEUE, &unix.Rlimit{Cur: unix.RLIM_INFINITY, Max: unix.RLIM_INFINITY}); err != nil {
		panic(err)
	}
	if err := unix.Getrlimit(unix.RLIMIT_MSGQUEUE, &rLim); err != nil {
		panic(err)
	}
	logrus.Info("Rlimit final: ", rLim)

	// setup logger
	logrus.SetLevel(logrus.DebugLevel)
	// logrus.SetFormatter(&logrus.TextFormatter{
	// 	FullTimestamp:   true,
	// 	TimestampFormat: "2006-01-02T15:04:05.000000000",
	// })
	logrus.SetFormatter(&logrus.JSONFormatter{
		TimestampFormat: "2006-01-02T15:04:05.000000000",
	})
	logFile, err := os.OpenFile("/tmp/tide/rsep.log", os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0666)
	if err != nil {
		logrus.Fatal("open log file failed, ", err)
	}
	logrus.SetOutput(logFile)
}

func main() {
	flag.Parse()

	dev.RSEPTest = true
	dev.EnableConcurrencyControl = *enableConcurrencyControl
	dev.EnableCpuset = *enableCpusets
	dev.EnableContainerPerRoutine = true // 恒为true，因为executor不支持并发执行routine

	logrus.Info("RESP Test:  ", dev.RSEPTest)
	logrus.Info("EnableConcurrencyControl:  ", dev.EnableConcurrencyControl)
	logrus.Info("EnableCpuset:  ", dev.EnableCpuset)
	logrus.Info("EnableContainerPerRoutine:  ", dev.EnableContainerPerRoutine)

	cpuNum := runtime.NumCPU()
	runtime.GOMAXPROCS(cpuNum)

	resCpuNum := 6

	// build self infomation
	selfId := stringx.GenerateId()
	selfRes := &routine.Resource{CpuNum: resCpuNum, GpuNum: 4}
	selfRole := clsstat.EDGE

	logrus.Info("GOMAXPROCS: ", runtime.GOMAXPROCS(0))
	logrus.Info("SelfID:", selfId)
	logrus.Info("SelfCpuNum:", cpuNum)
	logrus.Info("SelfRole:", selfRole)
	clsstat.Init(selfId, "", selfRole, selfRes)
	containerx.InitCgroup()
	containerx.InitCombCgroup()
	time.Sleep(2 * time.Second)

	procM := procmgr.NewProcMgr(selfRes)
	stubM := stubmgr.NewStubMgr(procM)

	runExp(stubM, procM, *fps, *duration)

	// graceful exit
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
