import importlib.util
from pathlib import Path
import unittest

P=Path(__file__).parents[1]/'revision'/'audit_control_comparison.py'

class AuditTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(P.exists(),'independent control data audit is not implemented')
        spec=importlib.util.spec_from_file_location('audit',P)
        self.m=importlib.util.module_from_spec(spec); spec.loader.exec_module(self.m)
        self.row=dict(req_id='0',planned_s='0',submit_s='0.001',reply_s='.026',latency_ms='25',
                      status='200',ddl_ms='50',worker_service_ns='20000000',bytes_processed='6553600',error='')

    def test_latency_is_recomputed_from_timestamps(self):
        self.m.audit_request(self.row,5,50,1)
        with self.assertRaises(ValueError): self.m.audit_request(dict(self.row,latency_ms='10'),5,50,1)

    def test_invalid_work_or_nonfinite_clock_rejected(self):
        for changes in ({'bytes_processed':'1'},{'reply_s':'nan'},{'submit_s':'-.1'}):
            with self.assertRaises(ValueError): self.m.audit_request(dict(self.row,**changes),5,50,1)

    def test_failed_request_is_retained_without_success_payload(self):
        self.m.audit_request(dict(self.row,status='599',worker_service_ns='0',bytes_processed='0'),5,50,1)

if __name__=='__main__': unittest.main()
