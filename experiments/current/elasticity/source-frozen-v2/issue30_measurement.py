"""Pure request and allocation contracts; CPU usage never substitutes for grants."""
import collections,math,statistics


def validate_witness(witness,input_sha256,output_sha256):
 if not isinstance(witness,dict):raise ValueError('missing physical inference witness')
 if witness.get('input_sha256')!=input_sha256:raise ValueError('input mismatch')
 if witness.get('detections')!=10 or witness.get('output_shape')!=[1,3549,85] or witness.get('finite') is not True:raise ValueError('invalid actual model result')
 if witness.get('output_sha256')!=[output_sha256]*10:raise ValueError('one or more actual forward-pass hashes differ')
 affinity=witness.get('affinity',[])
 if len(affinity)<2 or any(type(i) is not int or i not in range(12) for i in affinity):raise ValueError('worker ran outside frozen application CPU mask')
 return witness


def normalize_native_response(row,input_sha256,output_sha256):
 response=row.get('response')
 if not isinstance(response,dict):return None
 if response.get('client_request_id',row['id'])!=row['id']:raise ValueError('client identity mismatch')
 if response.get('status')!='success':return None
 witness=response.get('result_json') or response.get('witness')
 validate_witness(witness,input_sha256,output_sha256)
 if witness.get('request_id')!=response.get('request_id'):raise ValueError('physical routine identity mismatch')
 return witness


def summarize_requests(expected_ids,rows,deadline_ms,duration_s,input_sha256,output_sha256):
 ids=[r['id'] for r in rows]
 if len(ids)!=len(expected_ids) or len(set(ids))!=len(ids) or set(ids)!=set(expected_ids):raise ValueError('missing, extra or duplicate offered request IDs')
 success=timely=scheduled_timely=rejects=0;latencies=[];emission=[];failures=collections.Counter();destinations=collections.Counter()
 for row in rows:
  start=row['actual_submit_monotonic_ns'];end=row['return_monotonic_ns'];planned=row['planned_submit_monotonic_ns']
  if end<start:raise ValueError('negative response interval')
  latency=(end-start)/1e6;emission.append(max(0,(start-planned)/1e6))
  witness=normalize_native_response(row,input_sha256,output_sha256)
  if witness is not None and row.get('http_status')==200:
   success+=1;latencies.append(latency);timely+=latency<=deadline_ms;scheduled_timely+=(end-planned)/1e6<=deadline_ms
   response=row['response'];receiver=response.get('receiver_after_wait',{});destinations[receiver.get('node') or witness.get('node_name') or 'unknown']+=1
  else:
   response=row.get('response') if isinstance(row.get('response'),dict) else {};error=row.get('error') or response.get('error') or response.get('status') or ('http_'+str(row.get('http_status')))
   failures[str(error)]+=1
   rejects+=str(error) in ['no feasible execution destination','request exceeds destination stub capacity']
 return dict(offered=len(rows),unique_ids=len(set(ids)),success=success,on_time=timely,scheduled_on_time=scheduled_timely,admission_rejections=rejects,failed=len(rows)-success,yield_fraction=timely/len(rows),goodput_rps=timely/duration_s,emission_lateness_max_ms=max(emission),emission_lateness_median_ms=statistics.median(emission),latency_p50_ms=statistics.median(latencies) if latencies else None,latency_p95_ms=sorted(latencies)[math.ceil(.95*len(latencies))-1] if latencies else None,failure_types=dict(failures),destinations=dict(destinations))


def additional_allocation(rows,baseline):
 values=[]
 for row in rows:
  if not row.get('valid'):values.append(None);continue
  if 'allocated_cpus' not in row:raise ValueError('CPU usage is not an allocation witness')
  value=row['allocated_cpus']
  if not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:raise ValueError('invalid CPU grant')
  values.append(value-baseline)
 return values


def cpu_quantity(v):
 if isinstance(v,(int,float)):return float(v)
 if v.endswith('m'):return float(v[:-1])/1000
 return float(v)


def extract_allocation(row):
 """Exclusive reservations and executable/borrowed commitments stay distinct."""
 result=dict(begin_ns=row['begin_ns'],end_ns=row['end_ns'],valid=False,allocated_cpus=None,ready_cpus=None)
 if row['source']=='native-local-processors':
  samples=row['samples'];parts=[]
  if len(samples)!=3:return result
  for s in samples:
   p=(s.get('body') or {}).get('local_processors')
   if s.get('http_status')!=200 or p is None:return result
   
   if not 0<=p['reserved_cpus']<=12:raise ValueError('native per-host reservation exceeds 12 CPU budget')
   parts.append(p)
  if len({p['node'] for p in parts})!=3:raise ValueError('native node identity duplicated')
  values={k:sum(p[k] for p in parts) for k in ['reserved_cpus','committed_cpus','borrowed_cpus','physical_occupied_cpus']}
  if values['reserved_cpus']<0 or values['reserved_cpus']>36:raise ValueError('exclusive native reservation exceeds three-host budget')
  return dict(result,valid=True,allocated_cpus=values['reserved_cpus'],**values)
 if row['source']=='kubernetes-api':
  obj=row['objects']
  if obj.get('rc')!=0 or not isinstance(obj.get('body'),dict):return result
  allocated=ready=0;desired=None;uids=[];restarts=0;per_node=collections.Counter()
  for v in obj['body']['items']:
   if v['kind']=='HorizontalPodAutoscaler':desired=v.get('status',{}).get('desiredReplicas')
   if v['kind']!='Pod':continue
   uids.append(v['metadata']['uid']);restarts+=sum(c.get('restartCount',0) for c in v.get('status',{}).get('containerStatuses',[]))
   if not v['spec'].get('nodeName') or v.get('status',{}).get('phase') in ['Succeeded','Failed']:continue
   cpus=sum(cpu_quantity(c['resources']['requests']['cpu']) for c in v['spec']['containers']);allocated+=cpus;per_node[v['spec']['nodeName']]+=cpus
   if not v['metadata'].get('deletionTimestamp') and any(c['type']=='Ready' and c['status']=='True' for c in v.get('status',{}).get('conditions',[])):ready+=cpus
  if allocated>36 or any(v>12 for v in per_node.values()):raise ValueError('Kubernetes application requests exceed frozen host budget')
  return dict(result,valid=True,allocated_cpus=allocated,ready_cpus=ready,desired_replicas=desired,pod_uids=uids,restarts=restarts,per_node_cpus=dict(per_node))
 raise ValueError('unknown allocation source')
