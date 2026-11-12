//go:build integration

package executor

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/containerd/containerd"
	"github.com/containerd/containerd/namespaces"
	"github.com/ict/tide/pkg/clsstat"
	containerx "github.com/ict/tide/pkg/containerxv2"
	"github.com/ict/tide/pkg/dev"
	"github.com/ict/tide/pkg/interfaces"
	"github.com/ict/tide/pkg/processor"
	"github.com/ict/tide/pkg/routine"
)

func TestExecutorRebindsAcrossRequests(t *testing.T) {
	if os.Getenv("TIDE_BINDING_INTEGRATION") != "1" || os.Geteuid() != 0 {
		t.Skip("requires explicit TIDE_BINDING_INTEGRATION=1 on the isolated Linux host")
	}
	t.Setenv("TIDE_COMB_DEV_CPUNUM", "16")
	t.Setenv("TIDE_COMB_CPUIDS", "2,4,3,5,6,7")
	t.Setenv("TIDE_MQ_MAX_MESSAGES", "10")
	t.Setenv("TIDE_MQ_MESSAGE_BYTES", "262144")
	sysctl := "/proc/sys/fs/mqueue/msgsize_max"
	original, err := os.ReadFile(sysctl)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(sysctl, []byte("262144\n"), 0644); err != nil {
		t.Fatal(err)
	}
	defer os.WriteFile(sysctl, original, 0644)
	dev.RSEPTest, dev.EnableCpuset, dev.EnableConcurrencyControl = true, true, true
	dev.EnableContainerPerRoutine = true
	clsstat.Init("binding-regression", "", clsstat.EDGE, &routine.Resource{CpuNum: 6})
	containerx.InitCgroup()
	containerx.InitCombCgroup()
	defer containerx.DeleteAllCgroup()
	defer containerx.DeleteAllCombCgroup()
	exec, err := New("docker.io/tide/fig5-piei-20261010", "", []string{"env", "TIDE_DETECTIONS_PER_REQ=10", "python3", "/app/paix_worker.py"}, interfaces.StubFn)
	if err != nil {
		t.Fatal(err)
	}
	defer exec.Close()
	wait := func(kind interfaces.EventType) {
		t.Helper()
		select {
		case event := <-exec.GetEventChan():
			if event == nil || event.Type != kind {
				t.Fatalf("event: %+v; want %v", event, kind)
			}
		case <-time.After(20 * time.Second):
			t.Fatal("executor event timed out")
		}
	}
	wait(interfaces.INIT)
	client, err := containerd.New("/run/containerd/containerd.sock")
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	ctx := namespaces.WithNamespace(context.Background(), "tide")
	ctn, err := client.LoadContainer(ctx, exec.Id())
	if err != nil {
		t.Fatal(err)
	}
	task, err := ctn.Task(ctx, nil)
	if err != nil {
		t.Fatal(err)
	}
	pid := task.Pid()
	image, err := os.ReadFile("../../cmd/resptestv2/dog.jpg")
	if err != nil {
		t.Fatal(err)
	}
	logical := [][]int{{0, 2}, {1, 3}, {4, 5}, {0, 2}, {4, 5}, {1, 3}}
	physical := [][]int{{2, 3}, {4, 5}, {6, 7}, {2, 3}, {6, 7}, {4, 5}}
	for i, pair := range logical {
		resource := &processor.PhysicResource{CpuSet: make([]int32, 6)}
		for _, c := range pair {
			resource.CpuSet[c] = 1
		}
		proc := processor.NewProc(fmt.Sprintf("rebind-%d", i), &routine.Resource{CpuNum: 2}, resource, false)
		rid := fmt.Sprintf("binding-%d", i)
		if err := exec.BindProc(proc); err != nil {
			t.Fatal(err)
		}
		if err := exec.BindRtn(routine.NewSubmittedRtn(rid, "detect", image, proc.LogicRes)); err != nil {
			t.Fatal(err)
		}
		if err := exec.Execute(); err != nil {
			t.Fatal(err)
		}
		wait(interfaces.EXIT)
		var data []byte
		for deadline := time.Now().Add(2 * time.Second); ; {
			data, err = os.ReadFile(filepath.Join("/tmp/tide/exec-logs", exec.Id(), "stdout"))
			if err != nil {
				t.Fatal(err)
			}
			available := false
			for _, line := range strings.Split(string(data), "\n") {
				var event struct {
					RequestID string `json:"request_id"`
				}
				if json.Unmarshal([]byte(line), &event) == nil && event.RequestID == rid {
					available = true
				}
			}
			if available || time.Now().After(deadline) {
				break
			}
			time.Sleep(10 * time.Millisecond)
		}
		found := false
		for _, line := range strings.Split(string(data), "\n") {
			var e struct {
				RequestID string `json:"request_id"`
				Affinity  []int  `json:"affinity"`
				Success   bool   `json:"success"`
				Count     int    `json:"inferences_completed"`
			}
			if json.Unmarshal([]byte(line), &e) == nil && e.RequestID == rid {
				found = true
				if !e.Success || e.Count != 10 || !reflect.DeepEqual(e.Affinity, physical[i]) {
					t.Fatalf("request %s on same Executor %s: observed CPUs %v, want %v; success=%v count=%d", rid, exec.Id(), e.Affinity, physical[i], e.Success, e.Count)
				}
			}
		}
		if !found {
			t.Fatalf("no application evidence for %s", rid)
		}
		threads, err := filepath.Glob(fmt.Sprintf("/proc/%d/task/*/status", pid))
		if err != nil || len(threads) < 2 {
			t.Fatalf("inference threads missing: %v %v", threads, err)
		}
		want := fmt.Sprintf("%d-%d", physical[i][0], physical[i][1])
		for _, path := range threads {
			status, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			found := false
			for _, line := range strings.Split(string(status), "\n") {
				if strings.HasPrefix(line, "Cpus_allowed_list:") {
					found = true
					if strings.TrimSpace(strings.TrimPrefix(line, "Cpus_allowed_list:")) != want {
						t.Fatalf("thread %s: %s; want %s", path, line, want)
					}
				}
			}
			if !found {
				t.Fatalf("thread affinity missing: %s", path)
			}
		}
		t.Logf("same_executor=%s request=%s pid=%d CPU=%v threads=%d", exec.Id(), rid, pid, physical[i], len(threads))
		if _, err := exec.UnbindRtn(); err != nil {
			t.Fatal(err)
		}
		if got, err := exec.UnbindProc(); err != nil || got != proc {
			t.Fatalf("processor return: %v %v", got, err)
		}
	}
}
