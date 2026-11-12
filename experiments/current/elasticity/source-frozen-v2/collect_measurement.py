#!/usr/bin/env python3
"""Open-loop raw JPEG collection with independent native reservation sampling.

Only this CLI initiates HTTP; importing and testing pure helpers does not.
All scheduled requests receive terminal records. Client terminality is distinct
from native backend drain, which is reported conservatively from raw state.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import ssl
import time
from urllib.parse import urlsplit


def descriptor_limit(soft,hard,count):
    wanted=max(soft,count+256)
    return wanted if hard<0 or hard==resource.RLIM_INFINITY else min(hard,wanted)


def post_drain_deadline(meta,rows):
    offered_recovery=meta['start_monotonic_ns']+meta['duration_ns']+round(meta['recovery_s']*1e9)
    last_return=max((row['return_monotonic_ns'] for row in rows),default=meta['start_monotonic_ns'])
    return max(offered_recovery,last_return+round(meta['drain_settle_s']*1e9))


def atomic_json(path,value):
    path=Path(path);temporary=path.with_suffix(path.suffix+'.partial')
    temporary.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n');temporary.replace(path)


class Journal:
    def __init__(self,path):self.stream=Path(path).open('x')
    def append(self,row):
        self.stream.write(json.dumps(row,sort_keys=True)+'\n');self.stream.flush()
    def close(self):
        self.stream.flush();os.fsync(self.stream.fileno());self.stream.close()


async def http_request(url,body,headers,timeout_s):
    async def perform():
        parsed=urlsplit(url)
        if parsed.scheme not in ('http','https') or not parsed.hostname:raise ValueError('invalid URL')
        port=parsed.port or (443 if parsed.scheme=='https' else 80)
        reader,writer=await asyncio.open_connection(parsed.hostname,port,ssl=ssl.create_default_context() if parsed.scheme=='https' else None)
        try:
            target=(parsed.path or '/')+('?' + parsed.query if parsed.query else '')
            method='GET' if body is None else 'POST';payload=body or b''
            lines=[f'{method} {target} HTTP/1.1',f'Host: {parsed.netloc}','Connection: close',f'Content-Length: {len(payload)}']
            for key,value in headers.items():
                if '\r' in key+str(value) or '\n' in key+str(value):raise ValueError('invalid HTTP header')
                lines.append(f'{key}: {value}')
            writer.write(('\r\n'.join(lines)+'\r\n\r\n').encode('ascii')+payload);await writer.drain()
            raw_headers=await reader.readuntil(b'\r\n\r\n');lines=raw_headers.decode('iso-8859-1').split('\r\n')
            status=int(lines[0].split()[1]);response_headers={}
            for line in lines[1:]:
                if ':' in line:
                    key,value=line.split(':',1);response_headers[key.lower().strip()]=value.strip()
            if response_headers.get('transfer-encoding','').lower()=='chunked':
                chunks=[];length=0
                while True:
                    size=int((await reader.readline()).split(b';',1)[0],16)
                    if size==0:
                        while await reader.readline() not in (b'\r\n',b'\n',b''):pass
                        break
                    length+=size
                    if length>8*1024*1024:raise ValueError('response too large')
                    chunks.append(await reader.readexactly(size))
                    if await reader.readexactly(2)!=b'\r\n':raise ValueError('invalid chunk')
                raw=b''.join(chunks)
            elif 'content-length' in response_headers:
                size=int(response_headers['content-length'])
                if not 0<=size<=8*1024*1024:raise ValueError('response too large')
                raw=await reader.readexactly(size)
            else:
                chunks=[];length=0
                while True:
                    chunk=await reader.read(65536)
                    if not chunk:break
                    length+=len(chunk)
                    if length>8*1024*1024:raise ValueError('response too large')
                    chunks.append(chunk)
                raw=b''.join(chunks)
            return status,response_headers,raw
        finally:
            writer.close()
            try:await writer.wait_closed()
            except (ConnectionError,OSError):pass
    return await asyncio.wait_for(perform(),timeout_s)


async def wait_until(target):
    while time.monotonic_ns()<target:await asyncio.sleep((target-time.monotonic_ns())/1e9)


def decode_reply(raw):
    text=raw.decode('utf-8',errors='replace')
    try:response=json.loads(text)
    except ValueError:response=None
    return text,response


async def collect_requests(meta,jpeg,path,http=http_request):
    journal=Journal(path);tasks=[]
    async def one(ident,planned):
        row={'id':ident,'url':meta['url'],'planned_submit_monotonic_ns':planned,
             'actual_submit_monotonic_ns':time.monotonic_ns(),'http_status':None,'response':None,'error':None}
        try:
            status,headers,raw=await http(meta['url'],jpeg,{'Content-Type':'image/jpeg','X-Request-ID':ident},meta['timeout_s'])
            row['return_monotonic_ns']=time.monotonic_ns();row['http_status']=status;row['response_headers']=headers
            row['response_body'],row['response']=decode_reply(raw)
        except TimeoutError as error:row.update(error='client_timeout',error_detail=str(error))
        except Exception as error:row.update(error=type(error).__name__,error_detail=str(error))
        finally:
            row.setdefault('return_monotonic_ns',time.monotonic_ns());journal.append(row)
    try:
        count=len(meta['expected_ids'])
        for index,ident in enumerate(meta['expected_ids']):
            planned=meta['start_monotonic_ns']+index*meta['duration_ns']//count
            await wait_until(planned)
            tasks.append(asyncio.create_task(one(ident,planned)))
            await asyncio.sleep(0)
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        journal.close()


async def sample_states(meta,path,stop,http=http_request):
    journal=Journal(path);target=time.monotonic_ns();interval=round(meta['sample_interval_ms']*1e6)
    try:
        while not stop.is_set():
            await wait_until(target)
            row={'request_monotonic_ns':time.monotonic_ns(),'http_status':None,'response':None,'error':None}
            try:
                status,headers,raw=await http(meta['state_url'],None,{},meta['state_timeout_s'])
                row['http_status']=status;row['response_headers']=headers
                row['response_body'],row['response']=decode_reply(raw)
            except Exception as error:row.update(error=type(error).__name__,error_detail=str(error))
            row['return_monotonic_ns']=time.monotonic_ns();journal.append(row)
            target+=interval
            if target<time.monotonic_ns():target=time.monotonic_ns()
    finally:journal.close()


def drain_evidence(meta,samples,requests):
    unresolved=[r['id'] for r in requests if r.get('error') or not isinstance(r.get('response'),dict)
                or r['response'].get('status') in ('timeout','submission_error')]
    threshold=meta['start_monotonic_ns']+meta['duration_ns']+round(meta.get('drain_settle_s',3)*1e9)
    eligible=[s for s in samples if s.get('return_monotonic_ns',0)>=threshold]
    witnesses=[]
    for sample in eligible:
        body=sample.get('response') or {};nodes=body.get('nodes',[])
        valid=(sample.get('http_status')==200 and set(n.get('node') for n in nodes)==set(meta['node_roles'])
               and all(n.get('valid') is True and 0<=n.get('age_ms',float('inf'))<=meta['state_max_age_ms'] for n in nodes))
        if valid and body.get('unresolved_requests')==0 and body.get('active_http_requests')==0:
            witnesses.append(sample['return_monotonic_ns'])
    span=(max(witnesses)-min(witnesses))/1e9 if len(witnesses)>1 else 0
    return {'verified':False,'client_unresolved_ids':unresolved,'idle_origin_sample_count':len(witnesses),
            'idle_origin_observed_span_s':span,
            'reason':'origin wait/state evidence only; backend executor/queue drain requires independent owned-runtime reset audit'}


async def execute(args):
    from decimal import Decimal,ROUND_HALF_UP
    jpeg=args.jpeg.read_bytes()
    if not jpeg.startswith(b'\xff\xd8') or not jpeg.endswith(b'\xff\xd9'):raise ValueError('input is not a complete JPEG')
    if len(jpeg)>args.max_jpeg_bytes:raise ValueError('JPEG exceeds native message budget')
    if args.duration_s<=0 or args.rate_rps<=0 or not 20<=args.sample_ms<=50 or args.timeout_s<=0:
        raise ValueError('invalid duration/rate/sample interval/timeout')
    if args.baseline_s<0 or args.recovery_s<0 or args.drain_settle_s<0 or args.state_max_age_ms<=0:raise ValueError('invalid observation timing')
    if args.expected_output_sha256 is not None and (len(args.expected_output_sha256)!=64 or any(c not in '0123456789abcdef' for c in args.expected_output_sha256)):
        raise ValueError('expected output digest must be lowercase SHA-256 hex')
    roles=json.loads(args.node_roles.read_text())
    if not roles or any(role not in ('edge','cloud') for role in roles.values()):raise ValueError('invalid frozen node role map')
    budgets=json.loads(args.node_cpu_budgets.read_text()) if args.node_cpu_budgets else {}
    if not args.allow_status_only and (set(budgets)!=set(roles) or any(not isinstance(cpus,list) or len(set(cpus))<2 or any(type(cpu) is not int or cpu<0 for cpu in cpus) for cpus in budgets.values())):
        raise ValueError('formal inference requires exact frozen node CPU budget map')
    count=int((Decimal(str(args.duration_s))*Decimal(str(args.rate_rps))).to_integral_value(rounding=ROUND_HALF_UP))
    if count<1:raise ValueError('no offered requests')
    args.output.mkdir(parents=True,exist_ok=False)
    soft,hard=resource.getrlimit(resource.RLIMIT_NOFILE)
    wanted=descriptor_limit(soft,hard,count);resource.setrlimit(resource.RLIMIT_NOFILE,(wanted,hard))
    if wanted<count+32:raise ValueError('insufficient open file descriptor limit for uncapped trace')
    start=time.monotonic_ns()+round(args.baseline_s*1e9)
    meta={'schema_version':1,'system':args.system,'repeat':args.repeat,'rate_rps':args.rate_rps,
          'duration_ns':round(args.duration_s*1e9),'deadline_ms':args.deadline_ms,'start_monotonic_ns':start,
          'clock_sample':{'unix_ns':time.time_ns(),'monotonic_ns':time.monotonic_ns()},
          'baseline_s':args.baseline_s,'recovery_s':args.recovery_s,'timeout_s':args.timeout_s,
          'sample_interval_ms':args.sample_ms,'state_max_age_ms':args.state_max_age_ms,'state_timeout_s':args.state_timeout_s,
          'drain_settle_s':args.drain_settle_s,'node_roles':roles,'node_cpu_budgets':budgets,'url':args.url,'state_url':args.state_url,
          'jpeg_sha256':hashlib.sha256(jpeg).hexdigest(),'jpeg_bytes':len(jpeg),'expected_result':None,
          'response_contract':'native-jpeg-v1','require_inference_witness':not args.allow_status_only,
          'expected_output_sha256':args.expected_output_sha256,
          'expected_ids':[f'{args.run_id}-r{i:06d}' for i in range(count)],'run_id':args.run_id,
          'hostname':platform.node(),'pid':os.getpid(),'python':platform.python_version(),'rlimit_nofile':[wanted,hard],
          'source_sha256':{name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in ('collect_measurement.py','analyze_measurement.py')},
          'client_collection_complete':False,'transport':'raw JPEG HTTP/1.1 new connection per attempt; no retries or concurrency cap'}
    atomic_json(args.output/'metadata.json',meta);stop=asyncio.Event()
    sampler=asyncio.create_task(sample_states(meta,args.output/'states.jsonl',stop))
    try:
        await collect_requests(meta,jpeg,args.output/'requests.jsonl')
        from analyze_measurement import read_jsonl
        meta['post_drain_observation_deadline_monotonic_ns']=post_drain_deadline(meta,read_jsonl(args.output/'requests.jsonl'))
        await wait_until(meta['post_drain_observation_deadline_monotonic_ns'])
        meta['client_collection_complete']=True
    finally:
        stop.set();await sampler
        from analyze_measurement import read_jsonl,summarize
        requests=read_jsonl(args.output/'requests.jsonl');samples=read_jsonl(args.output/'states.jsonl')
        meta['collection_end_monotonic_ns']=time.monotonic_ns()
        meta['backend_drain']=drain_evidence(meta,samples,requests)
        meta['raw_sha256']={name:hashlib.sha256((args.output/name).read_bytes()).hexdigest() for name in ('requests.jsonl','states.jsonl')}
        atomic_json(args.output/'metadata.json',meta)
    summary=summarize(meta,requests,samples);atomic_json(args.output/'collection-summary.json',summary)
    print(json.dumps({'run_id':args.run_id,'offered':count,'failed':summary['failed'],'goodput_rps':summary['goodput_rps'],
                      'backend_drain':meta['backend_drain'],'output':str(args.output)}))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url',required=True);p.add_argument('--state-url',required=True);p.add_argument('--jpeg',type=Path,required=True)
    p.add_argument('--node-cpu-budgets',type=Path);p.add_argument('--node-roles',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--run-id',required=True);p.add_argument('--system',default='Tide-new-native');p.add_argument('--repeat',type=int,default=1)
    p.add_argument('--duration-s',type=float,default=30);p.add_argument('--rate-rps',type=float,required=True)
    p.add_argument('--deadline-ms',type=float,default=350);p.add_argument('--timeout-s',type=float,default=180)
    p.add_argument('--baseline-s',type=float,default=10);p.add_argument('--recovery-s',type=float,default=60)
    p.add_argument('--sample-ms',type=float,default=25);p.add_argument('--state-max-age-ms',type=float,default=500)
    p.add_argument('--state-timeout-s',type=float,default=2);p.add_argument('--drain-settle-s',type=float,default=3)
    p.add_argument('--expected-output-sha256',help='frozen calibration output digest; supply for formal measurement')
    p.add_argument('--max-jpeg-bytes',type=int,default=250000);p.add_argument('--allow-status-only',action='store_true')
    asyncio.run(execute(p.parse_args()))


if __name__=='__main__':main()
