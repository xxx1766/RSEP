# E7 — Historical re-derivation

**Paper item:** Figs. 11–15.

## What this is

Re-derived values from previously published CC/PI/EI measurements, not new
runs. The artifact stores the extracted values, the source pointers and the
provenance hashes so that every redrawn point can be traced to a source table
or vector figure.

## Files

- `data/summaries/fig11_12_historical/HISTORICAL_ABLATION_MAIN_VALUES.json`
- `data/summaries/fig11_12_historical/HISTORICAL_LATENCY_MAIN_VALUES.json`
- `data/summaries/fig11_12_historical/HISTORICAL_FIGURE_PROVENANCE.json`
- `data/summaries/fig11_12_historical/historical_sources/*.csv|json|svg`
- Figure scripts: `figures/historical_ablation_main.py`,
  `figures/historical_latency_main.py`, `figures/historical_redraw.py`,
  `figures/historical_ablation_appendix.py`

## Caveats

Timing scopes and uncertainty in the original sources are unverified. The
historical settings differ from the new controlled comparisons and are not
pooled with them. All 105 workbook values (on/off ratios) and all 120 latency
summary values are retained; request distributions are not reconstructed.
