import unittest

from issue12_audit import audit_rows, validate_payload_scope


class Issue12AuditTests(unittest.TestCase):
    def setUp(self):
        self.trace = [{"id": "a", "scheduled_offset_ns": 0},
                      {"id": "b", "scheduled_offset_ns": 1000000}]
        self.rows = [
            {"id": "a", "scheduled_offset_ns": 0, "status": "success", "checksum": "42",
             "latency_ms": 100, "on_time": True, "submit_unix_ns": 10, "reply_unix_ns": 100000010},
            {"id": "b", "scheduled_offset_ns": 1000000, "status": "success", "checksum": 42,
             "latency_ms": 200, "on_time": False, "submit_unix_ns": 20, "reply_unix_ns": 200000020},
        ]

    def test_success_and_late_are_counted_against_offered_window(self):
        result = audit_rows(self.trace, self.rows, 1000, 42, 135)
        self.assertEqual((result["offered"], result["success"], result["on_time"], result["late_success"]),
                         (2, 2, 1, 1))
        self.assertEqual(result["on_time_goodput_rps"], 1)

    def test_duplicate_missing_checksum_or_false_on_time_rejected(self):
        for rows in [self.rows[:1], [self.rows[0], self.rows[0]],
                     [self.rows[0], dict(self.rows[1], checksum=43)],
                     [dict(self.rows[0], on_time=False), self.rows[1]]]:
            with self.assertRaises(ValueError):
                audit_rows(self.trace, rows, 1000, 42, 135)

    def test_network_payload_scope_must_match_on_every_request(self):
        validate_payload_scope({"request_payload_bytes": 65536},
                               [{"request_payload_bytes": 65536}], 65536)
        with self.assertRaises(ValueError):
            validate_payload_scope({"request_payload_bytes": 65536},
                                   [{"request_payload_bytes": 0}], 65536)


if __name__ == "__main__":
    unittest.main()
