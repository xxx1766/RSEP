package stub

import (
	"context"
	"os"
	"os/exec"
	"testing"
	"time"

	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/cmnct"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/event"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/routine"
	"github.com/ict/tide/pkg/server/handlers/stubhdlr"
	"github.com/ict/tide/proto/eventpb"
	"google.golang.org/protobuf/proto"
)

func TestSubmitExternalRecordsOriginAndRequestsRemoteData(t *testing.T) {
	if os.Getenv("TIDE_EXTERNAL_SUBMIT_CHILD") != "1" {
		cmd := exec.Command(os.Args[0], "-test.run=^TestSubmitExternalRecordsOriginAndRequestsRemoteData$", "-test.v")
		cmd.Env = append(os.Environ(), "TIDE_EXTERNAL_SUBMIT_CHILD=1")
		if output, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("isolated native ingress test: %v\n%s", err, output)
		}
		return
	}
	s, _ := newTTLStub(t, time.Hour)
	cfg := dev.CurrentConfig()
	cfg.QoSMS = 1
	cfg.ExecutionMS = 1000
	if err := dev.SetConfig(cfg); err != nil {
		t.Fatal(err)
	}
	const cloud = "external-test-cloud"
	clsstat.Singleton().AddApp(&clsstat.AddAppOpts{AppId: s.appId})
	clsstat.Singleton().AddStub(&clsstat.AddStubOpts{StubId: s.id, AppId: s.appId, NodeId: "ttl-node", Mode: interfaces.StubFn, CreateTime: time.Now()})
	clsstat.Singleton().AddNode(&clsstat.AddNodeOpts{Nid: cloud, Address: "ttl-local", Role: clsstat.CLOUD, ResTotal: &routine.Resource{CpuNum: 4}})
	dispatch := event.NewEventDispatcher()
	cmnct.Init("ttl-node", "ttl-local", dispatch)
	stubhdlr.RegisterEventHandler(dispatch, s)
	r := routine.NewSubmittedRtn("external-cloud-result", "cpu", nil, dev.GetReqResource())
	r.SubmitTime = time.Now().UnixNano()
	problems := make(chan string, 4)
	waitSent := make(chan struct{})
	submitSeen := make(chan struct{})
	dispatch.RegisterHandler(interfaces.CldRtnSbmEvt(s.appId), func(evt *event.Event) {
		defer close(submitSeen)
		var submit eventpb.RoutineSubmitEvent
		if err := proto.Unmarshal(evt.Content, &submit); err != nil {
			problems <- err.Error()
			return
		}
		if !s.rtnStore.Exist(r.Id) || s.rtnStore.Get(r.Id) != r || r.InitSid != s.id {
			problems <- "origin routine was not recorded before native offload"
		}
	})
	dispatch.RegisterHandler(interfaces.CldRtnWaitEvt(s.appId), func(evt *event.Event) {
		<-submitSeen
		var wait eventpb.RoutineWaitGetEvent
		if err := proto.Unmarshal(evt.Content, &wait); err != nil {
			problems <- err.Error()
			return
		}
		if wait.RtnId != r.Id || wait.ReqSid != s.id || !wait.WithData {
			problems <- "external ingress did not request native result data"
		}
		s.NoticeRtnData(r.Id, routine.EXIT_SUCC, []byte("actual-remote-envelope"), "")
		close(waitSent)
	})
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if err := SubmitExternal(ctx, s, r); err != nil {
		t.Fatal(err)
	}
	select {
	case <-waitSent:
	case <-ctx.Done():
		t.Fatal("native remote with-data wait was never sent")
	}
	select {
	case <-r.WaitData():
	case <-ctx.Done():
		t.Fatal("remote data did not unblock external caller")
	}
	if r.Status() != routine.EXIT_SUCC || string(r.Result()) != "actual-remote-envelope" {
		t.Fatalf("remote result lost: status=%v result=%q", r.Status(), r.Result())
	}
	select {
	case problem := <-problems:
		t.Fatal(problem)
	default:
	}
}

func TestSubmitExternalCancellationDoesNotWaitForReceiver(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	r := routine.NewSubmittedRtn("never-received", "cpu", nil, dev.GetReqResource())
	done := make(chan struct{})
	go func() {
		waitExternalReceive(ctx, nil, r)
		close(done)
	}()
	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("external receiver waiter leaked after cancellation")
	}
	s := &stub{}
	if err := SubmitExternal(ctx, s, r); err != context.Canceled {
		t.Fatalf("canceled submission returned %v", err)
	}
	if err := SubmitExternal(context.Background(), nil, r); err == nil {
		t.Fatal("non-native stub accepted")
	}
}

func TestExternalLocalNoticeDoesNotSetDataTwice(t *testing.T) {
	s, _ := newTTLStub(t, time.Hour)
	r := routine.NewSubmittedRtn(t.Name(), "cpu", nil, dev.GetReqResource())
	r.SetReceiver(false, s.id)
	s.rtnStore.Record(r)
	r.Succ()
	r.SetResult([]byte("local-result"))
	waitExternalReceive(context.Background(), s, r)
	s.NoticeRtnData(r.Id, routine.EXIT_SUCC, []byte("local-result"), "")
	if string(r.Result()) != "local-result" {
		t.Fatal("native self-wait changed the completed result")
	}
}
