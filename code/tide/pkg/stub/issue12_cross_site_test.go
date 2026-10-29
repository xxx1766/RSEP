package stub

import "testing"

func TestIssue12OriginAdvertisesReachableCallback(t *testing.T) {
	if got := issue5OriginAddress(issue5OriginConfig{Listen: "10.212.12.2:29582", AdvertiseAddr: "9.0.3.35:29582"}); got != "9.0.3.35:29582" {
		t.Fatalf("advertised address %q", got)
	}
	if got := issue5OriginAddress(issue5OriginConfig{Listen: "127.0.0.1:29582"}); got != "127.0.0.1:29582" {
		t.Fatalf("legacy address changed: %q", got)
	}
}

func TestIssue12WarmupDeadlineDoesNotChangeMeasuredDeadline(t *testing.T) {
	c := issue5OriginConfig{QoSMS: 135, ExecutionMS: 2, WarmupQoSMS: 1000,
		Candidates: []issue5Candidate{{NodeID: "nj2", NetworkMS: 65}, {NodeID: "nj3", NetworkMS: 65}}}
	cfg, err := issue5OriginScheduleConfig(c)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.QoSMS != 135 || cfg.QoSByFunctionMS["cpu-warm"] != 1000 || cfg.QoSByFunctionMS["cpu"] != 0 || cfg.NodeExecutionMS[issue5OriginID] <= 1000 {
		t.Fatalf("warmup QoS leaked into formal requests: %+v", cfg)
	}
}
