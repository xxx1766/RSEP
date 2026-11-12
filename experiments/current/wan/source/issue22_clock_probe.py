"""Independent LAN clock bounds; never modifies clocks or experiment traffic."""
import argparse
import json
import socket
import socketserver
import struct
import time

class Handler(socketserver.StreamRequestHandler):
 def handle(self):
  while self.rfile.read(1):
   self.wfile.write(struct.pack('!Q',time.time_ns()));self.wfile.flush()


def measure(host,port,count=30):
 rows=[]
 with socket.create_connection((host,port),timeout=3) as c:
  c.settimeout(3)
  for _ in range(count):
   t0=time.time_ns();m0=time.monotonic_ns();c.sendall(b'c');data=b''
   while len(data)<8:
    part=c.recv(8-len(data))
    if not part:raise RuntimeError('truncated clock reply')
    data+=part
   m1=time.monotonic_ns();t1=time.time_ns();remote=struct.unpack('!Q',data)[0]
   rows.append(dict(t0=t0,t1=t1,remote=remote,rtt_ns=m1-m0,offset_lower_ns=remote-t1,offset_upper_ns=remote-t0))
   time.sleep(.02)
 lo=max(r['offset_lower_ns'] for r in rows);hi=min(r['offset_upper_ns'] for r in rows)
 return dict(host=host,samples=rows,offset_interval_ms=[lo/1e6,hi/1e6],consistent=lo<=hi,maximum_absolute_offset_ms=max(abs(lo),abs(hi))/1e6)

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('mode',choices=['serve','measure']);p.add_argument('--host',required=True);p.add_argument('--port',type=int,default=30922);a=p.parse_args()
 if a.mode=='serve':
  with socketserver.ThreadingTCPServer((a.host,a.port),Handler) as s:s.serve_forever()
 else:print(json.dumps(measure(a.host,a.port)))
