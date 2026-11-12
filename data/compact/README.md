# Compact data

`current/INDEX.json` lists the figure payloads and experiment-family metadata. `DATASET.json` files give cohort sizes,units,file checksums and source identities.

Request tables retain run/configuration,request ID,relative timing,status,deadline outcome and execution role where recorded. Compressed CSV files use standard gzip. Missing fields are empty. Run summaries retain all offered requests and the recorded observation windows. `retained` identifies the runs used by TableIII.

`python tools/aggregate_data.py` independently checks compact requests against run totals and recomputes TableII,TableIII,the PAIX summary,the admission ratios and WAN run results. `make tables` also exports the supplementary admission and mixed-QoS tables. Output files use CSV and JSON.
