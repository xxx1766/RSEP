// Package schedpolicy supplies the same bounded score to runtime selection
// and restricted offline experiments. Feasibility is checked before scoring.
package schedpolicy

import "math"

// Score combines remaining CPU fraction, predicted response latency in ms,
// and a calibrated device weight in [0,1]. Weight magnitudes are normalized.
// The bounded latency term avoids a singularity at zero network latency.
func Score(freeFraction, latencyMS, deviceWeight float64, weights [3]float64, latencyScaleMS float64) float64 {
	for _, v := range []float64{freeFraction, latencyMS, deviceWeight, latencyScaleMS, weights[0], weights[1], weights[2]} {
		if math.IsNaN(v) || math.IsInf(v, 0) || v < 0 {
			return math.Inf(-1)
		}
	}
	sum := weights[0] + weights[1] + weights[2]
	if freeFraction > 1 || deviceWeight > 1 || latencyScaleMS <= 0 || sum <= 0 || math.IsInf(sum, 0) {
		return math.Inf(-1)
	}
	return (weights[0]*freeFraction + weights[1]/(1+latencyMS/latencyScaleMS) + weights[2]*deviceWeight) / sum
}
