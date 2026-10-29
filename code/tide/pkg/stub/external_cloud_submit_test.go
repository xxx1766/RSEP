package stub

import (
	"context"
	"net"
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
	"github.com/ict/tide/proto/eventpb"
	"github.com/pkg/errors"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"
)

func TestSubmitExternalCloudUsesNativeEnvelopeAndWait(t *testing.T) {
	if os.Getenv("TIDE_EXTERNAL_CLOUD_CHILD") != "1" {
		cmd := exec.Command(os.Args[0], "-test.run=^TestSubmitExternalCloudUsesNativeEnvelopeAndWait$", "-test.v")
		cmd.Env = append(os.Environ(), "TIDE_EXTERNAL_CLOUD_CHILD=1")
		if out, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("isolated cloud ingress: %v\n%s", err, out)
		}
		return
	}
	s, _ := newTTLStub(t, time.Hour)
	cfg := dev.CurrentConfig()
	cfg.C4Functions = map[string]bool{"cpu": true}
	if err := dev.SetConfig(cfg); err != nil {
		t.Fatal(err)
	}
	const cloud = "external-cloud-only"
	clsstat.Singleton().AddNode(&clsstat.AddNodeOpts{Nid: cloud, Address: "cloud-test-local", Role: clsstat.CLOUD, ResTotal: &routine.Resource{CpuNum: 2}})
	d := event.NewEventDispatcher()
	cmnct.Init("ttl-node", "cloud-test-local", d)
	r := routine.NewSubmittedRtn("cloud-native-envelope", "cpu", []byte("kernel"), dev.GetReqResource())
	r.SubmitTime = time.Now().UnixNano()
	submitted := make(chan eventpb.RoutineSubmitEvent, 1)
	waited := make(chan eventpb.RoutineWaitGetEvent, 1)
	d.RegisterHandler(interfaces.CldRtnSbmEvt(s.appId), func(e *event.Event) {
		var p eventpb.RoutineSubmitEvent
		_ = proto.Unmarshal(e.Content, &p)
		submitted <- p
	})
	d.RegisterHandler(interfaces.CldRtnWaitEvt(s.appId), func(e *event.Event) {
		var p eventpb.RoutineWaitGetEvent
		_ = proto.Unmarshal(e.Content, &p)
		waited <- p
		s.NoticeRtnData(r.Id, routine.EXIT_SUCC, []byte("real-envelope"), "")
	})
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	if err := SubmitExternalCloud(ctx, s, r, cloud); err != nil {
		t.Fatal(err)
	}
	select {
	case p := <-submitted:
		if p.RtnId != r.Id || !p.RequireLocalCapacity || p.LocalNodeId != "ttl-node" {
			t.Fatalf("lost metadata: %+v", p)
		}
	case <-ctx.Done():
		t.Fatal("cloud submit absent")
	}
	if !s.rtnStore.Exist(r.Id) || s.rtnStore.Get(r.Id) != r || r.InitSid != s.id {
		t.Fatal("origin not recorded")
	}
	select {
	case p := <-waited:
		if p.RtnId != r.Id || p.ReqSid != s.id || !p.WithData {
			t.Fatalf("invalid native wait: %+v", p)
		}
	case <-ctx.Done():
		t.Fatal("cloud wait absent")
	}
	select {
	case <-r.WaitData():
	case <-ctx.Done():
		t.Fatal("result absent")
	}
	if r.Status() != routine.EXIT_SUCC || string(r.Result()) != "real-envelope" {
		t.Fatal("result changed")
	}
}

func TestSubmitExternalCloudRejectsInvalidDestination(t *testing.T) {
	s, _ := newTTLStub(t, time.Hour)
	r := routine.NewSubmittedRtn(t.Name(), "cpu", nil, dev.GetReqResource())
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := SubmitExternalCloud(ctx, s, r, "missing"); err != context.Canceled {
		t.Fatalf("cancel: %v", err)
	}
	if err := SubmitExternalCloud(context.Background(), nil, r, "missing"); err == nil {
		t.Fatal("invalid stub accepted")
	}
	if err := SubmitExternalCloud(context.Background(), s, r, "missing"); err == nil {
		t.Fatal("unknown cloud accepted")
	}
	if s.rtnStore.Exist(r.Id) {
		t.Fatal("invalid submission recorded")
	}
}

type refusingCloud struct {
	eventpb.UnimplementedEventServiceServer
	events chan string
}

func (s *refusingCloud) SendEvent(_ context.Context, in *eventpb.EventList) (*eventpb.EventResult, error) {
	for _, e := range in.Events {
		s.events <- e.Type
	}
	return nil, status.Error(codes.Unavailable, "controlled native endpoint unavailable")
}

func TestSubmitExternalCloudUnavailableDoesNotClaimReceiverOrRetry(t *testing.T) {
	if os.Getenv("TIDE_CLOUD_REFUSAL_CHILD") != "1" {
		cmd := exec.Command(os.Args[0], "-test.run=^TestSubmitExternalCloudUnavailableDoesNotClaimReceiverOrRetry$", "-test.v")
		cmd.Env = append(os.Environ(), "TIDE_CLOUD_REFUSAL_CHILD=1")
		if out, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("isolated refusal: %v\n%s", err, out)
		}
		return
	}
	s, _ := newTTLStub(t, time.Hour)
	lis, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer lis.Close()
	endpoint := &refusingCloud{events: make(chan string, 4)}
	gs := grpc.NewServer()
	eventpb.RegisterEventServiceServer(gs, endpoint)
	go gs.Serve(lis)
	defer gs.Stop()
	clsstat.Singleton().AddNode(&clsstat.AddNodeOpts{Nid: "refusing-cloud", Address: lis.Addr().String(), Role: clsstat.CLOUD, ResTotal: &routine.Resource{CpuNum: 2}})
	r := routine.NewSubmittedRtn(t.Name(), "cpu", nil, dev.GetReqResource())
	if err := SubmitExternalCloud(context.Background(), s, r, "refusing-cloud"); status.Code(errors.Cause(err)) != codes.Unavailable {
		t.Fatalf("transport error lost: %v", err)
	}
	if _, id := r.Receiver(); id != "" {
		t.Fatal("rejected send claimed a receiver")
	}
	select {
	case kind := <-endpoint.events:
		if kind != interfaces.CldRtnSbmEvt(s.appId) {
			t.Fatal(kind)
		}
	case <-time.After(time.Second):
		t.Fatal("no real RPC")
	}
	select {
	case kind := <-endpoint.events:
		t.Fatalf("unexpected retry/wait %q", kind)
	case <-time.After(30 * time.Millisecond):
	}
}
