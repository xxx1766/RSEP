package stub

import (
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/routine"
)

func TestIssue12NoDestinationEndsWaiters(t *testing.T) {
	s, _ := newTTLStub(t, time.Hour)
	clsstat.Singleton().AddApp(&clsstat.AddAppOpts{AppId: "ttl-app"})
	r := routine.NewSubmittedRtn(t.Name(), "cpu", nil, &routine.Resource{CpuNum: 2})
	r.InitSid = s.id
	r.SubmitTime = time.Now().Add(-time.Second).UnixNano()
	s.rtnStore.Record(r)
	s.SubmitRoutine(r)

	select {
	case <-r.WaitReceive():
	case <-time.After(500 * time.Millisecond):
		t.Fatal("request without destination waited for a receipt timeout")
	}
	select {
	case <-r.WaitData():
	case <-time.After(500 * time.Millisecond):
		t.Fatal("request without destination has no terminal result")
	}
	if r.Status() != routine.EXIT_FAIL || !strings.Contains(r.Error(), "no feasible execution destination") {
		t.Fatalf("unexpected terminal outcome: status=%v error=%q", r.Status(), r.Error())
	}
	if got := atomic.LoadUint64(&s.noSubmitted); got != 0 {
		t.Fatalf("unroutable request was admitted: %d", got)
	}
}
