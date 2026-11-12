package schedpolicy

import (
	"math"
	"testing"
)

func TestWeightsChangePreference(t *testing.T) {
	a := func(w [3]float64) float64 { return Score(.9, 200, .3, w, 100) }
	b := func(w [3]float64) float64 { return Score(.2, 20, .8, w, 100) }
	if a([3]float64{1, 0, 0}) <= b([3]float64{1, 0, 0}) {
		t.Fatal("capacity preference")
	}
	if a([3]float64{0, 1, 0}) >= b([3]float64{0, 1, 0}) {
		t.Fatal("latency preference")
	}
	if a([3]float64{0, 0, 1}) >= b([3]float64{0, 0, 1}) {
		t.Fatal("device preference")
	}
	if math.Abs(a([3]float64{1, 1, 1})-a([3]float64{2, 2, 2})) > 1e-12 {
		t.Fatal("weight normalization")
	}
}
func TestScoreFiniteAtZeroLatencyAndRejectsInvalidFeatures(t *testing.T) {
	if s := Score(1, 0, 1, [3]float64{1, 1, 1}, 100); s != 1 {
		t.Fatalf("zero-latency score %v", s)
	}
	for _, s := range []float64{Score(-1, 1, 1, [3]float64{1, 1, 1}, 100), Score(1, -1, 1, [3]float64{1, 1, 1}, 100), Score(1, 1, 1, [3]float64{}, 100)} {
		if !math.IsInf(s, -1) {
			t.Fatal("invalid features must be ineligible")
		}
	}
}
