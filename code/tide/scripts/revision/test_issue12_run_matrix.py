import unittest

from issue12_run_matrix import make_plan, origin_config


class FormalMatrixTests(unittest.TestCase):
    def test_matrix_is_frozen_balanced_and_complete(self):
        cells = make_plan(20261007)
        self.assertEqual(len(cells), 12)
        self.assertEqual(len({cell["id"] for cell in cells}), 12)
        self.assertEqual(sum(cell["order"][0] == "tide" for cell in cells), 6)
        self.assertEqual({cell["producers"] for cell in cells}, {1, 2})
        self.assertEqual({cell["pattern"] for cell in cells}, {"gradual", "burst"})
        self.assertEqual({cell["repeat"] for cell in cells}, {1, 2, 3})

    def test_origin_uses_exact_trace_and_separate_warmup_deadline(self):
        cell = {"id": "gradual-p1-r1", "pattern": "gradual", "producers": 1, "repeat": 1, "window_ms": 270000}
        config = origin_config(cell)
        self.assertEqual(config["qos_ms"], 135)
        self.assertEqual(config["warmup_qos_ms"], 1000)
        self.assertEqual(config["runs"][0]["trace_path"], "/tmp/tide-issue12-20261007/formal-v2/gradual-p1-r1.jsonl")
        self.assertEqual(config["runs"][0]["duration_ms"], 270000)


if __name__ == "__main__":
    unittest.main()
