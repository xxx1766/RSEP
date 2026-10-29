"""Strict partition of directly observed request events (all one clock domain)."""
PHASES=['http_enter','submit','enqueue','dispatch','resource_acquired','pool_get','pool_acquired','execute','exit_received','http_result','http_written']
NAMES=['adapter_input','admission','dispatch_wait','resource_wait','handoff','executor_acquisition','binding','execute_to_exit','result_delivery','adapter_output']
def partition(events,row):
    if int(row['status'])!=200 or int(row['bytes_processed'])!=6553600:raise ValueError('request failed or wrong kernel work')
    found=[e for e in events if e['id']==row['id']]
    if len(found)!=len(PHASES) or {e['phase'] for e in found}!=set(PHASES):raise ValueError('missing/duplicate trace phase')
    by={e['phase']:e for e in found}; times=[by[p]['ns'] for p in PHASES]
    if any(b<a for a,b in zip(times,times[1:])):raise ValueError('trace phase order violation')
    stages={n:(b-a)/1e6 for n,a,b in zip(NAMES,times,times[1:])}
    handler=(times[-1]-times[0])/1e6
    external=float(row['latency_ms'])-handler
    service=int(row['worker_service_ns'])/1e6
    ipc=stages['execute_to_exit']-service
    if external<0 or ipc<0:raise ValueError('negative timing residual')
    return dict(id=row['id'],executor=by['pool_acquired']['related'],handler_ms=handler,external_ms=external,worker_ms=service,ipc_residual_ms=ipc,stages_ms=stages)
