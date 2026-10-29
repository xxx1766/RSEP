import importlib.util
from pathlib import Path
import unittest

path=Path(__file__).parents[1]/'revision/analyze_fig5_confirmatory.py'
spec=importlib.util.spec_from_file_location('fig5stats',path)
stats=importlib.util.module_from_spec(spec)
spec.loader.exec_module(stats)


def row(i,start,end,status=200):
    return dict(req_id=str(i),t_submit_ns=str(int(start*1e9)),t_reply_ns=str(int(end*1e9)),latency_ms=str((end-start)*1000),status=str(status))


class Fig5StatisticsTests(unittest.TestCase):
    def test_low_rate_does_not_exceed_offered_rate(self):
        values=stats.summarize_rows([row(1,1,1.2),row(2,2,2.2),row(3,3,3.2)],3)
        self.assertAlmostEqual(values['throughput'],3/3.2)
        self.assertLess(values['throughput'],1)

    def test_overload_includes_drain_and_failed_request(self):
        values=stats.summarize_rows([row(1,.1,1),row(2,.2,2),row(3,.3,4,504)],1)
        self.assertEqual(values['throughput'],.5)
        self.assertEqual(values['n_failed'],1)
        self.assertAlmostEqual(values['success_yield'],2/3)
        self.assertEqual(values['avg_ms'],1350)

    def test_duplicate_requests_are_rejected(self):
        with self.assertRaises(ValueError): stats.summarize_rows([row(1,1,2),row(1,2,3)],3)


if __name__=='__main__': unittest.main()
