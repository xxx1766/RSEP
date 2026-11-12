"""Low-rate network boundary diagnostics; no workload or scheduling policy."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import socket
import threading
import time


def make_server(host,port,output):
 lock=threading.Lock()
 class Handler(BaseHTTPRequestHandler):
  protocol_version='HTTP/1.1';disable_nagle_algorithm=True
  def log_message(self,*args):pass
  def do_GET(self):
   body=b'{"ready":true}';self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
  def do_POST(self):
   row=dict(id=self.headers.get('X-Probe-ID'),peer=self.client_address,received_unix_ns=time.time_ns());start=time.monotonic_ns();self.connection.settimeout(3)
   try:
    size=int(self.headers['Content-Length']);assert 0<size<=1048576
    body=self.rfile.read(size);assert len(body)==size
    digest=hashlib.sha256(body).hexdigest();row.update(bytes=size,sha256=digest,read_ms=(time.monotonic_ns()-start)/1e6)
    self.send_response(200);self.send_header('Content-Length',str(len(body)));self.send_header('X-Probe-ID',row['id']);self.send_header('X-Body-SHA256',digest);self.send_header('X-Server-Unix-Ns',str(time.time_ns()));self.end_headers();self.wfile.write(body);row['ok']=True
   except Exception as error:row.update(ok=False,error=f'{type(error).__name__}: {error}')
   row['ended_unix_ns']=time.time_ns()
   if output:
    with lock:output.write(json.dumps(row)+'\n');output.flush()
 class Server(ThreadingHTTPServer):
  daemon_threads=True;request_queue_size=128
 return Server((host,port),Handler)


def probe(endpoint,ident,size=0,path='/health'):
 host,port=endpoint.split(':');conn=http.client.HTTPConnection(host,int(port),timeout=2)
 start=time.monotonic_ns();row=dict(id=ident,endpoint=endpoint,bytes=size,path='/echo' if size else path,unix_ns=time.time_ns(),stage='connect')
 try:
  conn.connect();row['local_port']=conn.sock.getsockname()[1];row['connect_ms']=(time.monotonic_ns()-start)/1e6;row['stage']='send'
  body=bytes(range(256))*(size//256)+bytes(range(size%256)) if size else None
  conn.request('POST' if size else 'GET','/echo' if size else path,body,{'X-Probe-ID':ident})
  row['sent_ms']=(time.monotonic_ns()-start)/1e6;row['stage']='headers';reply=conn.getresponse();row['headers_ms']=(time.monotonic_ns()-start)/1e6;row['http_status']=reply.status;row['stage']='body';data=reply.read();row['stage']='validate'
  if reply.status!=200:raise ValueError('HTTP status '+str(reply.status))
  if size:
   digest=hashlib.sha256(body).hexdigest()
   if data!=body or reply.getheader('X-Probe-ID')!=ident or reply.getheader('X-Body-SHA256')!=digest:raise ValueError('echo identity/body mismatch')
   row['sha256']=digest
  elif not json.loads(data).get('ready'):raise ValueError('health not ready')
  row.update(ok=True,stage='done')
 except Exception as error:row.update(ok=False,error=f'{type(error).__name__}: {error}')
 finally:conn.close()
 row.update(ended_unix_ns=time.time_ns(),elapsed_ms=(time.monotonic_ns()-start)/1e6)
 return row


def campaign(endpoints,seconds,output,echo=False,label="net24"):
 start=time.monotonic();index=0;identity=str(time.time_ns())
 with ThreadPoolExecutor(len(endpoints)) as pool:
  while time.monotonic()-start<seconds:
   begin=time.monotonic();size=[1,4096,65536,1048576][index%4] if echo else 0
   for row in pool.map(lambda endpoint:probe(endpoint,f'{label}-{identity}-{index}-{endpoint}',size),endpoints):output.write(json.dumps(row)+'\n');output.flush()
   index+=1;time.sleep(max(0,5-(time.monotonic()-begin)))


if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['serve','probe']);parser.add_argument('--host',default='0.0.0.0');parser.add_argument('--port',type=int,default=30924);parser.add_argument('--endpoints',nargs='+');parser.add_argument('--seconds',type=int,default=600);parser.add_argument('--echo',action='store_true');parser.add_argument('--label',default='net24');parser.add_argument('--out',type=Path,required=True);a=parser.parse_args()
 a.out.parent.mkdir(parents=True,exist_ok=True)
 with a.out.open('x',buffering=1) as output:
  if a.mode=='serve':make_server(a.host,a.port,output).serve_forever()
  else:campaign(a.endpoints,a.seconds,output,a.echo,a.label)
