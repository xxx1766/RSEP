# E8 — Offline objective calibration

**Paper item:** Fig. 16, Tables III–IV, §VI-E calibration subsection.

## Question

Do the frozen online score weights (0, 0.9, 0.1) survive different objective
preferences, and how sensitive is the choice to those weights?

## Design

- Restricted model (not native Tide execution): modelled CPU/memory occupancy,
  deadline yield, latency.
- Six preference profiles over the four-objective simplex.
- Grid over online score weights; stars mark the frozen choice, circles mark
  equal weights (1,1,1).
- Selection rule: calibration-only mean reward; ties within 1e-12 prefer the
  closest to equal weights, then grid order.
- Held-out scenarios are evaluated but never used for selection.

## Files

- Grid data: `data/summaries/fig16_objective_calibration/plot_data.json`
  (SHA-256 checked by the plotting script)
- Held-out means: `heldout_summary.csv`, `per_scenario.csv`
- Selection audit: `selection.json`, `manifest.json`
- Sensitivity: `lambda_sensitivity.json`
- Exports: `objective-calibration-exports/`
- Figure script: `figures/calibration_panels.py`
- Binary: `code/tide/cmd/revision-calibrate/`
- Tests: `go test ./cmd/revision-calibrate ./pkg/schedpolicy`

## Caveats

This is a simulated restricted model, not measured CPU/RSS or fixed Stub
reservation. `R` here is outage-arrival deadline yield, distinct from
structural `R_cap`. The calibration does not validate model-wide memory
management and does not vary `α_full`.
