package stub

import (
	"encoding/json"
	"math/rand"
	"os"
	"reflect"
	"sort"
	"testing"
	"time"

	"github.com/ict/tide/pkg/revisionworkload"
)

// issue5GenerateTrace freezes arrival times before comparing candidate weights.
func issue5GenerateTrace(seed int64, rate int, duration time.Duration, pattern string) []time.Duration {
	rng := rand.New(rand.NewSource(seed))
	n := int(duration.Seconds() * float64(rate))
	trace := make([]time.Duration, n)
	for i := range trace {
		fraction := (float64(i) + 0.2*rng.Float64()) / float64(n)
		if pattern == "burst" {
			const group = 4
			groups := (n + group - 1) / group
			fraction = (float64(i/group) + 0.2*float64(i%group)/group + 0.01*rng.Float64()) / float64(groups)
		}
		trace[i] = time.Duration(fraction * float64(duration))
	}
	return trace
}

func TestIssue5PairedTrace(t *testing.T) {
	const seed int64 = 20261006
	first := issue5GenerateTrace(seed, 8, 2*time.Second, "steady")
	paired := issue5GenerateTrace(seed, 8, 2*time.Second, "steady")
	other := issue5GenerateTrace(seed+1, 8, 2*time.Second, "steady")
	if len(first) != 16 || !reflect.DeepEqual(first, paired) {
		t.Fatalf("paired weights must see the same 16-request trace: %v / %v", first, paired)
	}
	if reflect.DeepEqual(first, other) {
		t.Fatal("independent repeats must not reuse the same trace")
	}
	for i, at := range first {
		if at < 0 || at >= 2*time.Second || i > 0 && at < first[i-1] {
			t.Fatalf("invalid trace offset %d: %v", i, at)
		}
	}
	burst := issue5GenerateTrace(seed, 8, 2*time.Second, "burst")
	if len(burst) != 16 || reflect.DeepEqual(first, burst) {
		t.Fatalf("burst trace must use the same request count with a distinct arrival pattern: %v", burst)
	}
}

// The smoke test must exercise two independently served remote Tide stubs,
// collect actual CPU results, and observe a weight-dependent native decision.
func TestIssue5NativeTwoCandidateSmoke(t *testing.T) {
	issue5RunSmoke(t)
}

func TestIssue5NativeMatrixPilot(t *testing.T) {
	issue5RunMatrixSmoke(t)
}

func TestIssue5KernelCalibration(t *testing.T) {
	out := os.Getenv("TMC_ISSUE5_CALIBRATE_OUT")
	if out == "" {
		t.Skip("calibration helper")
	}
	kernel := revisionworkload.Config{Kind: "cpu", Bytes: 65536, Iterations: 30}
	for i := 0; i < 5; i++ {
		if _, err := revisionworkload.Run(kernel); err != nil {
			t.Fatal(err)
		}
	}
	values := make([]float64, 50)
	for i := range values {
		start := time.Now()
		if _, err := revisionworkload.Run(kernel); err != nil {
			t.Fatal(err)
		}
		values[i] = float64(time.Since(start).Microseconds()) / 1000
	}
	sort.Float64s(values)
	body, err := json.MarshalIndent(map[string]any{"workload": kernel, "samples_ms_sorted": values,
		"median_ms": (values[24] + values[25]) / 2, "p95_ms": values[47], "n": len(values), "measured_utc": time.Now().UTC()}, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(out, append(body, '\n'), 0644); err != nil {
		t.Fatal(err)
	}
}
