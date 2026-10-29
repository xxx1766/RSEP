package stub

import (
	"os"
	"path/filepath"
	"testing"
)

func TestIssue12FrozenTraceRejectsMissingDuplicateAndOutOfWindowIDs(t *testing.T) {
	cases := []struct {
		name, input string
		valid       bool
	}{
		{"valid coincident arrivals", "{\"id\":\"a\",\"scheduled_offset_ns\":0}\n{\"id\":\"b\",\"scheduled_offset_ns\":0}\n", true},
		{"duplicate", "{\"id\":\"a\",\"scheduled_offset_ns\":0}\n{\"id\":\"a\",\"scheduled_offset_ns\":1}\n", false},
		{"reverse time", "{\"id\":\"a\",\"scheduled_offset_ns\":2}\n{\"id\":\"b\",\"scheduled_offset_ns\":1}\n", false},
		{"outside window", "{\"id\":\"a\",\"scheduled_offset_ns\":1000001}\n", false},
		{"empty", "", false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "trace.jsonl")
			if err := os.WriteFile(path, []byte(tc.input), 0600); err != nil {
				t.Fatal(err)
			}
			rows, hash, err := issue5LoadFrozenTrace(path, 1)
			if (err == nil) != tc.valid {
				t.Fatalf("valid=%v rows=%v sha=%s err=%v", tc.valid, rows, hash, err)
			}
			if tc.valid && (len(rows) != 2 || hash == "") {
				t.Fatalf("bad valid trace rows=%v sha=%s", rows, hash)
			}
		})
	}
}
