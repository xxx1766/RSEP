"""Continue only unstarted frozen cells after documented collection recovery."""
import json
import time
import traceback
from pathlib import Path
import issue29_common as p
import issue29_ops as ops
import issue29_campaign as campaign
from issue29_finalize import frozen_run_checks

def main():
 results=json.loads((p.OUT/'pilot-results-progress.json').read_text())['results']
 assert len(results)==29 and results[-1]['name']=='original65-gradual-kz'
 frozen_run_checks()
 cells=json.loads((p.OUT/'matrix-frozen.json').read_text())['cells'];frozen=json.loads((p.OUT/'parameters-frozen.json').read_text());endpoints=json.loads((p.OUT/'services/endpoints.json').read_text());done={r['name'] for r in results}
 pending=[(c,a) for c in cells for a in c['arms'] if c['name']+'-'+a not in done]
 assert len(pending)==5 and all(not (p.OUT/'runs'/(c['name']+'-'+a)).exists() for c,a in pending)
 p.save(p.OUT/'interruptions/002-gradual-kz-collection/continuation.json',dict(completed=29,pending=[c['name']+'-'+a for c,a in pending],change='SCP -O file transfer only; all measured parameters/trace bytes unchanged',unix_ns=time.time_ns()))
 (p.OUT/'campaign-final.json').unlink()
 try:
  ops.shape('65',p.OUT/'comparisons-network/resume65');ops.clocks('resume-comparisons-before')
  for cell,arm in pending:
   result=campaign.comparison(cell,arm,frozen['predictors']['contexts'][cell['rtt']],endpoints);results.append(result);p.save(p.OUT/'pilot-results-progress.json',dict(results=results,completed=len(results),expected=34))
  campaign.state('completed',completed=len(results));p.save(p.OUT/'campaign-final.json',dict(status='COMPLETE',completed=len(results),expected=34,unix_ns=time.time_ns(),collection_interruption_preserved=True))
 except BaseException as error:
  p.save(p.OUT/'campaign-final.json',dict(status='STOPPED',completed=len(results),expected=34,error=str(error),traceback=traceback.format_exc(),unix_ns=time.time_ns()));campaign.state('stopped',error=str(error),completed=len(results));raise

if __name__=='__main__':main()
