"""Balanced saturated-capacity accounting; never infer capacity from goodput."""
import collections


def capacity_jobs(scale):
    if scale!=6:raise ValueError('frozen active scale required')
    return [(node,index,index%4) for index in range(512) for node in range(scale)]


def node_capacity(events):
    if len(events)!=512 or len({e['request_id'] for e in events})!=512 or any(e['status']!='success' for e in events):
        raise ValueError('exactly512 successful unique physical executions required')
    coverage=collections.Counter(e['input_sha256'] for e in events)
    if len(coverage)!=4 or set(coverage.values())!={128}:raise ValueError('balanced per-node input coverage required')
    duration=max(e['completed_unix_ns'] for e in events)-min(e['received_unix_ns'] for e in events)
    service=sum(e['service_ns'] for e in events)
    if duration<=0:raise ValueError('positive observation span required')
    busy=service/duration
    if not .90<=busy<=1.01:raise ValueError('not a saturated serialized application block')
    return dict(capacity_rps=512/(duration/1e9),span_ns=duration,service_ns=service,busy_fraction=busy,input_coverage=dict(coverage))
