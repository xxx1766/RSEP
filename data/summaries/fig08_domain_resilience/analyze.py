#!/usr/bin/env python3
"""All-offer domain study analysis. Host provenance comes from A/B journals.

No framework label establishes physical location. Missing offers remain in all
denominators. Run-level n=3 intervals are unbounded Student t(2), never pooled.
Plots require all 36 qualified runs and a separate passing protocol audit.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics

SECOND=10**9
BYTES=65536*1000
T2=4.302652729911275
SYSTEMS=('tide','ray','kubeedge')


def golden(work):
    if work!={'kind':'cpu','bytes':65536,'iterations':1000}:raise ValueError('kernel differs from frozen SHA workload')
    data=bytearray((i*17+3)%256 for i in range(65536))
    for _ in range(1000):value=hashlib.sha256(data).digest();data[:32]=value
    return int.from_bytes(value[:8],'little')


def load(path):return json.loads(path.read_text())


def jsonl(path):
    records=[]
    for line in path.read_bytes().splitlines():
        if line.strip():records.append(json.loads(line))
    return records


def unwrap(reply):
    if isinstance(reply,dict)and'result_json'in reply:
        reply=reply['result_json']
        if isinstance(reply,str):reply=json.loads(reply)
    return reply if isinstance(reply,dict)else{}


def envelope_ok(reply):
    return not(isinstance(reply,dict)and'result_json'in reply)or(
        not reply.get('error')and reply.get('status')in(None,'success'))


def outcome(record,reply,correct,timely):
    if record is None:return'missing'
    if correct:return'timely'if timely else'valid_late'
    if record.get('http_code')is not None and record['http_code']>=400:return'http_error'
    if record.get('error'):
        text=str(record['error']).lower()
        return'timeout'if'timeout'in text or'timed out'in text else'transport_error'
    if not envelope_ok(record.get('reply'))or reply.get('error'):return'application_error'
    return'invalid_result'


def metric(rows,duration):
    offered=len(rows);correct=sum(r['correct']for r in rows);timely=sum(r['timely']for r in rows)
    latencies=[r['latency_ms']for r in rows if r['correct']and r['latency_ms']is not None]
    categories={name:sum(r.get('outcome')==category for r in rows)for name,category in(
        ('http_errors','http_error'),('timeouts','timeout'),('transport_errors','transport_error'),
        ('application_errors','application_error'),('invalid_results','invalid_result'))}
    return dict(offered=offered,correct=correct,timely=timely,late=correct-timely,**categories,
        failures=offered-correct,missing=sum(r['missing']for r in rows),
        timely_fraction=timely/offered if offered else None,goodput=timely/duration if duration>0 else None,
        deadline_failure_fraction=(offered-timely)/offered if offered else None,
        conditional_mean_latency_ms=statistics.mean(latencies)if latencies else None,
        A_executed=sum(len(r['executions']['A'])for r in rows),
        B_executed=sum(len(r['executions']['B'])for r in rows),
        A_timely=sum(r['timely']and r['physical_host']=='A'for r in rows),
        B_timely=sum(r['timely']and r['physical_host']=='B'for r in rows))


def completions(metrics,rows,left,right):
    returned=[r for r in rows if r['timely']and left<=r['ended_unix_ns']<right]
    metrics['cohort_deadline_goodput']=metrics['goodput']
    valid_returns=[r for r in rows if r['correct']and r['ended_unix_ns']is not None and left<=r['ended_unix_ns']<right]
    metrics.update(timely_returns=len(returned),A_timely_returned=sum(r['physical_host']=='A'for r in returned),
                   B_timely_returned=sum(r['physical_host']=='B'for r in returned),
                   conditional_return_mean_latency_ms=statistics.mean(r['latency_ms']for r in valid_returns)if valid_returns else None,
                   goodput=len(returned)/((right-left)/SECOND)if right>left else None)
    return metrics


def journals(directory):
    mapping={};requests={};problems=[];sources=[]
    for host in ('A','B'):
        for path in sorted((directory/host).rglob('*.jsonl')):
            if 'journal'not in str(path.relative_to(directory/host)).lower():continue
            sources.append(str(path.relative_to(directory)))
            try:records=jsonl(path)
            except(ValueError,OSError)as error:problems.append(f'Unreadable journal {path}: {error}');continue
            for record in records:
                instance=record.get('worker_instance');rid=record.get('request_id')
                if not instance:continue
                if instance in mapping and mapping[instance]!=host:problems.append('Worker instance appears on both physical hosts: '+instance)
                mapping[instance]=host
                if rid and record.get('event')=='result':
                    key=(instance,rid,record.get('worker_start_ns'),record.get('worker_end_ns'))
                    requests[key]=dict(record,physical_host=host)
    return mapping,list(requests.values()),problems,sources


def phase_bounds(spec,directory):
    start=spec['start_unix_ns'];end=start+int(spec.get('duration_s',150)*SECOND)
    if spec['fault']:
        fault=load(directory/'C/fault.json')
        names=('on_start_unix_ns','on_end_unix_ns','off_start_unix_ns','off_end_unix_ns')
        a,b,c,d=[fault[name]for name in names]
        if not start<a<=b<c<=d<end:raise ValueError('actual A-clock fault bounds missing or unordered')
    else:
        on=spec.get('fault_on_s',20);off=spec.get('fault_off_s',110)
        a=b=start+int(on*SECOND);c=d=start+int(off*SECOND)
    return {'normal':(start,a),'transition_on':(a,b),'outage':(b,c),
            'transition_off':(c,d),'recovery':(d,end)}


def phase_at(when,bounds):
    for phase,(left,right)in bounds.items():
        if left<=when<right:return phase
    return'outside'


def b_events(directory,spec,journal_records,rows):
    records=[r for r in journal_records if r['physical_host']=='B']
    worker_instances=sorted({r['worker_instance']for r in records})
    boot=min((r['worker_boot_ns']for r in records if r.get('worker_boot_ns')),default=None)
    first_offer=min((r['scheduled_offset_ns']/SECOND for r in rows if r['executions']['B']),default=None)
    first_return=min(((r['ended_unix_ns']-spec['start_unix_ns'])/SECOND for r in rows
                      if r['timely']and r['physical_host']=='B'),default=None)
    ready=[];offset=None;clock_note=None
    try:
        before=load(directory/'A/clock-B-before.json');after=load(directory/'A/clock-B-after.json')
        for value in(before,after):
            if len(value['samples'])!=20 or value['best']['uncertainty_ns']!=min(x['uncertainty_ns']for x in value['samples']):
                raise ValueError('B clock does not use twenty minimum-RTT brackets')
        uncertainty=(abs(after['best']['offset_ns']-before['best']['offset_ns'])
                     +before['best']['uncertainty_ns']+after['best']['uncertainty_ns'])
        if uncertainty>=10000000:raise ValueError('B conservative clock drift/uncertainty at least 10 ms')
        offset=before['best']['offset_ns'];clock_note={'offset_B_minus_A_ns':offset,'bound_ns':uncertainty}
    except(OSError,ValueError,KeyError)as error:clock_note={'unaligned_reason':str(error)}
    # Observer event files must explicitly name readiness; boot and first use are
    # not silently relabeled as an instance-ready event.
    for path in sorted((directory/'B').rglob('*.jsonl')):
        if not any(word in str(path.relative_to(directory/'B')).lower()for word in ('inventory','event','journal')):continue
        try:entries=jsonl(path)
        except(ValueError,OSError):continue
        for event in entries:
            if event.get('event')in('ready','instance_ready','worker_ready'):
                when=event.get('a_unix_ns')
                if when is None and offset is not None:
                    observed=event.get('observed_unix_ns',event.get('unix_ns'))
                    if observed is not None:when=observed-offset
                ready.append(dict(source=str(path.relative_to(directory)),record=event,
                                  relative_A_s=(when-spec['start_unix_ns'])/SECOND if when else None))
                if event.get('worker_instance')and event['worker_instance']not in worker_instances:
                    worker_instances.append(event['worker_instance'])
    coverage=False;worker_seen=None
    try:
        observations=jsonl(directory/'B/observations.jsonl')
        if offset is not None and observations:
            left=spec['start_unix_ns'];right=left+int(spec.get('duration_s',150)*SECOND)
            coverage=(observations[0]['unix_ns']-offset<=left+SECOND and observations[-1]['unix_ns']-offset>=right-SECOND)
            during=[x for x in observations if left<=x['unix_ns']-offset<right]
            worker_seen=any(x.get('worker_count',len(x.get('workers',[])))>0 for x in during)if during else None
    except(OSError,ValueError,KeyError):pass
    return dict(worker_instances=worker_instances,worker_boot_B_unix_ns=boot,
        boot_clock_note='B wall-clock boot timestamp; not a readiness or A-clock timestamp.',
        first_B_offer_s=first_offer,first_B_timely_return_s=first_return,ready_events=ready,B_clock=clock_note,
        observation_coverage_complete=coverage,worker_seen_during_window=worker_seen)


def analyze_run(directory):
    directory=Path(directory);failures=[];warnings=[]
    spec=load(directory/'A/spec.json');checksum=golden(spec['workload'])
    if spec.get('initial')not in('warm','cold')or spec.get('repeat')not in(1,2,3):failures.append('Invalid initial state/repeat')
    if spec.get('deadline_ms')!=500 or spec.get('timeout_s')!=2:failures.append('Deadline/timeout differs from frozen protocol')
    arrivals=spec['arrivals'];ids=[r['id']for r in arrivals];duration=spec.get('duration_s',150)
    if len(ids)!=len(set(ids)):failures.append('Duplicate offered IDs')
    if any(not 0<=r['offset_ns']<duration*SECOND for r in arrivals):failures.append('Offer outside planned observation window')
    try:raw=jsonl(directory/'A/requests.jsonl')
    except(OSError,ValueError)as error:raw=[];failures.append('Missing/unreadable client records: '+str(error))
    byid={}
    for record in raw:
        rid=record.get('id')
        if rid in byid:failures.append('Duplicate client outcome: '+str(rid))
        byid[rid]=record
    unexpected=sorted(str(rid)for rid in set(byid)-set(ids))
    if unexpected:failures.append('Unplanned returned offer IDs: '+','.join(unexpected))
    mapping,executions,journal_problems,journal_sources=journals(directory);failures.extend(journal_problems)
    try:bounds=phase_bounds(spec,directory)
    except(OSError,ValueError,KeyError)as error:
        failures.append('Phase evidence: '+str(error));bounds={'unqualified':(spec['start_unix_ns'],spec['start_unix_ns']+int(duration*SECOND))}
    rows=[];crossings=[]
    for offer in arrivals:
        rid=offer['id'];record=byid.get(rid);reply={};latency=None;ended=None;correct=False;host=None
        own=[e for e in executions if e['request_id']==rid]
        observed={'A':[],'B':[]}
        for execution in own:observed[execution['physical_host']].append(execution['worker_instance'])
        if record is None:failures.append('Missing offered result record: '+rid)
        else:
            if record.get('scheduled_offset_ns')!=offer['offset_ns']:failures.append('Scheduled offset changed: '+rid)
            try:
                reply=unwrap(record.get('reply'));result=reply.get('result',{})
                latency=(record['ended_monotonic_ns']-record['scheduled_monotonic_ns'])/1e6
                ended=record['ended_unix_ns']
                if record['started_unix_ns']<spec['start_unix_ns']or record['started_unix_ns']>=spec['start_unix_ns']+duration*SECOND:
                    failures.append('Actual client submission outside observation window: '+rid)
                correct=(record.get('http_code')==200 and not record.get('error')and not reply.get('error')and envelope_ok(record.get('reply'))
                    and reply.get('request_id')==rid and bool(reply.get('worker_instance'))
                    and result.get('checksum')==checksum and result.get('bytes_processed')==BYTES)
                instance=reply.get('worker_instance')
                matches=[e for e in own if e['worker_instance']==instance]
                host=mapping.get(instance)if matches else None
                if correct and host is None:failures.append('Correct return lacks physical worker/request journal join: '+rid)
                if matches and correct and not any(e.get('result')==result
                    and e.get('worker_start_ns')==reply.get('worker_start_ns')
                    and e.get('worker_end_ns')==reply.get('worker_end_ns')for e in matches):
                    failures.append('Returned result differs from physical worker journal: '+rid)
            except(ValueError,KeyError,TypeError)as error:failures.append('Malformed client record '+rid+': '+str(error))
        timely=bool(correct and latency is not None and 0<=latency<=spec['deadline_ms'])
        scheduled=spec['start_unix_ns']+offer['offset_ns'];phase=phase_at(scheduled,bounds)
        end_phase=phase_at(ended,bounds)if ended is not None else None
        if end_phase is not None and end_phase!=phase:crossings.append(rid)
        rows.append(dict(id=rid,scheduled_offset_ns=offer['offset_ns'],phase=phase,
            completion_phase=end_phase,ended_unix_ns=ended,correct=bool(correct),timely=timely,
            missing=record is None,latency_ms=latency,physical_host=host,outcome=outcome(record,reply,correct,timely),
            http_code=record.get('http_code')if record else None,
            worker_instance=reply.get('worker_instance'),claimed_node=reply.get('node'),executions=observed,
            service_ms=(reply.get('worker_end_monotonic_ns',0)-reply.get('worker_start_monotonic_ns',0))/1e6 if reply else None))
        if record and(record.get('correct')!=bool(correct)or record.get('timely')!=timely):warnings.append('Driver flags differ from independent outcome verification: '+rid)
        if len(own)>1:warnings.append('Multiple actual worker executions observed for one offered request: '+rid)
    try:
        if load(directory/'A/driver-exit.json')['rc']!=0:failures.append('Driver exit not zero')
        if load(directory/'A/driver.json').get('driver_valid')is not True:failures.append('Driver protocol qualification false')
    except(OSError,ValueError,KeyError)as error:failures.append('Driver receipts missing: '+str(error))
    phases={name:dict(completions(metric([r for r in rows if r['phase']==name],(right-left)/SECOND),rows,left,right),
                      start_relative_s=(left-spec['start_unix_ns'])/SECOND,end_relative_s=(right-spec['start_unix_ns'])/SECOND)
            for name,(left,right)in bounds.items()}
    bins=[dict(second=i,**completions(metric([r for r in rows if i*SECOND<=r['scheduled_offset_ns']<(i+1)*SECOND],1),
               rows,spec['start_unix_ns']+i*SECOND,spec['start_unix_ns']+(i+1)*SECOND))
          for i in range(math.ceil(duration))]
    return dict(run_id=directory.name,system=spec['system'],initial=spec['initial'],fault=spec['fault'],repeat=spec['repeat'],
        status='PASS'if not failures else'INCOMPLETE',failures=failures,warnings=warnings,
        spec=spec,totals=completions(metric(rows,duration),rows,spec['start_unix_ns'],spec['start_unix_ns']+int(duration*SECOND)),
        phases=phases,seconds=bins,requests=rows,
        phase_crossings=crossings,unexpected_ids=unexpected,journal_sources=journal_sources,
        B_events=b_events(directory,spec,executions,rows),
        execution_count_time_basis='scheduled offer cohorts; physical host joined from actual worker journals',
        connected_phase_note='Matched planned outage window; no fault injected.'if not spec['fault']else'Actual fault brackets on A clock.')


def interval(values):
    mean=statistics.mean(values)
    half=T2*statistics.stdev(values)/math.sqrt(3)
    return mean,[mean-half,mean+half]


def summarize(runs):
    groups=[];paired=[]
    metrics=('timely_fraction','goodput','deadline_failure_fraction','conditional_mean_latency_ms')
    def value(run,scope,key):
        return(run.get('totals',{})if scope=='overall'else run.get('phases',{}).get(scope,{})).get(key)
    for system in sorted({r['system']for r in runs}):
        for initial in('warm','cold'):
            for scope in('overall','normal','outage','recovery'):
                for key in metrics:
                    for fault in(False,True):
                        selected=[r for r in runs if(r['system'],r['initial'],r['fault'])==(system,initial,fault)]
                        if not selected:continue
                        values=[value(r,scope,key)for r in selected]
                        qualified=(len(selected)==3 and {r['repeat']for r in selected}=={1,2,3}
                                   and all(r['status']=='PASS'for r in selected)and all(v is not None for v in values))
                        mean,ci=interval(values)if qualified else(None,None)
                        groups.append(dict(system=system,initial=initial,fault=fault,scope=scope,metric=key,
                            n=len(selected),qualified=qualified,values=values,mean=mean,ci95=ci,
                            inference_unit='run',interval='unbounded t(2), 95%'))
                    pairs=[];qualified=True
                    for repeat in(1,2,3):
                        controls=[r for r in runs if(r['system'],r['initial'],r['fault'],r['repeat'])==(system,initial,False,repeat)]
                        faults=[r for r in runs if(r['system'],r['initial'],r['fault'],r['repeat'])==(system,initial,True,repeat)]
                        if len(controls)!=1 or len(faults)!=1:qualified=False;continue
                        a,b=controls[0],faults[0];left,right=value(a,scope,key),value(b,scope,key)
                        if a['status']!='PASS'or b['status']!='PASS':qualified=False
                        if [x['offset_ns']for x in a['spec']['arrivals']]!=[x['offset_ns']for x in b['spec']['arrivals']]:qualified=False
                        if left is None or right is None:qualified=False;continue
                        pairs.append(right-left)
                    if pairs:
                        mean,ci=interval(pairs)if qualified and len(pairs)==3 else(None,None)
                        paired.append(dict(system=system,initial=initial,scope=scope,metric=key,contrast='fault minus connected',
                            n=len(pairs),qualified=qualified and len(pairs)==3,values=pairs,mean=mean,ci95=ci,
                            inference_unit='paired repetition',interval='unbounded t(2), 95%'))
    return dict(groups=groups,paired=paired)


def analyze_campaign(root):
    root=Path(root);order=load(root/'protocol/order.json');runs=[]
    for item in order:
        directory=root/'runs'/item['run_id']
        try:runs.append(analyze_run(directory))
        except(OSError,ValueError,KeyError,TypeError)as error:
            runs.append(dict(run_id=item['run_id'],system=item['system'],initial=item['initial'],fault=item['fault'],repeat=item['repeat'],
                status='INCOMPLETE',failures=['Missing/unreadable treatment data: '+str(error)],warnings=[],
                totals={'timely_fraction':None},spec={'arrivals':[]}))
    expected={(s,i,f,r)for s in SYSTEMS for i in('warm','cold')for f in(False,True)for r in(1,2,3)}
    cells=[(r['system'],r['initial'],r['fault'],r['repeat'])for r in runs]
    matrix_ok=len(cells)==len(set(cells))==36 and set(cells)==expected
    return dict(status='PASS'if matrix_ok and all(r['status']=='PASS'for r in runs)else'INCOMPLETE',
        expected_runs=36,runs=runs,matrix_complete=matrix_ok,statistics=summarize(runs),
        policy='All planned treatments and all offers retained; unqualified cells cannot form a complete n=3 interval.')


def readiness_note(initial,runs):
    times=[e['relative_A_s']for r in runs for e in r['B_events']['ready_events']if e['relative_A_s']is not None]
    visible=[t for t in times if 0<=t<=150]
    pre=sum(any(e['relative_A_s']is not None and e['relative_A_s']<0 for e in r['B_events']['ready_events'])for r in runs)
    if initial=='warm'and pre:return visible,f'B ready before t=0 ({pre}/{len(runs)} runs)'
    if visible:return visible,''
    stable=all(r['B_events'].get('B_clock',{}).get('bound_ns',10000000)<10000000 for r in runs)
    if not stable:return visible,'B readiness clock unavailable'
    covered=all(r['B_events'].get('observation_coverage_complete')for r in runs)
    if any(r['B_events'].get('worker_instances')or r['B_events'].get('worker_seen_during_window')for r in runs):
        return visible,'B instance seen; ready event not observed'
    if covered and all(r['B_events'].get('worker_seen_during_window')is False for r in runs):
        return visible,f'No B instance observed ({len(runs)}/{len(runs)} runs)'
    return visible,'B readiness not established; observation coverage incomplete'


def decorate(ax,initial,runs):
    on=statistics.mean(r['phases']['outage']['start_relative_s']for r in runs)
    off=statistics.mean(r['phases']['outage']['end_relative_s']for r in runs)
    ax.axvspan(on,off,color='#cc4444',alpha=.10,label='C packet drop')
    markers,note=readiness_note(initial,runs)
    for when in markers:ax.axvline(when,color='#38934a',alpha=.45,linestyle=':')
    if note:ax.text(.02,.97,note,transform=ax.transAxes,fontsize=7,va='top')
    ax.set_xlim(0,150);ax.grid(alpha=.18)


def plot(report,directory):
    if report['status']!='PASS':raise ValueError('No plots before all 36 treatments qualify')
    import matplotlib.pyplot as plt
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    figure,axes=plt.subplots(3,2,figsize=(12,9),sharex=True)
    companion,events=plt.subplots(3,2,figsize=(12,10),sharex=True)
    x=range(150)
    edges=range(151)
    def bins(values):return values+[values[-1]]
    for row,system in enumerate(SYSTEMS):
        for column,initial in enumerate(('warm','cold')):
            ax=axes[row,column];event=events[row,column]
            runs=[r for r in report['runs']if(r['system'],r['initial'],r['fault'])==(system,initial,True)]
            connected=[r for r in report['runs']if(r['system'],r['initial'],r['fault'])==(system,initial,False)]
            def series(key):return[statistics.mean(r['seconds'][i][key]for r in runs)for i in x]
            ax.stackplot(edges,bins(series('A_timely_returned')),bins(series('B_timely_returned')),
                         labels=['A timely returns','B timely returns'],colors=['#2070b5','#dc8024'],alpha=.65,step='post')
            ax.step(edges,bins(series('offered')),label='Offered',color='#777777',linewidth=1,where='post')
            ax.step(edges,bins([statistics.mean(r['seconds'][i]['timely_returns']for r in connected)for i in x]),
                    color='black',label='Connected timely total',linestyle='--',linewidth=1,where='post')
            error_keys=('http_errors','timeouts','transport_errors','application_errors','invalid_results','late','missing')
            errors=[series(key)for key in error_keys]
            event.stackplot(edges,bins(errors[0]),bins(errors[1]),
                bins([sum(values)for values in zip(*errors[2:5])]),bins(errors[5]),bins(errors[6]),
                labels=['HTTP errors','Timeouts','Other failures','Valid late','Missing record'],
                colors=['#bd3434','#8c54a1','#777777','#dcab30','#222222'],alpha=.65,step='post')
            latency=event.twinx();conditional=[]
            for i in x:
                values=[r['seconds'][i]['conditional_return_mean_latency_ms']for r in runs
                        if r['seconds'][i]['conditional_return_mean_latency_ms']is not None]
                conditional.append(statistics.mean(values)if values else float('nan'))
            latency.plot([i+.5 for i in x],conditional,color='#2070b5',linewidth=.9,label='Conditional valid-return latency')
            latency.axhline(500,color='#2070b5',linestyle=':',linewidth=.7)
            latency.set_ylabel('valid-return latency (ms)',fontsize=8);latency.set_xlim(0,150)
            for axis in(ax,event):
                decorate(axis,initial,runs);axis.set_title(f'{system}, {initial} (3 runs)')
                if row==2:axis.set_xlabel('seconds from offered-window start')
            ax.set_ylabel('actual timely returns/s')
            event.set_ylabel('adverse outcomes/s\nby scheduled arrival',fontsize=8)
    axes[0,0].legend(fontsize=7,loc='upper right');events[0,0].legend(fontsize=6,loc='upper right')
    companion.supxlabel('Latency uses actual completion-second bins; errors/late/missing use all offered arrival cohorts. Gaps have no valid return. Available run means are descriptive.',fontsize=8)
    for current,name in((figure,'domain-timeline'),(companion,'domain-latency-errors')):
        current.tight_layout()
        for suffix in('pdf','png'):current.savefig(directory/(name+'.'+suffix),dpi=200)
        plt.close(current)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign',required=True,type=Path);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--plot-dir',type=Path);parser.add_argument('--protocol-audit',type=Path)
    args=parser.parse_args();report=analyze_campaign(args.campaign)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    if args.plot_dir:
        if not args.protocol_audit or load(args.protocol_audit).get('status')!='PASS':raise ValueError('Passing independent protocol audit required before plotting')
        plot(report,args.plot_dir)
    print(json.dumps(dict(status=report['status'],runs=len(report['runs']))))
    return 0 if report['status']=='PASS'else 1


if __name__=='__main__':raise SystemExit(main())
