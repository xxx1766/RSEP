import math
import unittest

from issue12_network_probe import summarize


class ProbeSummaryTests(unittest.TestCase):
    def test_errors_stay_in_denominator(self):
        rows = [{"rtt_ms": 60.0}, {"rtt_ms": 70.0}, {"error": "timeout"}]
        got = summarize(rows)
        self.assertEqual(got["sent"], 3)
        self.assertEqual(got["received"], 2)
        self.assertAlmostEqual(got["error_fraction"], 1 / 3)
        self.assertAlmostEqual(got["mean_ms"], 65.0)
        self.assertAlmostEqual(got["median_ms"], 65.0)
        self.assertAlmostEqual(got["stdev_ms"], math.sqrt(50))

    def test_all_errors_have_no_latency_summary(self):
        got = summarize([{"error": "closed"}])
        self.assertIsNone(got["median_ms"])
        self.assertEqual(got["error_fraction"], 1.0)


if __name__ == "__main__":
    unittest.main()
