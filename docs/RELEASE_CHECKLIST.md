# Release checklist

Status as of 2026-10-29. The local artifact is complete and verified; the items
below are the remaining publication steps.

## Done locally

- [x] Curated tree assembled (`code/`, `data/`, `figures/`, `experiments/`, `docs/`, `paper/`).
- [x] No absolute user paths, internal registry address, or credentials.
      Placeholders: `<REPO>`, `<HOME>`, `registry.example.org`,
      `198.51.100.0/24`.
- [x] 989 files, 31 MB; `MANIFEST.sha256` verifies.
- [x] Four figure scripts re-run from the shipped exports:
      `calibration_panels.py`, `goodput_yield.py`,
      `compact_remaining_figures.py`, `fig6_cluster.py`.
- [x] `go test ./pkg/schedpolicy ./cmd/revision-calibrate` passes.
- [x] `make figures` and `make check` work.

## Before upload

- [x] License confirmed: Apache-2.0 for code, data and figures (`LICENSE`).
- [ ] Confirm author list/order in `CITATION.cff`.
- [x] Repository created (private) at https://github.com/xxx1766/RSEP.
- [x] Response letter Major 5 now points to the repository URL; the paper has
      no separate data-availability statement.
- [x] Confirmed: the private repository contains only the processed
      plotting exports (no raw per-request logs, no 16 GB campaign trees).
- [x] `git init`, first commit and push to https://github.com/xxx1766/RSEP.
- [ ] Tag `v1.0.0` at submission.
- [ ] Mint a DOI (Zenodo or institutional) and add it to `README.md` and
      `CITATION.cff`.
- [ ] Archive a `.zip`/`.tar.gz` of the tagged tree with the DOI.
- [ ] Re-generate `MANIFEST.sha256` after any edit.

## Optional

- [ ] Add a 1–2 minute screencast of `make figures`.
- [ ] Add CI (GitHub Actions) running the Python figure checks and the two Go
      test packages.
- [ ] If raw logs are ever released, split them per experiment and keep the
      campaign names used in `MANIFEST.sha256`.
