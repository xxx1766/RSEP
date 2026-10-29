#!/usr/bin/env python3
"""Patch an isolated Tide source tree with opt-in timing only; fail on drift."""
import argparse,shutil
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('tree',type=Path);a=p.parse_args();root=a.tree
trace='github.com/ict/tide/pkg/revisiontrace'
def edit(name,changes):
 f=root/name;s=f.read_text()
 if trace not in s:s=s.replace('import (','import (\n "'+trace+'"',1)
 for old,new in changes:
  if s.count(old)!=1:raise ValueError(f'{name}: expected one match: {old!r}; found {s.count(old)}')
  s=s.replace(old,new)
 f.write_text(s)
shutil.copytree(Path(__file__).with_name('issue8_trace'),root/'pkg/revisiontrace')
shutil.copytree(root/'cmd/revision-control',root/'cmd/revision-latency')
edit('cmd/revision-latency/main.go',[
 ('flag.Parse()', 'initial := flag.Int("initial", 1, "initial executor count: 0=cold, 1=warm")\n flag.Parse()\n if *initial != 0 && *initial != 1 { panic("initial must be 0 or 1") }\n defer func(){if err:=revisiontrace.Flush(os.Getenv("TIDE_STAGE_TRACE"));err!=nil{panic(err)}}()'),
 ('InitExecNo: 1','InitExecNo: *initial'),
 ('mux.HandleFunc("/run", func(w http.ResponseWriter, req *http.Request) {','mux.HandleFunc("/run", func(w http.ResponseWriter, req *http.Request) {\n id:=fmt.Sprintf("latency-%d", seq.Add(1))\n revisiontrace.Mark(id,"http_enter", "")\n w.Header().Set("X-Trace-Id",id)'),
 ('fmt.Sprintf("control-%d", seq.Add(1))','id'),
 ('st.SubmitRoutine(r)','revisiontrace.Mark(id,"submit", "")\n st.SubmitRoutine(r)'),
 ('case <-r.WaitData():','case <-r.WaitData():\n revisiontrace.Mark(id,"http_result", "")'),
 ('w.Write(r.Result())','w.Write(r.Result())\n revisiontrace.Mark(id,"http_written", "")')])
edit('pkg/stub/stub_op.go',[( '\t\t\ts.rtnStore.Push(rtn)', '\t\t\trevisiontrace.Mark(rtn.Id,"enqueue", "")\n\t\t\ts.rtnStore.Push(rtn)')])
edit('pkg/stub/process_routine.go',[
 ('func (s *stub) processOne(r *routine.Routine) {','func (s *stub) processOne(r *routine.Routine) {\n revisiontrace.Mark(r.Id,"dispatch", "")'),
 ('proc := s.acquireProcessor(r)','proc := s.acquireProcessor(r)\n revisiontrace.Mark(r.Id,"resource_acquired", "")'),
 ('exec := s.execPool.Get()', 'revisiontrace.Mark(rtn.Id,"pool_get", "")\n exec := s.execPool.Get()\n if exec != nil { revisiontrace.Mark(rtn.Id,"pool_acquired",exec.Id()) }'),
 ('executeErr = exec.Execute()', 'revisiontrace.Mark(rtn.Id,"execute", "")\n executeErr = exec.Execute()')])
edit('pkg/stub/event_loop.go',[( 'rtn := e.GetRoutine()', 'rtn := e.GetRoutine()\n revisiontrace.Mark(rtn.Id,"exit_received",e.Id())')])
edit('pkg/executor/instance/container_ins.go',[
 ('ctn, err := containerx.CreateCtn(opts)', 'revisiontrace.Mark(args.Id,"container_create_start", "")\n ctn, err := containerx.CreateCtn(opts)\n revisiontrace.Mark(args.Id,"container_create_end", "")'),
 ('return containerx.StartCtr(ins.ctr)', 'revisiontrace.Mark(ins.id,"container_start_start", "")\n err:=containerx.StartCtr(ins.ctr)\n revisiontrace.Mark(ins.id,"container_start_end", "")\n return err')])
edit('pkg/stub/execpool/stack_exec_pool.go',[( 'logrus.WithField("ExecutorId", newExec.Id()).Info("StackPool: received one INIT event")', 'revisiontrace.Mark(newExec.Id(),"executor_ready", "")\n logrus.WithField("ExecutorId", newExec.Id()).Info("StackPool: received one INIT event")')])
print('Patched isolated tree:',root)
