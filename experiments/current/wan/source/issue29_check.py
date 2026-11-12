"""Compact live status; complete raw per-guest snapshots stay in progress/."""
import contextlib
import io
import json
import issue29_common as p
from issue29_progress import main

if __name__=='__main__':
 with contextlib.redirect_stdout(io.StringIO()):r=main()
 errors={vm:x['read_error'] for vm,x in r['guests'].items() if 'read_error' in x}
 obs=[(vm,x) for vm,row in r['guests'].items() for k,x in row.get('observations',{}).items() if k=='heartbeat']
 state=json.loads((p.OUT/'STATE.json').read_text()) if (p.OUT/'STATE.json').exists() else {}
 final=json.loads((p.OUT/'campaign-final.json').read_text()) if (p.OUT/'campaign-final.json').exists() else None
 print(json.dumps(dict(minutes=round(r['minutes_since_started'],2),read_errors=errors,heartbeat_observers=len(obs),all_heartbeats_present=not errors and len(obs)==8,max_heartbeat_gap_ms=max([x['max_gap_ms'] for _,x in obs],default=None),campaign_state=state,campaign_final=final)))
