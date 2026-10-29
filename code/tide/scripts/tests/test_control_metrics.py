import importlib.util
from pathlib import Path
import unittest
import tempfile
P = Path(__file__).parents[1] / 'revision' / 'control_metrics.py'

class MetricsTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(P.exists(), 'control-plane CPU accounting is not implemented')
        spec = importlib.util.spec_from_file_location('metrics', P)
        self.m = importlib.util.module_from_spec(spec); spec.loader.exec_module(self.m)

    def test_cpu_is_one_core_percent_and_pid_reuse_is_rejected(self):
        a = dict(pid=7, start=11, ticks=100, rss_kib=1000, pss_kib=900)
        b = dict(a, ticks=125)
        self.assertEqual(self.m.cpu_delta(a, b, 0.5, 100), 50)
        with self.assertRaises(ValueError):
            self.m.cpu_delta(a, dict(b, start=12), .5, 100)
        with self.assertRaises(ValueError):
            self.m.cpu_delta(a, dict(b, ticks=99), .5, 100)

    def test_group_summary_uses_time_weighting(self):
        def snap(t,ticks):
            return dict(monotonic_s=t,processes=[dict(pid=7,start=10,ticks=ticks,group='core',rss_kib=1024,pss_kib=512)])
        result=self.m.summarize_samples([snap(0,0),snap(1,50),snap(3,100)],0,3,100)
        self.assertAlmostEqual(result['core']['cpu_pct'],100/3)
        self.assertEqual(result['core']['rss_mib'],1)
        self.assertEqual(result['core']['pss_mib'],.5)
        self.assertEqual(result['core']['cpu_s'],1)

    def test_cgroup_controller_file_can_be_inherited(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); leaf=root/'user.slice'/'session.scope'; leaf.mkdir(parents=True)
            (root/'cpuset.cpus.effective').write_text('0-15\n')
            self.assertEqual(self.m.inherited_cgroup_value(leaf,'cpuset.cpus.effective',root), ('0-15',str(root)))
            self.assertEqual(self.m.inherited_cgroup_value(leaf,'cpu.max',root), (None,None))

    def test_kind_init_scope_is_not_container_root(self):
        self.assertEqual(self.m.container_root('0::/system.slice/nerdctl-abc.scope/init.scope'), '/system.slice/nerdctl-abc.scope')

    def test_stat_handles_spaces_and_parentheses_in_comm(self):
        fields = ['S', '1'] + ['0'] * 9 + ['12', '3'] + ['0'] * 6 + ['123', '0', '4']
        r = self.m.parse_stat('7 (worker (cpu)) ' + ' '.join(fields), 4096)
        self.assertEqual((r['pid'], r['ppid'], r['ticks'], r['start'], r['rss_kib']), (7, 1, 15, 123, 16))

if __name__ == '__main__': unittest.main()
