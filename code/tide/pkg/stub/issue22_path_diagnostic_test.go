package stub

import (
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"testing"
	"time"

	"github.com/ict/tide/pkg/clsstat"
	"github.com/ict/tide/pkg/cmnct"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/event"
	"github.com/ict/tide/pkg/processor"
	"github.com/ict/tide/pkg/revisionworkload"
	"github.com/ict/tide/pkg/routine"
	"github.com/sirupsen/logrus"
)

func TestIssue22ApplicationBudgetMatchesPod(t *testing.T) {
	raw := os.Getenv("TMC_ISSUE22_TOTAL_CPU")
	if raw == "" {
		t.Skip("isolated diagnostic process only")
	}
	total, err := strconv.Atoi(raw)
	if err != nil || total < 2 {
		t.Fatal("invalid total CPU")
	}
	logrus.SetLevel(logrus.ErrorLevel)
	if err := dev.SetConfig(issue5BaseConfig(2000, 25)); err != nil {
		t.Fatal(err)
	}
	clsstat.Init("budget-node", "budget-local", clsstat.EDGE, &routine.Resource{CpuNum: total})
	clsstat.Singleton().AddApp(&clsstat.AddAppOpts{AppId: issue5App})
	cmnct.Init("budget-node", "budget-local", event.NewEventDispatcher())
	st, pm := issue5NewStub(t, total, revisionworkload.Config{Kind: "cpu", Bytes: 64, Iterations: 1})
	defer pm.Close()
	defer st.Close()
	first := st.acquireProcessor(routine.NewSubmittedRtn("budget-first", "cpu", nil, dev.GetReqResource()))
	if first == nil {
		t.Fatal("first processor missing")
	}
	got := make(chan *processor.Processor, 1)
	go func() {
		got <- st.acquireProcessor(routine.NewSubmittedRtn("budget-second", "cpu", nil, dev.GetReqResource()))
	}()
	var second *processor.Processor
	select {
	case second = <-got:
	case <-time.After(500 * time.Millisecond):
	}
	held := first.LogicRes.CpuNum
	witness := map[string]any{"configured_total_cpu": total, "application_cpu_limit": 2,
		"request_cpu": dev.GetReqResource().CpuNum, "first_processor_id": first.Id,
		"first_is_bound": first == st.bindProc, "second_obtained_while_first_held": second != nil}
	if second != nil {
		held += second.LogicRes.CpuNum
		witness["second_processor_id"] = second.Id
		witness["second_is_bound"] = second == st.bindProc
	}
	witness["concurrent_executable_cpu"] = held
	body, _ := json.Marshal(witness)
	fmt.Println(string(body))
	st.returnProc(first)
	if second == nil {
		select {
		case second = <-got:
		case <-time.After(time.Second):
			t.Fatal("queued request did not acquire released processor")
		}
	}
	if second == nil {
		t.Fatal("second processor missing")
	}
	st.returnProc(second)
	if held > 2 {
		t.Fatalf("fixture exposes %d executable CPU to a 2-CPU application", held)
	}
}
