package main

import (
	"embed"
	"strings"
	"sync"
	"time"

	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/procmgr"
	"github.com/ict/tide/pkg/routine"
	"github.com/ict/tide/pkg/stringx"
	"github.com/ict/tide/pkg/stubmgr"
	"github.com/sirupsen/logrus"
)

//go:embed dog_serialized.byte
var f embed.FS

func runExp(stubM *stubmgr.StubManager, procM *procmgr.ProcessorManager, fps int, duration time.Duration) {
	imgName := "registry.example.org:5000/team/tide/yolox"
	commands := strings.Split("python3 -u /app/src/yolox_concurrent_tide.py --fps -1 --total -1", " ")
	resource := dev.GetReqResource()

	var initExecNo int
	if dev.EnableConcurrencyControl {
		initExecNo = 3
	} else {
		initExecNo = 6
	}

	createStubOpts := &stubmgr.CreateStubOpts{
		AppId:      stringx.GenerateId(),
		ImgName:    imgName,
		Commands:   commands,
		Mode:       interfaces.StubFn,
		ResReq:     resource,
		InitExecNo: initExecNo,
	}
	stub, err := stubM.CreateStub(createStubOpts)
	if err != nil {
		logrus.Fatal("create stub failed, ", err)
	}
	logrus.WithField("StubId", stub.Id()).Info("create stub successfully")

	time.Sleep(5 * time.Second)

	submitRoutine(stub, fps, duration)
}

func submitRoutine(stub interfaces.Stub, fps int, duration time.Duration) {
	dogBuffer, err := f.ReadFile("dog_serialized.byte")
	if err != nil {
		logrus.Fatal("read args buffer file failed, ", err)
	}

	interval := time.Second.Nanoseconds() / int64(fps)
	iterNum := int(duration.Nanoseconds() / interval)
	logrus.WithFields(logrus.Fields{
		"Interval": float64(interval) / 1000000.0,
		"IterNum":  iterNum,
	}).Info("start exp")

	var wg sync.WaitGroup
	wg.Add(iterNum)

	ticker := time.NewTicker(time.Duration(interval))
	for i := 0; i < iterNum; i++ {
		<-ticker.C
		go func() {
			defer wg.Done()
			rtn := routine.NewSubmittedRtn(stringx.GenerateId(), "proce_img", dogBuffer, dev.GetReqResource())
			stub.SubmitRoutineExp(rtn)
		}()
	}

	wg.Wait()
	ticker.Stop()
}
