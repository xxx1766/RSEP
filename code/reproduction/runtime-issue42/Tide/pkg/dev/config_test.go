package dev

import (
	"math"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestConfigOverridesAndSnapshotIsolation(t *testing.T) {
	old := CurrentConfig()
	t.Cleanup(func() { _ = SetConfig(old) })
	c := DefaultConfig()
	c.ExecutionMS = 25
	c.QoSMS = 100
	c.TTLMS = 80
	c.NodeExecutionMS = map[string]int{"slow": 50}
	c.NetworkMS = map[string]int{"slow": 12}
	c.QoSByFunctionMS = map[string]int{"tight": 40}
	if err := SetConfig(c); err != nil {
		t.Fatal(err)
	}
	c.NodeExecutionMS["slow"] = 999
	if GetEstExecTime("slow") != int(50*time.Millisecond) || GetEstExecTime("other") != int(25*time.Millisecond) {
		t.Fatal("node estimate or immutable configuration wrong")
	}
	if GetToleranceTimeFor("tight") != int(40*time.Millisecond) || GetToleranceTimeFor("other") != int(100*time.Millisecond) {
		t.Fatal("QoS lookup wrong")
	}
	if GetStubTTL() != 80*time.Millisecond || GetNetworkBudget("slow") != 12*time.Millisecond {
		t.Fatal("duration units wrong")
	}
	snapshot := CurrentConfig()
	snapshot.NodeExecutionMS["slow"] = 999
	if GetEstExecTime("slow") != int(50*time.Millisecond) {
		t.Fatal("snapshot modified live config")
	}
}

func TestConfigRejectsInvalidValues(t *testing.T) {
	for _, change := range []func(*Config){
		func(c *Config) { c.TTLMS = 0 }, func(c *Config) { c.QoSMS = -1 }, func(c *Config) { c.ExecutionMS = 0 },
		func(c *Config) { c.Weights = [3]float64{} }, func(c *Config) { c.Weights[0] = math.NaN() },
		func(c *Config) { c.NetworkMS = map[string]int{"n": -1} },
		func(c *Config) { c.DeviceWeights = map[string]float64{"n": 1.1} },
	} {
		c := DefaultConfig()
		change(&c)
		if err := SetConfig(c); err == nil {
			t.Fatalf("accepted invalid config %+v", c)
		}
	}
}

func TestLoadConfigKeepsDefaultsAndRejectsTypos(t *testing.T) {
	old := CurrentConfig()
	t.Cleanup(func() { _ = SetConfig(old) })
	path := filepath.Join(t.TempDir(), "config.json")
	if err := os.WriteFile(path, []byte(`{"qos_ms":123}`), 0600); err != nil {
		t.Fatal(err)
	}
	if err := LoadConfig(path); err != nil {
		t.Fatal(err)
	}
	if CurrentConfig().QoSMS != 123 || CurrentConfig().ExecutionMS != 430 {
		t.Fatal("partial defaults incorrect")
	}
	_ = os.WriteFile(path, []byte(`{"qoss_ms":123}`), 0600)
	if err := LoadConfig(path); err == nil {
		t.Fatal("unknown config key accepted")
	}
}

func TestLoadConfigRejectsNonObjectAndWrongWeightCount(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.json")
	for _, body := range []string{`null`, `{"weights":[1,0,0,1]}`, `{"weights":[1,0]}`, `{"weights":null}`, `{"Weights":[1,0,0,1]}`, `{"weights":[null,0,0]}`, `{"qos_ms":null}`} {
		if err := os.WriteFile(path, []byte(body), 0600); err != nil {
			t.Fatal(err)
		}
		if err := LoadConfig(path); err == nil {
			t.Errorf("accepted invalid config %s", body)
		}
	}
}
