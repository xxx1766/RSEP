PYTHON ?= python3
TIDE   := code/tide

.PHONY: all figures check manifest test clean-figures

all: figures

figures:
	cd figures && "$(PYTHON)" calibration_panels.py
	cd figures && "$(PYTHON)" goodput_yield.py
	cd figures && "$(PYTHON)" compact_remaining_figures.py
	cd figures && "$(PYTHON)" fig6_cluster.py \
		--cells    ../data/summaries/fig05_cluster_workloads/cells.csv \
		--manifest ../data/summaries/fig05_cluster_workloads/manifest.json \
		--audit    ../data/summaries/fig05_cluster_workloads/audit.json \
		--capacity-labels \
		--output   rendered/evaluation/figD1_cluster.pdf

test:
	cd $(TIDE) && go test ./pkg/... ./cmd/...

manifest:
	"$(PYTHON)" tools/make_manifest.py

check:
	shasum -a 256 -c MANIFEST.sha256

clean-figures:
	rm -f figures/rendered/evaluation/{goodput_yield_compact.pdf,control_pressure_compact.pdf,figD1_cluster.pdf}
