import unittest

from issue12_compare import paired_summary


class PairedSummaryTests(unittest.TestCase):
    def test_equal_repeats_have_exact_difference_interval(self):
        audits = []
        for repeat in (1, 2, 3):
            audits.append({"cell": {"pattern": "gradual", "producers": 1, "repeat": repeat},
                           "systems": {"tide": {"on_time_goodput_rps": 1, "yield_fraction": 0.25},
                                       "k8s": {"on_time_goodput_rps": 3, "yield_fraction": 0.75}}})
        result = paired_summary(audits)
        row = result["gradual-p1"]
        self.assertEqual(row["goodput_difference_k8s_minus_tide_rps"]["mean"], 2)
        self.assertEqual(row["goodput_difference_k8s_minus_tide_rps"]["ci95"], [2, 2])
        self.assertEqual(row["yield_difference_k8s_minus_tide"]["mean"], 0.5)


if __name__ == "__main__":
    unittest.main()
