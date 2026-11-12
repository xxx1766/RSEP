package stub

import (
	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/cmnct"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/event"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/revisionworkload"
	"github.com/ict/tide/pkg/routine"
	"github.com/ict/tide/pkg/server"
	"github.com/ict/tide/pkg/server/handlers/clsstathdlr"
	"github.com/ict/tide/pkg/server/handlers/stubhdlr"
	"github.com/ict/tide/proto/eventpb"
	"github.com/sirupsen/logrus"
	"google.golang.org/grpc"
	"net"
	"os"
	"testing"
)

func TestIssue20DomainOriginProcess(t *testing.T) {
	path := os.Getenv("TMC_ISSUE5_ORIGIN_CONFIG")
	if path == "" {
		t.Skip("helper process")
	}
	logrus.SetLevel(logrus.ErrorLevel)
	if os.Getenv("TMC_ISSUE12_DEBUG") == "1" {
		logrus.SetLevel(logrus.DebugLevel)
	}
	var c issue5OriginConfig
	issue5ReadJSON(t, path, &c)
	if err := issue12LoadImagePayloads(); err != nil {
		t.Fatal(err)
	}
	if len(c.Candidates) != 3 || c.QoSMS < 1 || c.ExecutionMS < 1 {
		t.Fatal("domain origin requires three candidates and positive timing values")
	}
	cfg, err := issue5OriginScheduleConfig(c)
	if err != nil {
		t.Fatal(err)
	}
	if err := dev.SetConfig(cfg); err != nil {
		t.Fatal(err)
	}
	lis, err := net.Listen("tcp", c.Listen)
	if err != nil {
		t.Fatal(err)
	}
	defer lis.Close()
	cs := clsstat.Singleton()
	clsstat.Init(issue5OriginID, issue5OriginAddress(c), clsstat.THINGS, &routine.Resource{CpuNum: 2})
	cs.AddApp(&clsstat.AddAppOpts{AppId: issue5App})
	d := event.NewEventDispatcher()
	cmnct.Init(issue5OriginID, issue5OriginAddress(c), d)
	origin, pm := issue5NewStub(t, 2, c.Workload)
	origin.id = issue5OriginStubID
	defer origin.Close()
	defer pm.Close()
	stubhdlr.RegisterEventHandler(d, origin)
	clsstathdlr.RegisterEventHandler(d)
	svr := grpc.NewServer()
	eventpb.RegisterEventServiceServer(svr, server.NewEventServer(d))
	go func() { _ = svr.Serve(lis) }()
	defer svr.Stop()
	for _, worker := range c.Candidates {
		cs.AddNode(&clsstat.AddNodeOpts{Nid: worker.NodeID, Address: worker.Address, Role: clsstat.EDGE, ResTotal: &routine.Resource{CpuNum: worker.TotalCPU}})
		cs.UpdateNode(&clsstat.UpdateNodeOpts{Nid: worker.NodeID, ResRemain: &routine.Resource{CpuNum: worker.TotalCPU - 2}})
		cs.AddStub(&clsstat.AddStubOpts{StubId: issue5StubID(worker.NodeID), AppId: issue5App, NodeId: worker.NodeID, Mode: interfaces.StubFn})
	}
	want, err := revisionworkload.Run(c.Workload)
	if err != nil {
		t.Fatal(err)
	}
	if len(c.Runs) > 0 {
		issue5RunMatrix(t, origin, c, cfg, want.Checksum)
		return
	}
	t.Fatal("domain experiment requires explicit frozen run specs")
}
