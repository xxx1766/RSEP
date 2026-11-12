# Results index

Figure and table numbers follow the current manuscript and supplementary material.

| Item | Data | Reproduction |
|---|---|---|
| Fig.2 | `figures/current/data/motivation/` | Three original figure-only panels |
| Figs.5-6 | `data/compact/current/native_pytorch/` | `figures/current/native_pytorch.py` |
| Fig.7; TableII | `figures/current/data/calibration/`; `data/compact/current/score_calibration/` | `calibration.py`; `tools/aggregate_data.py` |
| Fig.8a | `data/compact/current/elasticity/` | `elasticity.py` |
| Fig.8b | `data/compact/current/lifecycle/` | `elasticity.py` |
| Fig.9 | `data/compact/current/capacity_sweep/`; `figures/current/data/capacity/` | `capacity.py` |
| Fig.10 | `figures/current/data/historical/` | `historical.py` |
| Fig.11 | `figures/current/data/historical/goodput.csv` | `historical.py` |
| Fig.12 | `data/compact/current/control_cost/`; `figures/current/data/control/` | `control.py` |
| Fig.13 | `data/compact/current/wan/` | `wan.py` |
| Fig.14 | `data/compact/current/domain_outage/`; `figures/current/data/domain/` | `domain.py` |
| TableIII | `data/compact/current/native_weight_study/` | `tools/aggregate_data.py` |
| Supplement Figs.S1-S9 | `figures/current/data/supplement_originals/`; historical numerical summaries | `supplement.py`, original assets |
| Supplement TableS3; admission result | `data/compact/current/admission_offline/` | `tools/build_tables.py`; `tools/aggregate_data.py` |
| Supplement TableS4 | `data/compact/current/mixed_qos/` | `tools/build_tables.py`; `tools/aggregate_data.py` |
| Supplement PAIX measurements | `data/compact/current/paix_ipc/` | `tools/aggregate_data.py` |
| TTL response and reservation measurements | `data/compact/current/ttl_retention/` | Nine run summaries |

Figs.1,3,4 and TablesI,S1,S2 present architecture, notation or document comparisons. They do not introduce independent experimental datasets.

`make figures` generates current experimental figures and copies the original historical figure assets. `make tables` exports CSV/JSON values. `make check` verifies release checksums, dataset accounting and numerical anchors. The full capacity dataset contains the three-pattern campaign; Fig.9 selects balanced and hotspot arrivals.

Historical case-summary values and source figure assets retain their own identities. New native measurements, restricted simulations and historical results have separate entries.
