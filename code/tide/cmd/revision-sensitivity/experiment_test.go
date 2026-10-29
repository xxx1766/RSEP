package main

import (
	"compress/gzip"
	"encoding/csv"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"reflect"
	"testing"
)

func TestExperimentRecordsEveryDecisionAndReplays(t *testing.T) {
	dir := t.TempDir()
	calibration := filepath.Join(dir, "calibration.json")
	err := os.WriteFile(calibration, []byte(`{"cpu":{"service_p95_ms":21.283347},"memory":{"service_p95_ms":78.898112},"io":{"service_p95_ms":59.614709}}`), 0600)
	if err != nil {
		t.Fatal(err)
	}
	cfg := Config{Output: filepath.Join(dir, "first"), Calibration: calibration, SourceRoot: "../..", Repetitions: 1, DurationSeconds: 1}
	if err = Run(cfg); err != nil {
		t.Fatal(err)
	}
	loadRows := func(path string) []RunSummary {
		data, e := os.ReadFile(filepath.Join(path, "summary.json"))
		if e != nil {
			t.Fatal(e)
		}
		var rows []RunSummary
		if e = json.Unmarshal(data, &rows); e != nil {
			t.Fatal(e)
		}
		return rows
	}
	rows := loadRows(cfg.Output)
	if len(rows) != 396 {
		t.Fatalf("run count %d", len(rows))
	}
	file, e := os.Open(filepath.Join(cfg.Output, "requests.csv.gz"))
	if e != nil {
		t.Fatal(e)
	}
	defer file.Close()
	gz, e := gzip.NewReader(file)
	if e != nil {
		t.Fatal(e)
	}
	defer gz.Close()
	reader := csv.NewReader(gz)
	if _, e = reader.Read(); e != nil {
		t.Fatal(e)
	}
	counts := map[string]int{}
	for {
		record, e := reader.Read()
		if e == io.EOF {
			break
		}
		if e != nil {
			t.Fatal(e)
		}
		counts[record[0]]++
	}
	traceRuns := map[string]int{}
	for _, row := range rows {
		if counts[row.RunID] != row.Metrics.Arrivals || row.Metrics.Accepted+row.Metrics.Rejected != row.Metrics.Arrivals || row.Metrics.CompletedWithinWindow+row.Metrics.DrainedAfterWindow != row.Metrics.Accepted {
			t.Fatalf("count mismatch %+v", row)
		}
		traceRuns[row.TraceID]++
	}
	if len(traceRuns) != 3 {
		t.Fatal("incorrect input traces")
	}
	for _, n := range traceRuns {
		if n != 132 {
			t.Fatal("trace not shared across weights and networks")
		}
	}
	cfg.Traces = filepath.Join(cfg.Output, "traces.json")
	cfg.Output = filepath.Join(dir, "replay")
	if err = Run(cfg); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(rows, loadRows(cfg.Output)) {
		t.Fatal("saved trace replay differs")
	}
}
