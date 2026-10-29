import unittest

from issue12_qdisc_audit import parse_qdisc, deltas


class QdiscCounterTests(unittest.TestCase):
    def test_packet_byte_and_drop_deltas_follow_actual_order(self):
        def counter(byte, packet, drop):
            return f"qdisc netem delay 44ms 5ms loss 0.1%\n Sent {byte} bytes {packet} pkt (dropped {drop}, overlimits 0 requeues 0)\n"
        before = parse_qdisc(counter(100, 10, 1))
        first = parse_qdisc(counter(400, 13, 2))
        second = parse_qdisc(counter(900, 19, 2))
        result = deltas(before, first, second, ["k8s", "tide"])
        self.assertEqual(result["k8s"], {"bytes": 300, "packets": 3, "dropped": 1})
        self.assertEqual(result["tide"], {"bytes": 500, "packets": 6, "dropped": 0})


if __name__ == "__main__":
    unittest.main()
