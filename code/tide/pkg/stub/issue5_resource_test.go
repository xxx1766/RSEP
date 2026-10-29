package stub

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestIssue5ResourceSamplerWritesCPUAndRSS(t *testing.T) {
	path := filepath.Join(t.TempDir(), "resources.jsonl")
	stop := issue5StartResourceSampler(t, path)
	time.Sleep(220 * time.Millisecond)
	stop()
	body, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSpace(string(body)), "\n")
	if len(lines) < 2 {
		t.Fatalf("expected periodic and final resource samples, got %d", len(lines))
	}
	for _, line := range lines {
		var row struct {
			UnixNS      int64  `json:"unix_ns"`
			UserCPUUS   int64  `json:"user_cpu_us"`
			SystemCPUUS int64  `json:"system_cpu_us"`
			RSSBytes    uint64 `json:"rss_bytes"`
			MaxRSSBytes uint64 `json:"max_rss_bytes"`
		}
		if err := json.Unmarshal([]byte(line), &row); err != nil {
			t.Fatal(err)
		}
		if row.UnixNS <= 0 || row.RSSBytes == 0 || row.MaxRSSBytes == 0 {
			t.Fatalf("invalid resource sample: %+v", row)
		}
	}
}
