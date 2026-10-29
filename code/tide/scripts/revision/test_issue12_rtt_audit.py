import unittest

from issue12_rtt_audit import parse_ping, summarize_window


class ContinuousRTTAuditTests(unittest.TestCase):
    def test_timestamped_ping_and_sequence_gap(self):
        lines = """PING 11.0.4.48 (11.0.4.48) 56(84) bytes of data.
[100.0] 64 bytes from 11.0.4.48: icmp_seq=1 ttl=60 time=65.2 ms
[110.0] 64 bytes from 11.0.4.48: icmp_seq=3 ttl=60 time=66.8 ms
"""
        rows = parse_ping(lines)
        result = summarize_window(rows, 99, 111)
        self.assertEqual((result["received"], result["sequence_gap_count"]), (2, 1))
        self.assertEqual(result["median_ms"], 66)

    def test_two_monitor_segments_do_not_create_false_sequence_gap(self):
        first = parse_ping("[100.0] 64 bytes from x: icmp_seq=8 ttl=60 time=65.0 ms\n", source=0)
        second = parse_ping("[105.0] 64 bytes from x: icmp_seq=1 ttl=60 time=66.0 ms\n", source=1)
        result = summarize_window(first + second, 99, 106)
        self.assertEqual((result["received"], result["sequence_gap_count"]), (2, 0))


if __name__ == "__main__":
    unittest.main()
