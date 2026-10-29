package stub

import (
	"context"
	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/routine"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestC4AdmissionRejectsWithoutQueueingAndUnblocksWaiters(t *testing.T) {
	for _, cloud := range []bool{false, true} {
		t.Run(map[bool]string{false: "local", true: "cloud"}[cloud], func(t *testing.T) {
			s, _ := newTTLStub(t, time.Hour)
			r := routine.NewSubmittedRtn(t.Name(), "cpu", nil, &routine.Resource{CpuNum: 3})
			r.RequireLocalCapacity = true
			r.InitSid = s.id
			r.SubmitTime = time.Now().UnixNano()
			if cloud {
				r.LocalNodeID = "ttl-node"
				s.CloudSubmitRoutine(r)
			} else {
				s.SubmitRoutine(r)
			}
			if atomic.LoadUint64(&s.noSubmitted) != 0 || atomic.LoadInt64(&s.noExecuting) != 0 {
				t.Fatal("C4 rejected request was admitted")
			}
			if r.Status() != routine.EXIT_FAIL || !strings.Contains(r.Error(), "C4:") {
				t.Fatalf("missing C4 failure: %v %s", r.Status(), r.Error())
			}
			ctx, cancel := context.WithTimeout(context.Background(), time.Second)
			defer cancel()
			if !s.WaitRoutineReach(ctx, r.Id) {
				t.Fatal("failure not retained")
			}
			select {
			case <-s.WaitRtnDone(r.Id):
			case <-ctx.Done():
				t.Fatal("failure waiter blocked")
			}
			if !cloud {
				select {
				case <-r.WaitReceive():
				case <-ctx.Done():
					t.Fatal("receive waiter blocked")
				}
			}
		})
	}
}
func TestC4ConfiguredFunctionRunsLocallyWhenCapable(t *testing.T) {
	s, e := newTTLStub(t, time.Hour)
	c := dev.CurrentConfig()
	c.C4Functions = map[string]bool{"critical": true}
	if err := dev.SetConfig(c); err != nil {
		t.Fatal(err)
	}
	r := routine.NewSubmittedRtn(t.Name(), "critical", nil, &routine.Resource{CpuNum: 2})
	r.InitSid = s.id
	r.SubmitTime = time.Now().UnixNano()
	s.SubmitRoutine(r)
	if !r.RequireLocalCapacity || r.LocalNodeID != clsstat.Singleton().GetSelf().Id {
		t.Fatal("source classification/anchor missing")
	}
	select {
	case <-e.started:
	case <-time.After(time.Second):
		t.Fatal("eligible critical task did not execute")
	}
	e.finish()
	select {
	case <-r.WaitData():
	case <-time.After(time.Second):
		t.Fatal("eligible task did not complete")
	}
	if r.Status() != routine.EXIT_SUCC {
		t.Fatal("eligible task failed")
	}
}
func TestC4TransitCannotReanchorAtReceiver(t *testing.T) {
	s, _ := newTTLStub(t, time.Hour)
	r := routine.NewSubmittedRtn(t.Name(), "cpu", nil, &routine.Resource{CpuNum: 2})
	r.InitSid = s.id
	r.RequireLocalCapacity = true
	r.LocalNodeID = "unknown-origin"
	r.SubmitTime = time.Now().UnixNano()
	s.SubmitRoutine(r)
	if r.LocalNodeID != "unknown-origin" || r.Status() != routine.EXIT_FAIL {
		t.Fatal("receiver replaced the invalid origin with its own capacity")
	}
}

func TestC4DoesNotSubstituteForDestinationCapacity(t *testing.T) {
	s, _ := newTTLStub(t, time.Hour)
	clsstat.Singleton().AddNode(&clsstat.AddNodeOpts{Nid: "capable-source", Address: "ttl-local", Role: clsstat.THINGS, ResTotal: &routine.Resource{CpuNum: 4}})
	r := routine.NewSubmittedRtn(t.Name(), "cpu", nil, &routine.Resource{CpuNum: 3})
	r.RequireLocalCapacity = true
	r.LocalNodeID = "capable-source"
	r.InitSid = s.id
	r.SubmitTime = time.Now().UnixNano()
	s.CloudSubmitRoutine(r)
	if atomic.LoadUint64(&s.noSubmitted) != 0 || r.Status() != routine.EXIT_FAIL {
		t.Fatal("cloud used a two-CPU stub for a three-CPU request despite retaining the wire requirement")
	}
}

func TestC4ExperimentalIngressInitializesOrigin(t *testing.T) {
	s, e := newTTLStub(t, time.Hour)
	c := dev.CurrentConfig()
	c.C4Functions = map[string]bool{"critical": true}
	if err := dev.SetConfig(c); err != nil {
		t.Fatal(err)
	}
	r := routine.NewSubmittedRtn(t.Name(), "critical", nil, &routine.Resource{CpuNum: 2})
	r.SubmitTime = time.Now().UnixNano()
	s.SubmitRoutineExp(r)
	select {
	case <-e.started:
	case <-time.After(time.Second):
		t.Fatal("experimental ingress lost source capability")
	}
	if r.InitSid != s.id || r.LocalNodeID != "ttl-node" || !r.RequireLocalCapacity {
		t.Fatal("experimental ingress missing source metadata")
	}
	e.finish()
}
