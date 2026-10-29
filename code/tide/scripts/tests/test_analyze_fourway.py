"""Four-system statistics must be run-level and reject changed raw evidence."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from scripts.revision import analyze_fourway


class FourwayAnalysisTest(unittest.TestCase):
    def test_three_runs_produce_mean_and_run_level_interval(self):
        result = analyze_fourway.mean_ci([1.0, 2.0, 3.0])
        self.assertEqual(result["n"], 3)
        self.assertEqual(result["mean"], 2.0)
        self.assertLess(result["ci95_low"], 1.0)
        self.assertGreater(result["ci95_high"], 3.0)

    def test_changed_raw_file_invalidates_outer_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "raw.csv"
            raw.write_text("req_id,status\n0,200\n")
            manifest = {"status": "completed", "exit_code": 0, "timed_out": False,
                        "files_sha256": {"raw.csv": hashlib.sha256(raw.read_bytes()).hexdigest()}}
            analyze_fourway.verify_outer(root, manifest)
            raw.write_text("req_id,status\n0,500\n")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                analyze_fourway.verify_outer(root, manifest)


if __name__ == "__main__":
    unittest.main()
