package dev

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"os"
	"sync"
	"time"
)

// Config contains fixed, recorded inputs for a run. Timing estimates are
// calibrated before evaluation; these values are not measured outcomes.
type Config struct {
	C4Functions     map[string]bool    `json:"c4_functions,omitempty"`
	ExecutionMS     int                `json:"execution_ms"`
	QoSMS           int                `json:"qos_ms"`
	TTLMS           int                `json:"ttl_ms"`
	Weights         [3]float64         `json:"weights"`
	LatencyScaleMS  int                `json:"latency_scale_ms"`
	StateMaxAgeMS   int                `json:"state_max_age_ms"`
	NodeExecutionMS map[string]int     `json:"node_execution_ms,omitempty"`
	NetworkMS       map[string]int     `json:"network_ms,omitempty"`
	DeviceWeights   map[string]float64 `json:"device_weights,omitempty"`
	QoSByFunctionMS map[string]int     `json:"qos_by_function_ms,omitempty"`
}

func DefaultConfig() Config {
	return Config{ExecutionMS: 430, QoSMS: 650, TTLMS: 2000, Weights: [3]float64{1, 1, 1}, LatencyScaleMS: 100, StateMaxAgeMS: 5000}
}

var configMu sync.RWMutex
var config = DefaultConfig()

func cloneMap[T any](m map[string]T) map[string]T {
	if m == nil {
		return nil
	}
	out := make(map[string]T, len(m))
	for k, v := range m {
		out[k] = v
	}
	return out
}
func (c Config) copy() Config {
	c.C4Functions = cloneMap(c.C4Functions)
	c.NodeExecutionMS = cloneMap(c.NodeExecutionMS)
	c.NetworkMS = cloneMap(c.NetworkMS)
	c.DeviceWeights = cloneMap(c.DeviceWeights)
	c.QoSByFunctionMS = cloneMap(c.QoSByFunctionMS)
	return c
}
func CurrentConfig() Config { configMu.RLock(); defer configMu.RUnlock(); return config.copy() }

func SetConfig(c Config) error {
	if err := c.validate(); err != nil {
		return err
	}
	configMu.Lock()
	defer configMu.Unlock()
	config = c.copy()
	return nil
}

func (c Config) validate() error {
	for k, v := range map[string]int{"execution_ms": c.ExecutionMS, "qos_ms": c.QoSMS, "ttl_ms": c.TTLMS, "latency_scale_ms": c.LatencyScaleMS, "state_max_age_ms": c.StateMaxAgeMS} {
		if v <= 0 || v > 86400000 {
			return fmt.Errorf("%s must be in (0, 86400000] ms", k)
		}
	}
	for fn := range c.C4Functions {
		if fn == "" {
			return fmt.Errorf("c4_functions requires nonempty function names")
		}
	}
	sum := 0.0
	for _, w := range c.Weights {
		if math.IsNaN(w) || math.IsInf(w, 0) || w < 0 {
			return fmt.Errorf("weights must be finite and nonnegative")
		}
		sum += w
	}
	if sum <= 0 || math.IsInf(sum, 0) {
		return fmt.Errorf("weight sum must be finite and positive")
	}
	for name, m := range map[string]map[string]int{"node_execution_ms": c.NodeExecutionMS, "qos_by_function_ms": c.QoSByFunctionMS, "network_ms": c.NetworkMS} {
		for key, v := range m {
			min := 1
			if name == "network_ms" {
				min = 0
			}
			if key == "" || v < min || v > 86400000 {
				return fmt.Errorf("invalid %s entry %q", name, key)
			}
		}
	}
	for key, w := range c.DeviceWeights {
		if key == "" || math.IsNaN(w) || math.IsInf(w, 0) || w < 0 || w > 1 {
			return fmt.Errorf("device weight %q must be in [0,1]", key)
		}
	}
	return nil
}

func LoadConfig(path string) error {
	body, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	var object map[string]json.RawMessage
	if err := json.Unmarshal(body, &object); err != nil {
		return err
	}
	if object == nil {
		return fmt.Errorf("config must be a JSON object")
	}
	for key := range object {
		switch key {
		case "execution_ms", "qos_ms", "ttl_ms", "weights", "latency_scale_ms", "state_max_age_ms", "node_execution_ms", "network_ms", "device_weights", "qos_by_function_ms", "c4_functions":
		default:
			return fmt.Errorf("unknown or noncanonical config field %q", key)
		}
	}
	// encoding/json otherwise treats null numbers as unchanged defaults.
	tokens := json.NewDecoder(bytes.NewReader(body))
	for {
		token, err := tokens.Token()
		if err == io.EOF {
			break
		}
		if err != nil {
			return err
		}
		if token == nil {
			return fmt.Errorf("null configuration values are not supported; omit optional fields")
		}
	}
	if raw, ok := object["weights"]; ok {
		var weights []float64
		if err := json.Unmarshal(raw, &weights); err != nil {
			return err
		}
		if len(weights) != 3 {
			return fmt.Errorf("weights must contain exactly three entries")
		}
	}
	c := DefaultConfig()
	d := json.NewDecoder(bytes.NewReader(body))
	d.DisallowUnknownFields()
	if err = d.Decode(&c); err != nil {
		return err
	}
	var extra interface{}
	if err = d.Decode(&extra); err != io.EOF {
		return fmt.Errorf("config must contain exactly one JSON object")
	}
	return SetConfig(c)
}

func GetStubTTL() time.Duration {
	configMu.RLock()
	defer configMu.RUnlock()
	return time.Duration(config.TTLMS) * time.Millisecond
}
func GetNetworkBudget(node string) time.Duration {
	configMu.RLock()
	defer configMu.RUnlock()
	return time.Duration(config.NetworkMS[node]) * time.Millisecond
}
func GetToleranceTimeFor(fn string) int {
	configMu.RLock()
	defer configMu.RUnlock()
	v := config.QoSMS
	if n, ok := config.QoSByFunctionMS[fn]; ok {
		v = n
	}
	return int(time.Duration(v) * time.Millisecond)
}
