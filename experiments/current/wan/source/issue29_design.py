"""Pilot estimators and common-window metrics; no policy-dependent tuning."""
import collections
import math
import statistics

def context_estimate(rows,events,vm):
 byid={r['id']:r for r in rows};service=[];residual=[];lat=[];coverage=collections.Counter()
 for e in events:
  r=byid.get(e['request_id'])
  if r and r['status']=='success' and e['status']=='success':
   if e['executing_vm']!=vm:raise ValueError('forced context executed elsewhere')
   service.append(e['service_ns']/1e6);residual.append(max(0,r['latency_ms']-(e['service_ns']+e['queue_ns'])/1e6));lat.append(r['latency_ms']);coverage[e['input_sha256']]+=1
 if len(service)<36 or len(coverage)!=4 or min(coverage.values())<8:raise ValueError('insufficient uncensored context coverage')
 return dict(execution_ms=math.ceil(statistics.median(service)),network_ms=math.ceil(statistics.median(residual)),service_median_ms=statistics.median(service),path_residual_median_ms=statistics.median(residual),caller_median_ms=statistics.median(lat),correct=len(lat),within135=sum(x<=135 for x in lat),input_coverage=dict(coverage))

def metrics(rows,start_ns,window_ms):
 if len({r['id'] for r in rows})!=len(rows):raise ValueError('duplicate offer IDs')
 end=start_ns+int((window_ms+135)*1e6)
 successful=[r for r in rows if r['status']=='success'];timely=[r for r in successful if r['latency_ms']<=135];fixed=[r for r in timely if r['reply_unix_ns']<=end]
 lateness=[max(0,(r['submit_unix_ns']-start_ns-r['scheduled_offset_ns'])/1e6) for r in rows]
 return dict(offered=len(rows),unique_ids=len(rows),success=len(successful),cohort_on_time=len(timely),on_time=len(fixed),yield_fraction=len(fixed)/len(rows),goodput_rps=len(fixed)/((window_ms+135)/1000),arrival_window_ms=window_ms,observation_window_ms=window_ms+135,global_window_end_ns=end,emission_lateness_max_ms=max(lateness),emission_lateness_median_ms=statistics.median(lateness),failure_types=dict(collections.Counter(r.get('error') or r['status'] for r in rows if r['status']!='success')))
