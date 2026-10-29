import unittest

try:
    from issue12_nj_analyze import verify_execution, rtt_window
except ImportError:
    verify_execution = None


class IndependentEvidenceTests(unittest.TestCase):
    def test_server_tensor_evidence_must_match_frozen_image(self):
        self.assertIsNotNone(verify_execution, "server-side evidence verifier missing")
        expected=dict(input_sha256="input",output_sha256="tensor",bytes=100,checksum="7")
        actual=dict(expected,status="success",finite=True,shape=[1,3549,85],service_ns=10,
                    received_unix_ns=100,completed_unix_ns=200)
        verify_execution(actual,expected)
        for patch in ({"output_sha256":"other"},{"finite":False},{"service_ns":0},
                      {"bytes":99},{"completed_unix_ns":99}):
            with self.assertRaises(ValueError):
                verify_execution(dict(actual,**patch),expected)

    def test_rtt_gap_is_not_reported_as_continuous_coverage(self):
        self.assertIsNotNone(verify_execution)
        samples=[(float(i),65.0) for i in range(11)]
        self.assertTrue(rtt_window(samples,0,10)["coverage_complete"])
        partial=[row for row in samples if row[0]<2 or row[0]>8]
        checked=rtt_window(partial,0,10)
        self.assertFalse(checked["coverage_complete"])
        self.assertEqual(checked["max_observation_gap_s"],8)


if __name__=="__main__":
    unittest.main()
