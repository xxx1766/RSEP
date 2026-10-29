import unittest

try:
    from issue12_nj_bandwidth import summarize
except ImportError:
    summarize = None


class BandwidthAccountingTests(unittest.TestCase):
    def test_common_receiver_window_not_sum_of_stream_rates(self):
        self.assertIsNotNone(summarize, "receiver-window accounting is missing")
        rows = [dict(bytes=1000000, first_ns=1000000000, last_ns=2000000000),
                dict(bytes=1000000, first_ns=1500000000, last_ns=3000000000)]
        result = summarize(rows)
        self.assertEqual(result["bytes"], 2000000)
        self.assertEqual(result["window_s"], 2)
        self.assertEqual(result["mbps"], 8)

    def test_empty_or_failed_stream_is_not_a_capacity_result(self):
        self.assertIsNotNone(summarize, "receiver-window accounting is missing")
        for rows in ([], [dict(bytes=0, first_ns=None, last_ns=1)], [dict(error="timeout")]):
            with self.assertRaises(ValueError):
                summarize(rows)


if __name__ == "__main__":
    unittest.main()
