package main

import (
	"encoding/csv"
	"encoding/json"
	"flag"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestHTTPBudgetProcess(t *testing.T) {
	mode := os.Getenv("HTTP_BUDGET_TEST_MODE")
	if mode == "" {
		return
	}
	var args []string
	if err := json.Unmarshal([]byte(os.Getenv("HTTP_BUDGET_TEST_ARGS")), &args); err != nil {
		t.Fatal(err)
	}
	os.Args = append([]string{"httpbase"}, args...)
	flag.CommandLine = flag.NewFlagSet("httpbase", flag.ExitOnError)
	if mode == "parse" {
		parseFlags()
		return
	}
	main()
}

func budgetProcess(t *testing.T, mode string, args []string, env ...string) *exec.Cmd {
	t.Helper()
	encoded, err := json.Marshal(args)
	if err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(os.Args[0], "-test.run=^TestHTTPBudgetProcess$")
	cmd.Env = append(os.Environ(), "HTTP_BUDGET_TEST_MODE="+mode, "HTTP_BUDGET_TEST_ARGS="+string(encoded))
	cmd.Env = append(cmd.Env, env...)
	return cmd
}

func TestCPUBudgetBackendAndWarmup(t *testing.T) {
	for _, cpus := range []int{6, 8, 12} {
		for _, cc := range []string{"off", "on"} {
			t.Run(fmt.Sprintf("cpu%d_cc%s", cpus, cc), func(t *testing.T) {
				checkBudgetRun(t, []string{fmt.Sprintf("-cpu-num=%d", cpus), "-cc=" + cc}, cpus, cc)
			})
		}
	}
}

func TestDefaultSixCPUBudget(t *testing.T) {
	for _, cc := range []string{"off", "on"} {
		t.Run(cc, func(t *testing.T) {
			checkBudgetRun(t, []string{"-cc=" + strings.ToUpper(cc)}, 6, cc)
		})
	}
}

func checkBudgetRun(t *testing.T, flags []string, cpus int, cc string) {
	t.Helper()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()
	dir := t.TempDir()
	ctrLog := filepath.Join(dir, "ctr.log")
	fakeCtr := "#!/bin/sh\nprintf '%s\\n' \"$@\" >> \"$HTTP_BUDGET_CTR_LOG\"\n"
	if err := os.WriteFile(filepath.Join(dir, "ctr"), []byte(fakeCtr), 0o755); err != nil {
		t.Fatal(err)
	}
	args := append(flags, "-backend-bind="+strings.TrimPrefix(server.URL, "http://"),
		"-outfile="+filepath.Join(dir, "raw.csv"), "-rps=1000", "-duration=1ms")
	cmd := budgetProcess(t, "run", args, "PATH="+dir+string(os.PathListSeparator)+os.Getenv("PATH"), "HTTP_BUDGET_CTR_LOG="+ctrLog)
	if output, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("HTTP run failed: %v\n%s", err, output)
	}
	workers := cpus
	if cc == "on" {
		workers /= 2
	}
	log, err := os.ReadFile(ctrLog)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(log), fmt.Sprintf("\nTIDE_WORKERS=%d\n", workers)) {
		t.Fatalf("backend does not receive %d workers:\n%s", workers, log)
	}
	for _, item := range []struct {
		name string
		want int
	}{{"warmup.csv", workers * 4}, {"raw.csv", 1}} {
		f, err := os.Open(filepath.Join(dir, item.name))
		if err != nil {
			t.Fatal(err)
		}
		rows, err := csv.NewReader(f).ReadAll()
		f.Close()
		if err != nil {
			t.Fatal(err)
		}
		if got := len(rows) - 1; got != item.want {
			t.Fatalf("%s: got %d requests, want %d", item.name, got, item.want)
		}
		for _, row := range rows[1:] {
			if row[4] != "200" {
				t.Fatalf("%s: unsuccessful HTTP row %v", item.name, row)
			}
		}
	}
}

func TestCPUBudgetInvalid(t *testing.T) {
	for _, value := range []string{"-2", "0", "1", "3", "17", "18"} {
		t.Run(value, func(t *testing.T) {
			output, err := budgetProcess(t, "parse", []string{"-cpu-num=" + value}).CombinedOutput()
			if err == nil || !strings.Contains(string(output), "must be even and in [2,16]") {
				t.Fatalf("expected CPU-budget rejection, got err=%v output=%s", err, output)
			}
		})
	}
}

func TestCPUBudgetBoundaries(t *testing.T) {
	for _, value := range []string{"2", "16"} {
		if output, err := budgetProcess(t, "parse", []string{"-cpu-num=" + value}).CombinedOutput(); err != nil {
			t.Fatalf("valid CPU budget %s rejected: %v\n%s", value, err, output)
		}
	}
}

func TestFlagValidationPreserved(t *testing.T) {
	for _, args := range [][]string{{"-cc=invalid"}, {"-rps=0"}, {"-duration=0"}, {"-cpu-num=2.5"}} {
		if output, err := budgetProcess(t, "parse", args).CombinedOutput(); err == nil {
			t.Fatalf("invalid flags %v accepted: %s", args, output)
		}
	}
}
