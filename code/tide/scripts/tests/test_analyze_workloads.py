import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'revision/analyze_workloads.py'
spec = importlib.util.spec_from_file_location('analyze_workloads', SCRIPT)
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)


class ResourceTests(unittest.TestCase):
    def setUp(self):
        # Actual run_checked schema, deliberately irregular sample spacing.
        self.epoch = 1790575724757337490
        self.samples = [
            {'unix_ns': self.epoch + offset, 'tide_parent_cpu_usage_usec': 10_000_000 + offset / 1000,
             'workload_cpu_ticks': 1000 + offset / 10_000_000,
             'process_cpu_ticks': 1000 + offset / 100_000_000,
             'process_rss_bytes': 4 * 1024**2}
            for offset in (-100_000_000, 400_000_000, 1_100_000_000)
        ]

    def test_one_core_equals_fifty_percent_of_two_core_budget(self):
        tide = a.counter_percent(self.samples, 'tide_parent_cpu_usage_usec', self.epoch,
                                 self.epoch + 10**9, units_per_second=10**6, cpu_budget=2)
        baseline = a.counter_percent(self.samples, 'workload_cpu_ticks', self.epoch,
                                     self.epoch + 10**9, units_per_second=100, cpu_budget=2)
        controller = a.counter_percent(self.samples, 'process_cpu_ticks', self.epoch,
                                       self.epoch + 10**9, units_per_second=100, cpu_budget=1)
        self.assertAlmostEqual(tide, 50)
        self.assertAlmostEqual(baseline, 50)
        self.assertAlmostEqual(controller, 10)

    def test_no_extrapolation_at_either_boundary(self):
        for start, end in [(self.epoch-200_000_000, self.epoch+10**9),
                           (self.epoch, self.epoch+1_200_000_000)]:
            with self.assertRaisesRegex(ValueError, 'bracket'):
                a.counter_percent(self.samples, 'workload_cpu_ticks', start, end, 100, 2)
        with self.assertRaisesRegex(ValueError, 'bracket'):
            a.counter_percent(self.samples[:-1], 'workload_cpu_ticks', self.epoch, self.epoch+10**9, 100, 2)

    def test_counter_reset_is_rejected(self):
        self.samples[1]['workload_cpu_ticks'] = 0
        with self.assertRaisesRegex(ValueError, 'decreas'):
            a.counter_percent(self.samples, 'workload_cpu_ticks', self.epoch, self.epoch+10**9, 100, 2)

    def test_gauge_mean_uses_time_not_number_of_samples(self):
        samples = [{'unix_ns': 0, 'rss': 0}, {'unix_ns': 10**9, 'rss': 10},
                   {'unix_ns': 3*10**9, 'rss': 10}]
        mean, maximum = a.gauge_summary(samples, 'rss', 0, 3*10**9)
        self.assertAlmostEqual(mean, 25/3)
        self.assertEqual(maximum, 10)

    def test_ci_uses_run_means_not_request_pooling(self):
        result = a.run_mean_ci([1, 2, 3])
        self.assertEqual(result['n'], 3)
        self.assertEqual(result['mean'], 2)
        # t_.975,df2 * sample_std/sqrt(3) = 2.4841377...
        self.assertAlmostEqual(result['ci95_high']-2, 2.48413771175, places=8)
        one = a.run_mean_ci([4])
        self.assertIsNone(one['ci95_low'])

    def test_completed_manifest_hash_and_exit_checks(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path/'raw.csv').write_text('a,b\n1,2\n')
            manifest = {'status':'completed', 'exit_code':0, 'timed_out':False,
                        'files_sha256':{'raw.csv':hashlib.sha256((path/'raw.csv').read_bytes()).hexdigest()}}
            a.verify_outer_manifest(path, manifest)
            manifest['exit_code'] = 7
            with self.assertRaises(ValueError): a.verify_outer_manifest(path, manifest)
            manifest['exit_code'] = 0
            (path/'raw.csv').write_text('changed')
            with self.assertRaisesRegex(ValueError, 'hash'): a.verify_outer_manifest(path, manifest)


