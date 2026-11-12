"""Identical complete computation and partial-work witnesses for both transports."""
import hashlib
import json
import os
from pathlib import Path
import time
from legacy_adapter import LegacyKernel,ROOT

INPUT='6818cd9ad787e555879a52a706f66652be180b9f9d62a08397d74012705a9620'


def thread_masks():
    return {p.name:sorted(os.sched_getaffinity(int(p.name))) for p in Path('/proc/self/task').glob('*')}


def make_kernel():
    threads=int(os.environ.get('TIDE_TORCH_THREADS','2'))
    if threads not in [1,2]:raise ValueError('unplanned torch thread count')
    import torch
    if torch.get_num_interop_threads() != 8:
        torch.set_num_interop_threads(8)
    kernel=LegacyKernel(threads=threads)
    kernel.module.cv2.setNumThreads(1)
    assert kernel.identity()['torch_interop']==8
    assert kernel.identity()['opencv_threads']==1
    return kernel


def ready(kernel,native):
    identity=kernel.identity();identity.pop('tide_sdk',None)
    return dict(event='worker_ready',**identity,model_sha256=identity['checkpoint_sha256'],
        inferences_requested=10,native_tide_dispatch=native,
        full_result_image_transferred=False,thread_masks=thread_masks())


def composite(kernel,bad_input,request_id,emit):
    if bad_input:raise ValueError('request input hash mismatch')
    start=time.time_ns();cpu=time.process_time_ns()
    emit(dict(event='request_start',request_id=request_id,pid=os.getpid(),start_ns=start,
        input_sha256=INPUT,affinity=sorted(os.sched_getaffinity(0)),thread_masks=thread_masks()))
    calls=[]
    for index in range(10):
        image,event=kernel.run_request(f'{request_id}/call-{index+1}')
        calls.append(event)
        emit(dict(event='detection_complete',request_id=request_id,pid=os.getpid(),
            call_index=index+1,completed_ns=time.time_ns(),call=event))
    finish=time.time_ns();cpu_finish=time.process_time_ns()
    return dict(event='request_complete',request_id=request_id,pid=os.getpid(),success=True,
        start_ns=start,finish_ns=finish,input_sha256=INPUT,inferences_requested=10,inferences_completed=10,
        affinity=sorted(os.sched_getaffinity(0)),shape=list(image.shape),
        process_cpu_ms=(cpu_finish-cpu)/1e6,function_execution_sum_ms=sum(e['execution_ms'] for e in calls),
        calls=calls,output_sha256=hashlib.sha256(image.tobytes()).hexdigest(),
        thread_masks=thread_masks(),full_result_image_transferred=False)


def valid_input(data):return hashlib.sha256(data).hexdigest()==INPUT
