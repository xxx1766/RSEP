package main

import (
	"flag"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/ict/tide/pkg/procmgr"
	"github.com/ict/tide/pkg/routine"
	"github.com/ict/tide/pkg/stubmgr"
	"github.com/sirupsen/logrus"
)

var addr string

func main() {
	// logrus.SetReportCaller(true)
	// logrus.SetFormatter(&logrus.TextFormatter{
	// 	FullTimestamp:   true,
	// 	TimestampFormat: "2006-01-02T15:04:05.000000000",
	// })
	logrus.SetFormatter(&logrus.JSONFormatter{
		// FullTimestamp:   true,
		TimestampFormat: "2006-01-02T15:04:05.000000000",
	})
	logrus.SetLevel(logrus.DebugLevel)
	logFile, err := os.OpenFile("logs/tide.log", os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0666)
	if err != nil {
		logrus.Fatal("open log file failed, ", err)
	}
	logrus.SetOutput(logFile)

	flag.StringVar(&addr, "addr", "0.0.0.0:10001", "http service address")
	flag.Parse()

	pm := procmgr.NewProcMgr(16)
	sm := stubmgr.NewStubMgr(pm)

	reqRes := &routine.Resource{
		CpuNum: 1,
	}

	opts := &stubmgr.CreateStubOpts{
		ImgName:    "registry.example.org:5000/team/tide-go:latest",
		ResReq:     reqRes,
		InitExecNo: 1,
	}
	st, err := sm.CreateStub(opts)
	if err != nil {
		logrus.Error(err)
		return
	}

	// graceful exit
	c := make(chan os.Signal, 1)
	signal.Notify(c, syscall.SIGTERM, os.Interrupt)
	go func() {
		sig := <-c
		logrus.Infof("Got %s signal. Aborting...", sig)
		st.Close()
		os.Exit(1)
	}()

	http.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		id := r.URL.Query().Get("id")
		logrus.WithField("RoutineId", id).Debug("stub cmd: receive a routine")
		rtn := &routine.Routine{
			Id: id,
			ResReq: &routine.Resource{
				CpuNum: 1,
			},
			Status: routine.PENDING,
			FnName: "numrec",
		}
		st.SubmitRoutine(rtn)
	})

	logrus.Infof("server listen at: %s, %d", addr, time.Now().UnixNano())
	http.ListenAndServe(addr, nil)
}