class MixedQoSTests(unittest.TestCase):
    def fixture(self):
        run = {'qos_classes_ms':[100,500], 'measurement_start_ns':0,
               'measurement_end_ns':10**9}
        records=[]
        for i,(latency,status) in enumerate([(80,200),(600,200),(100,500),(450,200)]):
            deadline=[100,500][i%2]
            records.append(dict(req_id=str(i), t_submit_ns=i*200_000_000,
                                t_reply_ns=i*200_000_000+latency*10**6,
                                latency_ms=latency, status=status, qos_ms=str(deadline),
                                fn_name=f'cpu-qos{deadline}'))
        return records,run

    def test_classes_use_same_window_and_do_not_pool_deadlines(self):
        records,run=self.fixture()
        classes=a.qos_class_metrics(records,[100,500],'cpu',run)
        self.assertEqual([c['qos_ms'] for c in classes],[100,500])
        self.assertEqual([c['n_eligible'] for c in classes],[2,2])
        self.assertEqual([c['on_time_goodput'] for c in classes],[1,0])
        self.assertEqual([c['on_time_yield'] for c in classes],[.5,.5])
        self.assertEqual([c['n_late'] for c in classes],[0,1])
        overall=a.metrics.aggregate_records(records,manifest=run)
        self.assertEqual(sum(c['on_time_goodput'] for c in classes),overall['on_time_goodput'])
        self.assertEqual(classes[1]['drained_successes'],1)

    def test_mixed_matrix_uses_per_job_system_and_frozen_bin_directory(self):
        import csv
        import hashlib
        records, metadata = self.fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'bin').mkdir()
            digest=lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
            binaries={}
            for name in ('tide','http','worker'):
                (root/'bin'/name).write_text(name)
                binaries[name]=digest(root/'bin'/name)
            config={'qos_ms':500}
            (root/'cpu_config.json').write_text(json.dumps(config))
            work={'kind':'cpu','bytes':64,'iterations':1}
            jobs=[];epoch=1790575724757337490
            for system in ('tide','bare','kubernetes'):
                jobroot=root/system/'rps4/run1'; measured=jobroot/'measurement'
                (measured/'run').mkdir(parents=True)
                command=[f'/remote/bin/{"tide" if system=="tide" else "http"}']
                run={**metadata,'status':'completed','requests':4,'rps':4,'cpus':2,
                     'workload':work,'argv':command,'pid':100,'successes':3,
                     'measurement_start_unix_ns':epoch,'stub_with_initial_executor_create_ns':1000,
                     'effective_config':{**config,'qos_by_function_ms':{'cpu-qos100':100,'cpu-qos500':500}}}
                (measured/'run/run.json').write_text(json.dumps(run))
                fields=list(records[0])+['submit_call_ns']
                with (measured/'run/raw.csv').open('w') as f:
                    writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
                    writer.writerows({**r,'submit_call_ns':1000} for r in records)
                samples=[{'unix_ns':epoch+offset,'monotonic_ns':10**12+offset,
                          'tide_parent_cpu_usage_usec':10_000_000+offset/1000,
                          'workload_cpu_ticks':1000+offset/10_000_000,
                          'process_cpu_ticks':1000+offset/100_000_000,
                          'process_rss_bytes':4*1024**2,'workload_rss_bytes':8*1024**2,
                          'tide_parent_memory_bytes':12*1024**2}
                         for offset in (-100_000_000,400_000_000,1_100_000_000)]
                (measured/'samples.jsonl').write_text('\n'.join(json.dumps(s) for s in samples)+'\n')
                outer={'status':'completed','exit_code':0,'timed_out':False,'pid':100,
                       'binary_sha256':binaries['tide' if system=='tide' else 'http'],
                       'command':command,'clock_ticks_per_second':100,'workload_pid':1000,
                       'started_unix_ns':epoch-200_000_000,'finished_unix_ns':epoch+1_200_000_000,
                       'files_sha256':{name:digest(measured/name) for name in ('run/raw.csv','run/run.json','samples.jsonl')}}
                (measured/'manifest.json').write_text(json.dumps(outer))
                job={'system':system,'workload':'mixed-qos','rps':4,'repeat':1,'requests':4,
                     'status':'completed','path':str(jobroot.relative_to(root)),
                     'command':['python','run_checked','--',*command],'successes':3}
                if system!='tide':
                    setup={'worker_pid':1000,'gomaxprocs':1,'threads_affinity':{'1000':[0,1]},'worker_ready_observed_ns':1000}
                    (jobroot/'setup.json').write_text(json.dumps(setup));job['setup_sha256']=digest(jobroot/'setup.json')
                jobs.append(job)
            matrix={'status':'completed','cpus':2,'repeats':1,'duration_s':1,'jobs':jobs,
                    'calibration':{'workload':work},'classes_ms':[100,500],
                    'binary_sha256':binaries,'config_sha256':digest(root/'cpu_config.json')}
            (root/'matrix.json').write_text(json.dumps(matrix))
            rows,series,audit,classes=a.audit_matrix(root,['tide','bare','kubernetes'],[4],mixed=True)
            self.assertEqual(audit['validated_jobs'],3)
            self.assertEqual(len(classes),6)
            self.assertEqual({r['worker_cpu_pct_2cpu'] for r in rows},{50})
            self.assertIn('tide_cgroup_memory_mean_mib',next(r for r in rows if r['system']=='tide'))
            self.assertNotIn('worker_rss_mean_mib',next(r for r in rows if r['system']=='tide'))

    def test_class_identity_and_metadata_must_agree(self):
        for key,value in [('qos_ms','500'),('fn_name','cpu-qos500')]:
            records,run=self.fixture()
            records[0][key]=value
            with self.assertRaisesRegex(ValueError,'class'):
                a.qos_class_metrics(records,[100,500],'cpu',run)
        records,run=self.fixture();run['qos_classes_ms']=[500,100]
        with self.assertRaisesRegex(ValueError,'class'):
            a.qos_class_metrics(records,[100,500],'cpu',run)


if __name__ == '__main__':
    unittest.main()
