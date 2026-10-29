# Recovered historical summaries

The Zheng placement CSV contains100 table entries,200 reported percentages and source-page provenance. Decimal execution totals are reported summaries rather than original integer event records.

The two goodput CSVs contain geometry-derived approximate values from the published thesis and TMC PDFs; they are not new experiments or recovered raw request logs. Run `python3 -B extract_goodput_vectors.py` in this directory to regenerate them from the supplied native SVGs and verify the25 shared points. Two near-zero differences remain documented in the output JSON. All zero-line quantization and absent-pattern flags are retained.

The thesis metadata files identify the original local sources and their hashes. The original thesis PDFs are not bundled. The manuscript/supplement give the physical and printed source-page references, definitions and protocol differences.

The editable main-text Fig.10 uses all100 table rows. Fig.11 uses `allocation_trace_vector_vertices.csv`: all77 original native polyline vertices, calibrated from independent axis ticks by `extract_allocation_trace.py`. These are approximate plotted coordinates, not recovered original sampling events. The extractor retains every short peak and original line segment without rounding, smoothing or inserted samples. Fig.12 uses all50 TMC bar pairs and original hatch-presence flags. Full render functions are in `../historical_redraw.py` and are embedded in the unified notebook; original PDFs remain separate archival files.
