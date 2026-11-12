PYTHON ?= python3

.PHONY: check figures tables manifest package test

check:
	$(PYTHON) tools/verify_release.py
	$(PYTHON) tools/reproduce_current.py check

figures:
	$(PYTHON) tools/reproduce_current.py figures --output figures/rendered/current

tables:
	$(PYTHON) tools/reproduce_current.py tables --output data/generated/tables

manifest:
	$(PYTHON) tools/make_manifest.py

package:
	$(PYTHON) tools/package_release.py --output ../RSEP-release.zip

test:
	cd code/tide && go test ./pkg/schedpolicy ./pkg/c4 ./cmd/revision-calibrate
