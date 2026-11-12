import unittest
from unittest.mock import patch
from issue29_capacity import capacity_jobs
from issue29_design import metrics
from issue23_lf_client import LocalFirst

class Pilot(unittest.TestCase):
 def test_status_transport_retry_never_relaunches(self):
  import issue29_path as jobs
  with patch.object(jobs,'remote',side_effect=[RuntimeError('command failed (255): transport'),'{"phase":"finished","exit_code":0}']) as remote,patch.object(jobs.time,'sleep'):
   r=jobs.status(('vm',True,'/owned/job','/owned/helper.py'))
   self.assertEqual(r['phase'],'finished');self.assertEqual(remote.call_count,2)
   self.assertTrue(all(' status ' in call.args[2] for call in remote.call_args_list))
 def test_remote_qmp_code_compiles_before_transport(self):
  import issue29_ops as ops
  def reply(host,code,**kwargs):
   compile(code,'qmp-remote','exec')
   return '{"return": ""}'
  with patch.object(ops.p,'py',side_effect=reply):
   self.assertEqual(ops.qmp('NJ-k1','i23-l01','query-status'),{'return':''})
 def test_six_node_balanced_capacity(self):
  jobs=capacity_jobs(6);self.assertEqual(len(jobs),3072);self.assertEqual(len({(n,i) for n,i,_ in jobs}),3072)
  for n in range(6):
   for image in range(4):self.assertEqual(sum(a==n and c==image for a,_,c in jobs),128)
 def test_native_and_custom_policy_distinction(self):
  p=LocalFirst(list('abcdef'),3,2);indices=[p.reserve() for _ in range(12)]
  self.assertTrue(all(x<3 for x in indices[:6]));self.assertTrue(all(x>=3 for x in indices[6:]))
  for x in indices:p.release(x)
  self.assertEqual(p.snapshot(),[0]*6)
 def test_common_window_excludes_delayed_global_tail(self):
  base=dict(status='success',latency_ms=100,scheduled_offset_ns=0,on_time=True)
  rows=[dict(base,id='a',submit_unix_ns=0,reply_unix_ns=100000000),dict(base,id='b',submit_unix_ns=1100000000,reply_unix_ns=1200000000)]
  r=metrics(rows,0,1000);self.assertEqual(r['cohort_on_time'],2);self.assertEqual(r['on_time'],1);self.assertEqual(r['offered'],2)

if __name__=='__main__':unittest.main()
