"""Single HTTP container with a fixed process pool, one model per serial worker."""
from concurrent.futures import ProcessPoolExecutor
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from multiprocessing import get_context
import fcntl
import json
import os
from pathlib import Path
import time

KERNEL=None


def emit(event):
    event=dict(event,native_tide_dispatch=False)
    event.pop('calls',None)
    path=os.environ['FIG5_TRACE_PATH']
    data=(json.dumps(event,separators=(',',':'))+'\n').encode()
    with open(path,'ab',buffering=0) as stream:
        fcntl.flock(stream,fcntl.LOCK_EX);stream.write(data);fcntl.flock(stream,fcntl.LOCK_UN)


def initialize(queue):
    global KERNEL
    os.sched_setaffinity(0,set(range(2,8)))
    from common import make_kernel,ready
    KERNEL=make_kernel();emit(ready(KERNEL,False));queue.put(os.getpid())


def hold(seconds):time.sleep(seconds);return os.getpid()


def infer(data,rid):
    from common import composite,valid_input
    result=composite(KERNEL,bad_input=not valid_input(data),request_id=rid,emit=emit)
    emit(result)
    return dict(success=True,request_id=rid,inferences_completed=10,output_sha256=result['output_sha256'])


def main():
    os.sched_setaffinity(0,set(range(2,8)))
    context=get_context('spawn');queue=context.Queue();workers=int(os.environ['TIDE_WORKERS'])
    trace=Path(os.environ['FIG5_TRACE_PATH']);assert not trace.exists();trace.parent.mkdir(parents=True,exist_ok=True)
    pool=ProcessPoolExecutor(max_workers=workers,mp_context=context,initializer=initialize,initargs=(queue,))
    warm=[pool.submit(hold,.1) for _ in range(workers)]
    ready_pids={queue.get(timeout=120) for _ in range(workers)}
    assert len(ready_pids)==workers
    for result in warm:result.result(timeout=120)

    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def log_message(self,*args):pass
        def reply(self,status,payload):
            data=json.dumps(payload).encode()
            self.send_response(status);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(data)));self.end_headers()
            try:self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError):pass
        def do_GET(self):self.reply(200,dict(ready=True,workers=workers))
        def do_POST(self):
            assert self.path=='/infer'
            size=int(self.headers['Content-Length']);data=self.rfile.read(size)
            rid=self.headers['X-Request-ID']
            try:result=pool.submit(infer,data,rid).result()
            except BaseException as exc:
                emit(dict(event='request_failure',request_id=rid,error=repr(exc)));self.reply(500,dict(success=False));return
            self.reply(200,result)

    address,port=os.environ.get('TIDE_BIND','127.0.0.1:8080').rsplit(':',1)
    server=ThreadingHTTPServer((address,int(port)),Handler);server.daemon_threads=True;server.serve_forever()


if __name__=='__main__':main()
