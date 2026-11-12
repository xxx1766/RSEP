"""Owned, synchronous per-worker witnesses; native EXIT follows the write."""
from pathlib import Path
import json
import os
import re


class WorkerEvidence:
    def __init__(self,root,incoming_queue):
        if re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}-in',incoming_queue) is None:
            raise ValueError('invalid owned worker queue identity')
        folder=Path(root)/'native-evidence'/incoming_queue[:-3]
        folder.mkdir(parents=True,exist_ok=False)
        self.path=folder/'stdout'
        self.fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)

    def write(self,event):
        data=(json.dumps(event,separators=(',',':'),allow_nan=False)+'\n').encode()
        if len(data)>=4096:raise ValueError('evidence record exceeds single-write bound')
        if os.write(self.fd,data)!=len(data):raise OSError('short evidence write')
