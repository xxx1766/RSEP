from pathlib import Path
import csv,json
p=Path(__file__).resolve().parents[1]; systems=['tide','bare','ray','kubeedge']; out=[]; audit=[]
for filename,label,title,mixed in [('per_cell.csv','tab:full-single-host','Complete homogeneous single-host on-time goodput',False),('mixed_per_cell.csv','tab:full-mixed','Complete mixed-deadline single-host on-time goodput',True)]:
 rows=[r for r in csv.DictReader((p/'figures'/filename).open()) if r['metric']=='on_time_goodput']
 groups=sorted(set((int(r['rps']),int(r['qos_ms'])) if mixed else (r['workload'],int(r['rps'])) for r in rows))
 out.append(r'\begin{table}[htbp]\centering\footnotesize')
 out.append('\\caption{'+title+r' (requests/s). Cells show the three-run mean above its run-level 95\% interval; intervals are reproduced without clipping. '+('Offered rates are totals, split equally between the two deadline classes.' if mixed else 'All cells use a 500-ms deadline.')+'}')
 out.append(r'\label{'+label+'}\n'+r'\begin{tabular}{llrrrr}\toprule')
 out.append(('Total rate & Deadline' if mixed else 'Workload & Rate')+r' & Tide & Bare & Ray Serve & KubeEdge\\\midrule')
 for g in groups:
  cells=[]
  for sys in systems:
   matches=[r for r in rows if r['system']==sys and ((int(r['rps']),int(r['qos_ms'])) if mixed else (r['workload'],int(r['rps'])))==g]
   assert len(matches)==1
   r=matches[0];audit.append({'source':filename,**r})
   cells.append(r'\shortstack{'+f"{float(r['mean']):.2f}"+r'\\{'+f"[{float(r['ci95_low']):.2f}, {float(r['ci95_high']):.2f}]"+'}}')
  lead=(f'{g[0]}/s & {g[1]} ms' if mixed else {'cpu':'CPU','memory':'Memory','io':'I/O'}[g[0]]+f' & {g[1]}/s')
  out.append(lead+' & '+' & '.join(cells)+r'\\[3pt]')
 out.append(r'\bottomrule\end{tabular}\end{table}')
(p/'paper/supplement_tables/single_host_full.tex').write_text('\\section*{Complete single-host goodput results}\n'+ '\n'.join(out)+'\n')
(p/'paper/validation/SUPPLEMENT_GOODPUT_VALUES.json').write_text(json.dumps(audit,indent=2)+'\n')
