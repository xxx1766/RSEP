import unittest
from issue30_measurement import validate_witness, summarize_requests, additional_allocation, normalize_native_response

GOLD='a'*64;INPUT='b'*64

def witness():
 return dict(input_sha256=INPUT,detections=10,output_shape=[1,3549,85],finite=True,output_sha256=[GOLD]*10,request_id='native-id',affinity=[0,1])

def request(ident='r0',status='success',latency_ms=100):
 return dict(id=ident,actual_submit_monotonic_ns=1000000000,planned_submit_monotonic_ns=1000000000,return_monotonic_ns=1000000000+int(latency_ms*1e6),http_status=200,response=dict(status=status,request_id='native-id',client_request_id=ident,result_json=witness()),error=None)

class Measurement(unittest.TestCase):
 def test_checks_every_forward_pass(self):
  w=witness();w['output_sha256'][-1]='c'*64
  with self.assertRaises(ValueError):validate_witness(w,INPUT,GOLD)
 def test_nonfinite_result_is_not_success(self):
  w=witness();w['finite']=False
  with self.assertRaises(ValueError):validate_witness(w,INPUT,GOLD)
 def test_missing_offered_id_is_not_a_successful_run(self):
  with self.assertRaises(ValueError):summarize_requests(['r0','r1'],[request()],350,1,INPUT,GOLD)
 def test_admission_rejection_remains_offered(self):
  r=request('r1',status='failure');r['response']['error']='no feasible execution destination';r['response']['result_json']=None
  s=summarize_requests(['r0','r1'],[request(),r],350,1,INPUT,GOLD)
  self.assertEqual((s['offered'],s['success'],s['on_time'],s['admission_rejections']),(2,1,1,1));self.assertEqual(s['yield_fraction'],.5)
 def test_late_emission_is_reported_separately(self):
  r=request();r['planned_submit_monotonic_ns']=0;s=summarize_requests(['r0'],[r],350,1,INPUT,GOLD)
  self.assertEqual(s['on_time'],1);self.assertEqual(s['scheduled_on_time'],0);self.assertEqual(s['emission_lateness_max_ms'],1000)
 def test_native_adapter_witness_identity(self):
  r=request();r['response']['client_request_id']='other'
  with self.assertRaises(ValueError):normalize_native_response(r,INPUT,GOLD)
 def test_usage_is_not_allocation(self):
  with self.assertRaises(ValueError):additional_allocation([{'valid':True,'cpu_usage':9}],0)
 def test_missing_sample_stays_gap(self):
  v=additional_allocation([{'valid':True,'allocated_cpus':4},{'valid':False,'allocated_cpus':99},{'valid':True,'allocated_cpus':8}],4)
  self.assertEqual(v,[0,None,4])

if __name__=='__main__':unittest.main()

class AllocationContract(unittest.TestCase):
 def test_borrowed_commitments_do_not_create_new_exclusive_cpu(self):
  from issue30_measurement import extract_allocation
  p=dict(reserved_cpus=2,committed_cpus=6,borrowed_cpus=4,physical_occupied_cpus=2)
  row=dict(source='native-local-processors',begin_ns=0,end_ns=100,samples=[dict(http_status=200,body=dict(local_processors=dict(p,node=str(i)))) for i in range(3)])
  x=extract_allocation(row)
  self.assertEqual(x['allocated_cpus'],6)
  self.assertEqual(x['committed_cpus'],18)
 def test_pending_pod_is_not_scheduled_or_ready_capacity(self):
  from issue30_measurement import extract_allocation
  pod=dict(kind='Pod',metadata=dict(uid='p'),spec=dict(containers=[dict(resources=dict(requests=dict(cpu='2')))]),status=dict(phase='Pending'))
  row=dict(source='kubernetes-api',begin_ns=0,end_ns=100,objects=dict(rc=0,body=dict(items=[pod])))
  x=extract_allocation(row);self.assertEqual(x['allocated_cpus'],0);self.assertEqual(x['ready_cpus'],0)
