"""Finish the fixed matrix, collect all evidence, clean only owned VMs, then seal."""
import json
import subprocess
import sys
import time
import issue29_common as p
import issue29_continue
import issue29_collect
import issue29_cleanup
import issue29_finalize

def main():
 try:issue29_continue.main()
 except Exception as error:print('CAMPAIGN_STOPPED_RETAIN_RAW',repr(error),flush=True)
 issue29_collect.main()
 issue29_cleanup.shutdown()
 until=time.monotonic()+240
 while time.monotonic()<until:
  ready=True
  for host in p.HOSTS:
   body=p.ssh(host,'cat '+p.REMOTE+'/shutdown-result.json 2>/dev/null || true')
   if not body or not json.loads(body).get('all_cleanly_stopped'):ready=False
  if ready:break
  time.sleep(5)
 else:raise RuntimeError('clean shutdown not confirmed; retain interfaces')
 issue29_cleanup.finish();issue29_finalize.main()

if __name__=='__main__':main()
