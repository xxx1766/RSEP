import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'revision'))
from issue8_metrics import partition

PHASES=['http_enter','submit','enqueue','dispatch','resource_acquired','pool_get','pool_acquired','execute','exit_received','http_result','http_written']
def sample():
    events=[dict(id='r1',phase=p,ns=1000+i*1000000,related='e1' if p=='pool_acquired' else '') for i,p in enumerate(PHASES)]
    row=dict(id='r1',latency_ms=12,status=200,worker_service_ns=500000,bytes_processed=6553600)
    return events,row
class PartitionTest(unittest.TestCase):
    def test_telescope(self):
        e,r=sample();x=partition(e,r)
        self.assertAlmostEqual(sum(x['stages_ms'].values()),10)
        self.assertAlmostEqual(x['external_ms'],2)
        self.assertAlmostEqual(x['ipc_residual_ms'],.5)
        self.assertEqual(x['executor'],'e1')
    def test_missing_stage(self):
        e,r=sample()
        with self.assertRaises(ValueError):partition(e[:-1],r)
    def test_order_violation(self):
        e,r=sample();e[4]['ns']=e[3]['ns']-1
        with self.assertRaises(ValueError):partition(e,r)
    def test_wrong_work(self):
        e,r=sample();r['bytes_processed']=42
        with self.assertRaises(ValueError):partition(e,r)
    def test_negative_worker_residual(self):
        e,r=sample();r['worker_service_ns']=2000000
        with self.assertRaises(ValueError):partition(e,r)
    def test_duplicate_stage(self):
        e,r=sample()
        with self.assertRaises(ValueError):partition(e+[e[0]],r)
if __name__=='__main__':unittest.main()
