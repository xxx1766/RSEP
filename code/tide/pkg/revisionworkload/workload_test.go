package revisionworkload

import (
	"os"
	"testing"
)

func TestWorkloadsAreDeterministicAndCleanUp(t *testing.T) {
	for _, kind := range []string{"cpu", "memory", "io"} {
		t.Run(kind, func(t *testing.T) {
			dir := t.TempDir()
			cfg := Config{Kind: kind, Bytes: 4096, Iterations: 3, TempDir: dir}
			a, err := Run(cfg)
			if err != nil {
				t.Fatal(err)
			}
			b, err := Run(cfg)
			if err != nil {
				t.Fatal(err)
			}
			if a.Checksum == 0 || a.Checksum != b.Checksum || a.BytesProcessed != 12288 {
				t.Fatalf("non-reproducible or missing work: %+v, %+v", a, b)
			}
			files, err := os.ReadDir(dir)
			if err != nil || len(files) != 0 {
				t.Fatalf("workload leaked files: %v, %v", files, err)
			}
		})
	}
}

func TestInvalidWorkloadRejected(t *testing.T) {
	for _, cfg := range []Config{
		{Kind: "unknown", Bytes: 4096, Iterations: 1},
		{Kind: "cpu", Bytes: 0, Iterations: 1},
		{Kind: "memory", Bytes: 4096, Iterations: -1},
		{Kind: "io", Bytes: 1 << 30, Iterations: 1},
	} {
		if _, err := Run(cfg); err == nil {
			t.Fatalf("invalid input accepted: %+v", cfg)
		}
	}
}

func TestCPUWorkDependsOnIterationCount(t *testing.T) {
	a, _ := Run(Config{Kind: "cpu", Bytes: 4096, Iterations: 2})
	b, _ := Run(Config{Kind: "cpu", Bytes: 4096, Iterations: 3})
	if a.Checksum == b.Checksum {
		t.Fatal("CPU result ignores iteration count")
	}
}
