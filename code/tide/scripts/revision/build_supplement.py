#!/usr/bin/env python3
"""Build an independent, evidence-scoped experiment supplement (no main-paper edits).

From Tide: python3 scripts/revision/build_supplement.py --root results-revision-20260928
Use --check to list missing final inputs without creating the TeX document.
Requires numpy/scipy for run-level summaries of existing measured CSVs.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics

from scipy.stats import t as student_t

SYSTEM = {'tide':'Tide','bare':'Bare','kubernetes':'Kubernetes'}
FIGURES = {'A':'figA_ctrl_plane.pdf','B':'figB_degradation.pdf','C':'figC_sensitivity.pdf',
           'D':'figD_hetero.pdf','E':'figE_utilization.pdf','F':'figF_optgap.pdf'}


def tex(value):
    escapes={'\\':r'\textbackslash{}','&':r'\&','%':r'\%','$':r'\$','#':r'\#','_':r'\_',
             '{':r'\{','}':r'\}','~':r'\textasciitilde{}','^':r'\textasciicircum{}'}
    return ''.join(escapes.get(c,c) for c in str(value))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition: raise ValueError(message)


def mean_ci(values):
    values=[float(v) for v in values]
    require(values and all(math.isfinite(v) for v in values),'missing/nonfinite observations')
    mean=statistics.mean(values)
    half=student_t.ppf(.975,len(values)-1)*statistics.stdev(values)/math.sqrt(len(values)) if len(values)>1 else None
    return {'n':len(values),'mean':mean,'ci95_low':mean-half if half is not None else None,
            'ci95_high':mean+half if half is not None else None}


def number(value, places=2):
    return f'{float(value):.{places}f}'


def interval(stats, scale=1, places=2):
    return f'{number(stats["mean"]*scale,places)} [{number(stats["ci95_low"]*scale,places)}, {number(stats["ci95_high"]*scale,places)}]'


def stored_interval(row, metric, scale=1, places=2):
    return interval({key:float(row[metric+'_'+key]) for key in ('mean','ci95_low','ci95_high')},scale,places)


class Inputs:
    def __init__(self, root):
        self.root=root;self.hashes={};self.source_root=Path(__file__).resolve().parents[2]
    def file(self,name):
        path=self.root/name
        require(path.is_file(),f'missing input: {path}')
        self.hashes[str(path.resolve())]=sha(path)
        return path
    def json(self,name): return json.loads(self.file(name).read_text())
    def csv(self,name):
        with self.file(name).open(newline='') as stream:return list(csv.DictReader(stream))
    def checked_hashes(self,directory,mapping):
        for name,expected in mapping.items():
            path=directory/name
            require(path.is_file() and sha(path)==expected,f'input hash mismatch: {path}')
            self.hashes[str(path.resolve())]=expected
    def figure_manifest(self,name):
        meta=self.json('figures/'+name+'_manifest.json')
        self.checked_hashes(self.source_root,meta.get('input_sha256',meta.get('sources_sha256',{})))
        for filename, expected in meta.get('outputs_sha256',{}).items():
            base = self.root/'figures' if Path(filename).parent == Path('.') else self.source_root
            self.checked_hashes(base,{filename:expected})
        return meta


def write_table(directory,name,headers,rows):
    with (directory/(name+'.csv')).open('w',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(headers);writer.writerows(rows)
    lines=[r'\begingroup\footnotesize\setlength{\tabcolsep}{4pt}\renewcommand{\arraystretch}{1.18}',
           r'\begin{longtable}{'+'l'*len(headers)+'}',r'\toprule',
           ' & '.join(tex(h) for h in headers)+r'\\\midrule',r'\endfirsthead',
           r'\toprule',' & '.join(tex(h) for h in headers)+r'\\\midrule',r'\endhead']
    lines += [' & '.join(tex(v) for v in row)+r'\\' for row in rows]
    lines += [r'\bottomrule\end{longtable}\endgroup']
    (directory/(name+'.tex')).write_text('\n'.join(lines)+'\n')


def summarize_groups(rows,key,metrics):
    groups=defaultdict(list)
    for row in rows: groups[row[key]].append(row)
    return {k:{metric:mean_ci([r[metric] for r in group]) for metric in metrics}
            for k,group in sorted(groups.items(),key=lambda p:float(p[0]))}


def figure(letter, caption, height=.48):
    return (r'\begin{figure}[!ht]\centering'+'\n'+
            r'\includegraphics[width=\linewidth,height='+str(height)+r'\textheight,keepaspectratio]{figures/'+FIGURES[letter]+'}\n'+
            r'\caption{'+tex(caption)+'}\n'+r'\end{figure}'+'\n')


def build(root):
    inp=Inputs(root)
    audit=inp.json('workload-analysis-final/audit.json')
    require(audit['status']=='passed' and audit['total_runs']==135 and audit['total_requests']==23760,'workload audit incomplete')
    inp.checked_hashes(root/'workload-analysis-final',audit['outputs_sha256'])
    workload=inp.csv('workload-analysis-final/per_cell.csv')
    mixed=inp.csv('workload-analysis-final/mixed_qos_per_cell.csv')
    require(len(workload)==45 and len(mixed)==18 and all(int(r['runs'])==3 for r in workload+mixed),'unexpected measured design')
    weights_meta=inp.json('weights-02/manifest.json');weights_validation=inp.json('weights-02/validation.json')
    require(weights_validation['verified_runs']==1980 and weights_validation['independent_fifo_score_replay']=='PASS','weight replay incomplete')
    inp.checked_hashes(root/'weights-02',weights_meta['artifact_sha256'])
    model=inp.json('model-01/summary.json');milp=inp.json('model-01/milp_validation.json')
    require(milp['status']=='passed' and milp['instances_checked']==120 and milp['all_objectives_match'] and milp['all_solver_gaps_zero'],'MILP corroboration incomplete')
    inp.checked_hashes(root/'model-01',milp['input_sha256'])
    for key in ('A','C','F'):inp.figure_manifest('fig'+key)
    for filename in FIGURES.values():inp.file('figures/'+filename)
    ipc=inp.csv('figures/figA_ipc.csv');ranking=inp.csv('figures/figA_ranking.csv')
    ttl=inp.csv('figures/figC_ttl.csv');weights=inp.csv('figures/figC_weights.csv')
    require(len(ipc)==9 and len(ranking)==20 and len(ttl)==9,'A/C measured inputs incomplete')
    fault_meta=inp.json('fault-01/manifest.json')
    fault_validation=inp.json('fault-01/validation.json')
    require(fault_meta['status']=='completed' and len(fault_meta['runs'])==6,'fault adapter matrix incomplete')
    inp.checked_hashes(root/'fault-01',fault_meta['artifact_sha256'])
    # The B validator/plotter is independently implemented; retain its full
    # validation object in provenance instead of inventing a summary schema.
    require(fault_validation.get('status')=='PASS' and fault_validation.get('verified_runs')==6
            and fault_validation.get('verified_requests')==3780
            and fault_validation.get('half_open_phase_and_full_windows') is True,'fault adapter validation incomplete')
    ttl_meta=inp.json('ttl-matrix-01/matrix.json')
    require(ttl_meta['status']=='completed' and len(ttl_meta['jobs'])==9
            and all(j['status']=='completed' and j['successes']==j['requests']==12 for j in ttl_meta['jobs']), 'TTL outcomes incomplete')
    fault=inp.csv('figures/figB_summary.csv')
    require(len(fault)==6,'expected two policies by three phases')
    inp.figure_manifest('figB')

    tables=root/'tables';tables.mkdir(exist_ok=True)
    sort=lambda r:({'cpu':0,'memory':1,'io':2,'mixed-qos':3}[r['workload']],int(r['rps']),{'tide':0,'bare':1,'kubernetes':2}[r['system']])
    wr=[]
    for row in sorted(workload,key=sort):
        wr.append([row['workload'],row['rps'],SYSTEM[row['system']],
                   stored_interval(row,'on_time_goodput'),stored_interval(row,'success_yield',100),
                   stored_interval(row,'p95_ms',1,1)])
    write_table(tables,'D_workload_results',['Workload','Req/s','System','Goodput (req/s)','Success yield (%)','Success p95 (ms)'],wr)
    mr=[]
    for row in sorted(mixed,key=lambda r:(int(r['rps']),int(r['qos_ms']),r['system'])):
        mr.append([row['rps'],row['qos_ms'],SYSTEM[row['system']],stored_interval(row,'on_time_goodput'),
                   stored_interval(row,'on_time_yield',100),stored_interval(row,'success_yield',100)])
    write_table(tables,'D_mixed_classes',['Total req/s','QoS (ms)','System','Class goodput (req/s)','On-time yield (%)','Success yield (%)'],mr)
    ig=summarize_groups(ipc,'padding_bytes',['request_wire_bytes_mean','rtt_mean_us','rtt_p95_us','roundtrips_per_second'])
    write_table(tables,'A_ipc',['Padding (B)','Mean request wire (B)','Mean RTT (us)','RTT p95 (us)','Round trips/s'],
                [[k,number(v['request_wire_bytes_mean']['mean']),interval(v['rtt_mean_us']),interval(v['rtt_p95_us']),interval(v['roundtrips_per_second'],places=0)] for k,v in ig.items()])
    rg=summarize_groups(ranking,'candidates',['ns_per_op'])
    write_table(tables,'A_ranking',['Candidates','Repetitions','Hook cost (us/op), mean [95% CI]'],
                [[k,v['ns_per_op']['n'],interval(v['ns_per_op'],.001,3)] for k,v in rg.items()])
    tg=summarize_groups(ttl,'ttl_ms',['creations','idle_reserved_cpu_s','later_burst_first_latency_ms','max_heartbeat_gap_ms'])
    write_table(tables,'C_ttl',['TTL (ms)','Created stubs','Idle reservation (CPU s)','Later-burst first latency (ms)'],
                [[k,number(v['creations']['mean'],0),interval(v['idle_reserved_cpu_s'],places=3),interval(v['later_burst_first_latency_ms'])] for k,v in tg.items()])
    wg=defaultdict(list)
    for row in weights:wg[(int(row['rps']),row['network'])].append(float(row['mean_yield_pct']))
    write_table(tables,'C_weight_ranges',['Offered req/s','Network profile','Smallest grid mean yield (%)','Largest grid mean yield (%)'],
                [[rate,net,number(min(values),4),number(max(values),4)] for (rate,net),values in sorted(wg.items())])
    high=[row for row in workload if int(row['rps'])==max(int(r['rps']) for r in workload if r['workload']==row['workload'])]
    cpu=[];memory=[]
    for row in sorted(high,key=sort):
        cpu.append([row['workload'],row['rps'],SYSTEM[row['system']],stored_interval(row,'worker_cpu_pct_2cpu'),stored_interval(row,'controller_cpu_pct_1cpu')])
        field='tide_cgroup_memory_mean_mib' if row['system']=='tide' else 'worker_rss_mean_mib'
        memory.append([row['workload'],SYSTEM[row['system']],stored_interval(row,'controller_rss_mean_mib'),
                       'cgroup memory.current' if row['system']=='tide' else 'worker process RSS',stored_interval(row,field)])
    write_table(tables,'E_cpu',['Workload','Req/s','System','Worker CPU (% of 2 CPUs)','Controller CPU (% of 1 CPU)'],cpu)
    write_table(tables,'E_memory',['Workload','System','Controller RSS (MiB)','Worker-memory scope','Scope-specific memory (MiB)'],memory)
    write_table(tables,'F_optimum',['Tasks','Nodes','Instances','Mean ratio','Minimum ratio','Mean gap','Maximum gap','Exact matches'],
                [[r['tasks'],r['nodes'],r['instances'],number(r['mean_heuristic_over_optimum'],4),number(r['min_heuristic_over_optimum'],4),number(r['mean_objective_gap'],3),r['max_objective_gap'],r['heuristic_matches_optimum']] for r in model])
    # Preserve B's exact checked table rather than infer metric names/units.
    b_table=inp.file('figures/figB_table.tex').read_text()
    (tables/'B_adapter.tex').write_text(b_table)
    (tables/'B_adapter.csv').write_text(inp.file('figures/figB_summary.csv').read_text())

    cpu40={r['system']:r for r in workload if r['workload']=='cpu' and int(r['rps'])==40}
    low_high={net:(min(wg[(40,net)]),max(wg[(40,net)])) for net in ('low','high')}
    captions={letter:inp.file('figures/fig'+letter+'_caption.txt').read_text().strip() for letter in ('A','B','C','F')}
    context=r'''\documentclass[10pt,a4paper]{article}
\usepackage[margin=0.65in]{geometry}
\usepackage{graphicx,booktabs,longtable,array,amsmath,amssymb,hyperref}
\hypersetup{colorlinks=true,urlcolor=blue,linkcolor=blue}
\setlength{\parindent}{0pt}\setlength{\parskip}{5pt}
\setlength{\emergencystretch}{2em}
\newcommand{\evidence}[1]{\textbf{Evidence type:} #1.}
\newcommand{\tabsource}[1]{\par{\footnotesize Machine-readable table: \nolinkurl{#1}.}}
\begin{document}
\begin{center}{\LARGE Supplementary revision experiments}\par
{\large Measured kernels, explicit observation windows, and bounded model claims}\par
{September 28, 2026}\end{center}
\section*{Evidence map and measurement definitions}
This standalone supplement documents additional experiments and their limits. It does not modify the submitted manuscript or rebuttal. The evidence comprises three different kinds of result: measured single-host runtime experiments, measured real-kernel execution through an HTTP fault adapter, and explicitly restricted simulations. Their outcomes are not pooled into an overall superiority claim.
\begin{center}\small
\begin{tabular}{lp{11cm}}\toprule
Panel & Evidence and coverage\\\midrule
A & Actual ranking hook, two-process PAIX round trips, and controller/lifecycle observations; not a full scheduling-cost decomposition.\\
B & Six real-kernel HTTP adapter runs under connected/disconnected/recovered gate phases; not native Tide distributed recovery.\\
C & 1,980 ideal-estimation selection simulations, plus nine measured local used-stub TTL runs.\\
D/E & 135 measured workload/load runs, 23,760 offered request records, three systems and three repetitions per cell.\\
F & 120 restricted offline instances, assignment certificates, and independent zero-gap MILP corroboration.\\\bottomrule
\end{tabular}\end{center}
For D/E, arrivals are selected from the explicit \texttt{run.json} interval $[t_0,t_1)$ before classifying outcomes. Every offered request remains in the denominator, including rejection, failure, timeout and late success. No request warmup is removed. If $T=t_1-t_0$ and $Q_i$ is the recorded per-request QoS budget,
\[
\begin{aligned}
 G&=\frac{\#\{i:\mathrm{success}_i,\ t_i^{reply}\in[t_0,t_1),\ L_i\leq Q_i\}}{T},\\[3pt]
 Y_s&=\frac{\#\mathrm{successful\ cohort\ requests}}{N},\qquad
 Y_q=\frac{\#\mathrm{on\mbox{-}time\ cohort\ requests}}{N}.
\end{aligned}
\]
The two yields include drained outcomes; window goodput does not count completions at or after $t_1$. Successful-latency mean/p95 are conditional on success, including drain, and must be read with yields and failure counts. Table entries are means with two-sided 95\% Student-$t$ intervals over three run metrics, unless a table explicitly states another convention. Request records are not pooled across repetitions to estimate confidence intervals. Intervals with three runs can be wide and can extend beyond the physical support of a metric; no interval endpoints are silently clipped.
CPU utilization is actual sampled CPU consumption divided by elapsed window time and by a budget of two logical CPUs. Controller CPU uses a separate one-CPU denominator. Counter values are linearly interpolated at both exact epoch-window boundaries only when samples bracket them; extrapolation is rejected. Approximately 100-ms sampling cannot resolve unsampled peaks.
The ordinary CPU/memory/I/O runs use 8-s windows, offered rates 5/10/20/40 requests/s and 500-ms QoS. Mixed-QoS runs use the same CPU kernel, 6-s windows and rates 20/40/80 requests/s, alternating 100/500-ms classes by offered request ID. Shared compiled kernels and workload parameters are retained in manifests. Kubernetes denotes a single hostNetwork Pod; neither Ray nor KubeEdge is evaluated.
\clearpage
\section*{A. Measured control-path components}
\evidence{real execution and hook microbenchmarks}
'''
    context+=figure('A',captions['A'],.42)
    context+=r'''\input{tables/A_ipc.tex}
\input{tables/A_ranking.tex}
PAIX includes request serialization, POSIX message-queue transport, a minimal SHA256 kernel and response decoding. Its 50 declared warmup exchanges per run are excluded; all 3,000 declared measured exchanges are retained. These microsecond RTTs are not a scheduler-throughput result. The actual ranking hook is measured five times at each candidate count; its cost omits network state dissemination, RPC and container startup. Initial executor-plus-stub creation and rebinding a retained executor are distinct operations.
Matched low-load end-to-end and worker-service observations expose time outside the kernel. That residual includes waiting, IPC and runtime work, and is not labeled pure control-plane cost. Synchronous \texttt{SubmitRoutine} timing is separately available in CSV. Controller CPU/RSS in D/E starts at the request measurement window, excluding launcher/executor setup.
\clearpage
\section*{B. Response-loss behavior through an explicit adapter}
\evidence{measured real kernels through an HTTP scheduling/fault adapter}
'''
    context+=figure('B',captions['B'],.36)
    context+=r'''{\small\centering\input{tables/B_adapter.tex}\par}
The table gives three-run means [observed minimum, maximum], not confidence intervals; failures are mean counts. Zero disconnected-window goodput does not imply zero cohort success: some entry-only requests complete after recovery, and their nonzero cohort yield is retained.
The run-owned remote HTTP gate cancels in-flight response waits and rejects new calls while disconnected. Remote kernels may finish after response loss; worker logs and client outcomes are checked separately, and execution slots stay occupied until actual completion. No retry is enabled.
Enabling local eligibility permits feasible local-first admission; disabling it leaves the allocated local worker unused. This is not an equal-active-capacity scheduler comparison. Policies use paired offered traces. Cohort yield follows submission phases; goodput follows completion phases and actual gate durations.
This adapter experiment does not establish physical partition behavior, native Tide gRPC/event-return recovery, exactly-once execution or a universal completion guarantee.
\clearpage
\section*{C. Weight selection and local TTL sensitivity}
\evidence{restricted online simulation for weights; measured local runtime for TTL}
'''
    context+=figure('C',captions['C'],.50)
    context+=r'''\input{tables/C_weight_ranges.tex}
The weight sweep includes all 66 nonnegative triples at step 0.1, summing to one; 15 saved Poisson traces are reused at every weight and both stipulated network profiles. The origin is entry-only, with three existing serial-executor candidates and fixed resource features. Deterministic predicted service equals simulated actual service, so accepted requests complete on time by construction. Color/yield variation therefore measures ideal-estimation selection sensitivity, not measured reliability or learning.
'''
    context+=('At 40 requests/s, five-seed mean admission yield ranges from '+number(low_high['low'][0],4)+' to '+number(low_high['low'][1],4)+r'\% for the low-network profile and '+number(low_high['high'][0],4)+' to '+number(low_high['high'][1],4)+r'\% for the high-network profile. The flat low-load results and modest observed differences are retained. No generally optimal weight or approximation guarantee follows.'+'\n')
    context+=r'''The authoritative source is \texttt{weights-02}, with strict half-open completion-window accounting. Independent replay checked all 1,980 runs and 934,560 decisions. Earlier \texttt{weights-01} is not used for final statistics.
\clearpage
\section*{C (continued). What the TTL experiment releases}
The nine TTL runs execute real CPU kernels through normal \texttt{SubmitRoutine}/PAIX paths. Each run has three bursts of four requests at 20 requests/s; all 108 request outcomes are successful. Every run uses post-outcome idle gaps of 250/1000/2500 ms. The TTL values are 100/500/2000 ms, each with three independent process runs. The last gap exceeds every tested TTL, allowing release to be observed.
\input{tables/C_ttl.tex}
The idle allocation quantity is an integral of observed high-priority logical CPU reservations, in CPU-seconds. It is computed from runtime heartbeat observations and explicit idle phase boundaries; it is neither actual CPU consumption nor an amount of reclaimed memory. Event and inventory records verify stub stop identities and a retained worker/container identity. Smaller TTLs can release reservations earlier and require more local stub creation, while the app executor remains alive.
The later-burst first-response statistic combines recreation and retention cases exactly as observed for each TTL. Arrival-to-response includes local creation when needed, a disclosed 5-ms settle, container-inventory RPCs used by this instrumented driver, and normal scheduling/IPC. It is not pure cold-start cost, container launch latency or distributed reconstruction latency. A request marked as using an existing stub can wait behind another request's creation; \texttt{ensure\_stub\_ns} retains this distinction.
\textbf{Interpretation boundary.} The measured tradeoff is idle logical reservation versus instrumented local first-response behavior with a retained executor. Container eviction, container-memory reclamation and native distributed originating-stub reconstruction are not established by these records. Heartbeat sampling limits the timing precision of the observed allocation integral.
\tabsource{tables/C_ttl.csv}
\clearpage
\section*{D. Workload and mixed-deadline results}
\evidence{measured single-host runtime comparisons}
'''
    context+=figure('D','CPU, memory, I/O and mixed-QoS results. Each curve retains all measured cells and three repetitions. Goodput counts on-time completions inside the observation window; yields and successful p95 include drain. Bands are run-level 95% t intervals. Mixed QoS uses the CPU kernel with alternating 100/500-ms classes; there is no priority queue.',.61)
    context+=('At 40 CPU requests/s, the observed mean window goodput is '+number(cpu40['tide']['on_time_goodput_mean'])+' requests/s for Tide, '+number(cpu40['bare']['on_time_goodput_mean'])+' for Bare and '+number(cpu40['kubernetes']['on_time_goodput_mean'])+' for Kubernetes. This observed disadvantage is retained rather than filtered or replaced by a favorable aggregate. Successful latency alone cannot summarize deadline reliability under overload.\n')
    context+=r'''The baseline policy is serial HTTP FCFS with a queue-inclusive external timeout and no predictive QoS admission. Tide uses its local runtime admission/dispatch path. These are different mechanisms, and a smaller successful-latency p95 can accompany rejection or timeout. The experiment does not isolate every runtime mechanism or establish cluster-placement performance.
\textbf{I/O qualification.} The kernel performs the same fsync/readback work, but Bare uses host \texttt{/tmp} while containers use writable-overlay \texttt{/tmp}. The resulting storage-path difference is a confound, so I/O differences are not attributed solely to scheduling. Kubernetes uses host networking because the existing CNI was unhealthy; no HPA behavior is tested.
\clearpage
\section*{D. Complete workload table}
All 45 cells are reported. Each entry is a run-level mean [95\% CI], $n=3$. Success yield includes drained successful requests; p95 is calculated per run on successful requests and then averaged. Goodput and latency units are shown in the headings. These summaries do not pool individual requests across runs.
\input{tables/D_workload_results.tex}
\tabsource{tables/D_workload_results.csv}
\clearpage
\section*{D. Mixed-QoS class accounting}
Every offered ID is audited against its alternating deadline and matching function name. Each class has half the offered requests, but its goodput denominator remains the full common 6-s observation window. Class goodputs therefore add to overall mixed goodput; class yields use their own offered-request denominators. A successful 100-ms-class response taking 150 ms is late even if it would satisfy the 500-ms class. No common default deadline replaces per-row budgets.
\input{tables/D_mixed_classes.tex}
Entries are means [95\% CI] across the three runs in each cell. On-time yield includes admitted requests completing after the window if their individual latency still meets QoS; window goodput excludes them. The experiment varies deadlines rather than introducing priority scheduling or claiming isolation between classes.
\tabsource{tables/D_mixed_classes.csv}
\clearpage
\section*{E. Actual CPU consumption and memory scopes}
\evidence{sampled Linux resource counters aligned to measured request windows}
'''
    context+=figure('E','Worker CPU consumption is divided by two logical CPUs for every system. Tide uses the exclusive parent cgroup usage counter; Bare/Kubernetes use worker process CPU ticks and the recorded clock frequency. Controller process CPU and RSS are separate. Curves and bands are run means with 95% t intervals.',.31)
    context+=r'''\input{tables/E_cpu.tex}
The table reports each workload's highest tested load (40 requests/s for homogeneous workloads, 80 for mixed QoS). A worker consuming one full logical CPU appears as 50\% of the two-CPU budget. These quantities are measured consumption, not reserved capacity, allocated CPU shares or a sum of configured quotas. Parent-cgroup attribution requires exclusive experiment use, as declared by the runner. Startup is outside the request window; first in-window requests remain included.
\tabsource{tables/E_cpu.csv}
\clearpage
\section*{E (continued). Memory is not one interchangeable metric}
\input{tables/E_memory.tex}
Values are run means [95\% CI] of time-weighted window observations, in MiB, at the same highest loads as the CPU table. Controller RSS always refers to the measured controller process. The baseline controller is the HTTP load generator; the Tide controller also performs scheduling/runtime work. Their footprints are useful scoped measurements, not an equal-function cost decomposition.
The final column deliberately retains different worker-memory scopes: Tide's \texttt{memory.current} includes the cgroup's charged memory and is \emph{not} process RSS. Baseline worker RSS describes its process resident set. These numbers must not be ranked as a like-for-like memory comparison or used to infer memory savings. The CSV keeps distinct columns rather than silently merging the two definitions.
'''
    optional=root/'figures/figE_timeseries.pdf'
    if optional.exists():
        inp.file('figures/figE_timeseries.pdf')
        context+=r'\begin{figure}[!ht]\centering\includegraphics[width=\linewidth,height=.28\textheight,keepaspectratio]{figures/figE_timeseries.pdf}\caption{CPU time series at the highest tested rate for each workload. Each point represents its preceding 0.5-s bin. Bands are pointwise run-level 95\% t intervals, not simultaneous bands.}\end{figure}'+'\n'
    context+=r'''\clearpage
\section*{F. Restricted offline optimality gap}
\evidence{simulated offline instances with exact cardinality optimization}
'''
    context+=figure('F',captions['F'],.43)
    context+=r'''\input{tables/F_optimum.tex}
All tasks arrive at zero and share a deadline $H$. Each node has one serial executor with a fixed two-CPU quota. Service ticks are positive deterministic inputs; network is a fixed response budget, not serialized executor work. Every task has unit importance. An assignment is feasible when the service sum on each used node plus its network budget is at most $H$. The exact objective maximizes accepted task count, allowing rejection.
The branch-and-bound bound pools residual capacities and grants each remaining task its cheapest feasible cost. This relaxes placement and thus provides an optimistic bound. Assignment certificates establish feasibility; exhaustive sound pruning establishes optimality within this restricted model. Independent SciPy/HiGHS MILP corroboration matched all 120 objectives with zero solver gaps and retains per-instance status, bounds and assignments.
'''
    context+=('The heuristic matches the optimum in '+str(milp['summary']['heuristic_matches_optimum'])+'/120 instances. Mean per-instance ratio is '+number(milp['summary']['mean_heuristic_over_optimum'],6)+', observed minimum '+number(milp['summary']['min_heuristic_over_optimum'],3)+', and maximum count gap '+str(milp['summary']['max_objective_gap'])+'. These are finite-instance observations, not a general Problem 1 optimum or a constant-factor approximation guarantee. Horizons are regenerated for each node count; those panels are not a fixed-deadline capacity-scaling comparison.\n')
    context+=r'''\clearpage
\section*{Limitations, validation and artifact provenance}
\textbf{Repetitions and uncertainty.} Most measured cells have only three process runs. Student-$t$ intervals describe run variability under this test setup, not broad deployment uncertainty. Weight-model variability comes from five generated traces per rate. F varies generated instances; its solver compute times are not runtime request latency. No observed numerical result is substituted by a target curve or desired percentage.
\textbf{Comparison scope.} Shared kernels improve workload matching, but Tide PAIX and HTTP FCFS have different admission/communication paths. Host and overlay I/O paths differ. Kubernetes is a single hostNetwork Pod, not KubeEdge, Ray, HPA or a cluster-placement experiment. No aggregated figure supports uniform Tide superiority.
\textbf{Failure and lifecycle scope.} B uses an HTTP scheduling adapter, response cancellation and a restored gate. It does not prove native Tide cross-node fault recovery, retry safety or exactly-once results. TTL releases observed logical reservations while retaining the app executor; instrumented recreation contains settle/inventory overhead and is not container cold-start cost.
\textbf{Model scope.} C weights assume ideal known service and network budgets, so accepted jobs meet their deadline by construction. F assumes zero arrivals, a shared deadline, fixed quotas and deterministic service. Neither establishes performance under stale/noisy state, full resource allocation, general Problem 1, or a universal approximation ratio.
\textbf{Validation chain.} Measured workload audit checks matrix/job completion, outer exit status, timeout flags, recorded file hashes, frozen binary identities, raw offered IDs and counts, outcome totals, explicit windows and mandatory resource-counter bracketing. Mixed runs also check deadline classes/function names and effective configuration. A/C figure manifests hash their audited raw inputs. B independently checks gate phases, paired traces, client outcomes and actual worker logs. Weight decisions are independently replayed, and F is independently solved by MILP.
\begin{small}
\begin{longtable}{lp{11.4cm}}\toprule
Evidence & Authoritative artifact\\\midrule
A & \nolinkurl{control-matrix-01/matrix.json}; \nolinkurl{figures/figA_manifest.json}\\
B & \nolinkurl{fault-01/manifest.json}; \nolinkurl{fault-01/validation.json}; \nolinkurl{figures/figB_manifest.json}\\
C weights & \nolinkurl{weights-02/manifest.json}; \nolinkurl{weights-02/validation.json}\\
C TTL & \nolinkurl{ttl-matrix-01/matrix.json}; \nolinkurl{figures/figC_manifest.json}\\
D/E & \nolinkurl{workload-analysis-final/audit.json}; raw matrices listed therein\\
F & \nolinkurl{model-01/manifest.json}; \nolinkurl{model-01/milp_validation.json}\\
This document & \nolinkurl{supplement_manifest.json}; \nolinkurl{tables/}\\\bottomrule
\end{longtable}\end{small}
Rebuild from the Tide module with \nolinkurl{scripts/revision/build_supplement.py}; compile \nolinkurl{revision_experiments.tex} from the result-root directory with Tectonic. The supplement manifest records source/output hashes and does not alter any original experiment manifest. This document reports evidence and limitations; it does not replace missing native distributed experiments with adapter or model claims.
\end{document}
'''
    output=root/'revision_experiments.tex';output.write_text(context)
    outputs=[output]+sorted(tables.glob('*.csv'))+sorted(tables.glob('*.tex'))
    report={'status':'completed','created_utc':datetime.now(timezone.utc).isoformat(),
            'generator_sha256':sha(Path(__file__)),'input_sha256':inp.hashes,
            'outputs_sha256':{str(p.relative_to(root)):sha(p) for p in outputs},
            'scope':'standalone supplementary evidence; no edits to submitted main.tex or rebuttal',
            'verified_measured_workload_runs':audit['total_runs'],'verified_measured_workload_requests':audit['total_requests'],
            'weight_model_runs':weights_validation['verified_runs'],'restricted_optimum_instances':milp['instances_checked'],
            'fault_validation':fault_validation,'uncertainty':'run-level 95% t intervals except separately labeled B convention'}
    (root/'supplement_manifest.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print(f'Generated {output} and {len(outputs)-1} table files; original experiment manifests unchanged.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('results-revision-20260928'))
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args()
    required=['workload-analysis-final/audit.json','weights-02/validation.json','model-01/milp_validation.json',
              'figures/figA_manifest.json','figures/figC_manifest.json','figures/figB_manifest.json',
              'figures/figB_summary.csv','figures/figB_table.tex','fault-01/manifest.json','fault-01/validation.json']
    required+=['figures/'+name for name in FIGURES.values()]
    missing=[name for name in required if not (args.root/name).is_file()]
    if args.check:
        print(json.dumps({'ready':not missing,'missing':missing},indent=2));return
    if missing:parser.error('final evidence not yet complete: '+', '.join(missing))
    build(args.root)


if __name__=='__main__':main()
