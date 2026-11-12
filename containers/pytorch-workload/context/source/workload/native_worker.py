"""Actual PAIX INIT/ROUTINE_SUBMIT/EXIT; computation uses unchanged user functions."""
import json
import os
import signal
import sys

os.sched_setaffinity(0,set(range(2,8)))
sys.path.insert(0,'/app')
import posix_ipc
from execinspb import proto_pb2 as pb
from common import make_kernel,ready,composite,valid_input
from evidence_sink import WorkerEvidence

sink=None


def emit(event):
    event=dict(event,native_tide_dispatch=True)
    event.pop('calls',None)
    assert sink is not None
    sink.write(event)


def main():
    global sink
    sink=WorkerEvidence(os.environ['LEGACY_ROOT'],os.environ['TIDE_IN_MQ'])
    kernel=make_kernel();emit(ready(kernel,True))
    incoming=posix_ipc.MessageQueue('/'+os.environ['TIDE_IN_MQ'])
    outgoing=posix_ipc.MessageQueue('/'+os.environ['TIDE_OUT_MQ'])
    event=pb.Event();event.type=pb.Event.INIT;outgoing.send(event.SerializeToString())
    while True:
        data,_=incoming.receive();event=pb.Event();event.ParseFromString(data)
        assert event.type==pb.Event.ROUTINE_SUBMIT
        result=composite(kernel,bad_input=not valid_input(event.routine.args),request_id=event.routine.id,emit=emit)
        emit(result)
        reply=pb.Event();reply.type=pb.Event.EXIT;reply.status=pb.Event.SUCC
        outgoing.send(reply.SerializeToString())


if __name__=='__main__':main()
