package main

import (
	"embed"
	"encoding/csv"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/procmgr"
	"github.com/ict/tide/pkg/routine"
	"github.com/ict/tide/pkg/stringx"
	"github.com/ict/tide/pkg/stubmgr"
	"github.com/sirupsen/logrus"
)

//go:embed dog.jpg
var f embed.FS

const defaultImage = "docker.io/tide/workload"

type record struct {
	reqID  uint64
	tSub   int64
	tRep   int64
	status int
}

func runExp(stubM *stubmgr.StubManager, procM *procmgr.ProcessorManager,
	fps int, duration time.Duration, outfile string, deadline time.Duration) {

	imgName := defaultImage
	commands := strings.Split("python3 -u /app/entrypoint.py --mode=paix", " ")

	resReq := dev.GetReqResource()
	initExecNo := 6
	if dev.EnableConcurrencyControl {
		initExecNo = 3
	}

	appID := stringx.GenerateId()
	clsstat.Singleton().AddApp(&clsstat.AddAppOpts{AppId: appID, ImageName: imgName, Commands: commands})
	defer clsstat.Singleton().RmApp(appID)
	logrus.WithField("AppId", appID).Info("ablation application registered")
	createStubOpts := &stubmgr.CreateStubOpts{
		AppId:      appID,
		ImgName:    imgName,
		Commands:   commands,
		Mode:       interfaces.StubFn,
		ResReq:     resReq,
		InitExecNo: initExecNo,
	}
	stub, err := stubM.CreateStub(createStubOpts)
	if err != nil {
		logrus.Fatal("create stub failed, ", err)
	}
	logrus.WithField("StubId", stub.Id()).Info("stub created")

	if *warmup {
		submitRoutine(stub, initExecNo*4, time.Second, filepath.Join(filepath.Dir(outfile), "warmup.csv"), deadline, "warmup")
	}
	submitRoutine(stub, fps, duration, outfile, deadline, "measured")
}

func submitRoutine(stub interfaces.Stub, fps int, duration time.Duration,
	outfile string, deadline time.Duration, phase string) {

	dogBuffer, err := f.ReadFile("dog.jpg")
	if err != nil {
		logrus.Fatal("read dog.jpg failed, ", err)
	}

	if err := os.MkdirAll(filepath.Dir(outfile), 0o755); err != nil {
		logrus.Fatal("mkdir outfile dir failed, ", err)
	}
	fh, err := os.Create(outfile)
	if err != nil {
		logrus.Fatal("create outfile failed, ", err)
	}
	defer fh.Close()
	csvw := csv.NewWriter(fh)
	defer csvw.Flush()
	_ = csvw.Write([]string{"req_id", "t_submit_ns", "t_reply_ns", "latency_ms", "status"})

	interval := time.Second.Nanoseconds() / int64(fps)
	iterNum := int(duration.Nanoseconds() / interval)
	logrus.WithFields(logrus.Fields{
		"Interval": float64(interval) / 1e6,
		"IterNum":  iterNum,
		"Deadline": deadline,
		"phase":    phase,
	}).Info("start exp")

	records := make(chan record, iterNum*2)
	var wg sync.WaitGroup
	var counter uint64
	t0 := time.Now()

	ticker := time.NewTicker(time.Duration(interval))
	for i := 0; i < iterNum; i++ {
		<-ticker.C
		wg.Add(1)
		go func() {
			defer wg.Done()
			id := atomic.AddUint64(&counter, 1)
			rtn := routine.NewSubmittedRtn(
				fmt.Sprintf("%s-%d", phase, id),
				"proce_img", dogBuffer, dev.GetReqResource(),
			)
			tSub := time.Since(t0).Nanoseconds()
			stub.SubmitRoutineExp(rtn)
			status := 1
			select {
			case <-rtn.WaitDone():
				if rtn.Status() == routine.EXIT_SUCC {
					status = 1
				} else {
					status = 2
				}
			case <-time.After(deadline):
				status = 0
			}
			tRep := time.Since(t0).Nanoseconds()
			records <- record{id, tSub, tRep, status}
		}()
	}
	ticker.Stop()
	wg.Wait()
	close(records)

	var okN, totalN int
	for r := range records {
		csvStatus := 200
		switch r.status {
		case 1:
			csvStatus = 200
			okN++
		case 2:
			csvStatus = 500
		case 0:
			csvStatus = 504
		}
		latencyMs := float64(r.tRep-r.tSub) / 1e6
		_ = csvw.Write([]string{
			strconv.FormatUint(r.reqID, 10),
			strconv.FormatInt(r.tSub, 10),
			strconv.FormatInt(r.tRep, 10),
			strconv.FormatFloat(latencyMs, 'f', 3, 64),
			strconv.Itoa(csvStatus),
		})
		totalN++
	}
	logrus.WithFields(logrus.Fields{"total": totalN, "ok": okN}).Info("exp complete")
}
