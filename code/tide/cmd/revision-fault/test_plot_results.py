import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('fault_plot',Path(__file__).with_name('plot_results.py'))
plot=importlib.util.module_from_spec(spec)
spec.loader.exec_module(plot)

class AuditTests(unittest.TestCase):
    def test_cohort_and_half_open_window(self):
        events=[{'phase':'connected','transition_completed_ns':0},{'phase':'disconnected','transition_completed_ns':100},{'phase':'recovered','transition_completed_ns':200}]
        rows=[{'t_submit_ns':'20','t_reply_ns':'120','route':'local','status':'200'}, {'t_submit_ns':'130','t_reply_ns':'150','route':'remote','status':'503'}, {'t_submit_ns':'230','t_reply_ns':'300','route':'local','status':'200'}]
        result=plot.reaggregate(rows,events,300,100)
        self.assertEqual(result[0]['cohort_ontime'],1)
        self.assertEqual(result[0]['window_ontime_completions'],0)
        self.assertEqual(result[1]['window_ontime_completions'],1)
        self.assertEqual(result[1]['cohort_failures'],1)
        self.assertEqual(result[2]['window_ontime_completions'],0)
        self.assertEqual(result[2]['cohort_successes_drained_after_full_window'],1)
        self.assertEqual(result[2]['cohort_ontime_yield'],1)

    def test_all_failures_remain_in_denominator(self):
        events=[{'phase':'connected','transition_completed_ns':0},{'phase':'disconnected','transition_completed_ns':100},{'phase':'recovered','transition_completed_ns':200}]
        rows=[{'t_submit_ns':'10','t_reply_ns':'20','route':'local','status':'200'}, {'t_submit_ns':'30','t_reply_ns':'30','route':'rejected','status':'429'}, {'t_submit_ns':'40','t_reply_ns':'60','route':'remote','status':'503'}]
        result=plot.reaggregate(rows,events,300,100)[0]
        self.assertEqual(result['arrivals'],3)
        self.assertEqual(result['cohort_ontime_yield'],1/3)
        self.assertEqual(result['admission_rejected'],1)
        self.assertEqual(result['cohort_failures'],2)

if __name__=='__main__':unittest.main()
