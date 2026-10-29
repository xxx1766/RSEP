#!/usr/bin/env python3
"""Paper-sized resource sensitivity and directly measured lifecycle partitions."""
import argparse,csv,json,statistics,math
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
p=argparse.ArgumentParser();p.add_argument('--control',type=Path,required=True);p.add_argument('--lifecycle',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();a.out.mkdir(parents=True,exist_ok=True)
def read(p):
 with p.open() as f:return list(csv.DictReader(f))
rows=read(a.control/'summary.csv');audit=json.loads((a.control/'independent-audit.json').read_text());assert audit['status']=='complete'
lifeaudit=json.loads((a.lifecycle/'audit.json').read_text());assert lifeaudit['lifecycle_blocks']==30 and lifeaudit['perturbation_blocks']==10
plt.rcParams.update({'font.size':7,'axes.titlesize':8,'axes.labelsize':7,'xtick.labelsize':7,'ytick.labelsize':7,'legend.fontsize':6.5,'pdf.fonttype':42})
for scope,stem in [('stack','control_paper'),('framework','control_framework_supplement')]:
 fig,axs=plt.subplots(2,3,figsize=(7.1,3.4),sharex=True)
 for col,system in enumerate(['tide','kubeedge','ray']):
  for ddl,color,marker in [(50,'#B84362','o'),(200,'#D09A16','s'),(1000,'#2875A3','^')]:
   select=sorted([r for r in rows if r['system']==system and int(r['ddl_ms'])==ddl],key=lambda r:int(r['rps']))
   for row,metric in enumerate([scope+'_cpu_pct',scope+'_pss_mib']):
    ax=axs[row,col];means=[float(r[metric]) for r in select];err=[float(r[metric+'_ci95_half']) for r in select]
    ax.errorbar([5,20,80],means,yerr=[[min(m,e) for m,e in zip(means,err)],err],color=color,marker=marker,markersize=3,capsize=2,linewidth=1,label=f'{ddl} ms')
    ax.set_xscale('log',base=2);ax.set_xticks([5,20,80],['5','20','80']);ax.grid(alpha=.2)
    if row==1:ax.set_xlabel('Offered requests/s')
  axs[0,col].set_title({'tide':'Tide','kubeedge':'KubeEdge','ray':'Ray Serve'}[system])
 axs[0,0].set_ylabel('Control + support CPU (%)' if scope=='stack' else 'Framework CPU (%)');axs[1,0].set_ylabel('Control + support PSS (MiB)' if scope=='stack' else 'Framework PSS (MiB)');axs[0,0].legend(title='DDL',title_fontsize=6.5)
 fig.tight_layout(pad=.6,w_pad=1.4,h_pad=.7)
 for ext in ('pdf','png'):fig.savefig(a.out/f'{stem}.{ext}',dpi=240)
 plt.close(fig)
req=read(a.lifecycle/'requests.csv');life=read(a.lifecycle/'lifecycle.csv')
keys=['executor_acquisition','binding','worker_ms','other_control','residual']
colors=['#8A72B5','#D39C38','#3F8C69','#5388B8','#C7858E']
labels=['Executor acquisition','Resource binding / preparation','Kernel execution','Other traced control stages','IPC / transport residual']
groups=[]
for mode in ('cold','warm','steady'):
 blocks=[]
 for b in range(1,31):
  selected=[r for r in req if r['kind']=='lifecycle' and int(r['block'])==b and (int(r['index'])>0 if mode=='steady' else int(r['index'])==0 and int(r['initial'])==(0 if mode=='cold' else 1))]
  d={k:statistics.mean(float(r[k]) for r in selected) for k in ('latency_ms','executor_acquisition','binding','worker_ms','external_ms','ipc_residual_ms')}
  d['other_control']=statistics.mean(sum(float(r[k]) for k in ['adapter_input','admission','dispatch_wait','resource_wait','handoff','result_delivery','adapter_output']) for r in selected)
  d['residual']=d['external_ms']+d['ipc_residual_ms']
  assert abs(sum(d[k] for k in keys)-d['latency_ms'])<1e-8
  blocks.append(d)
 groups.append(blocks)
fig,ax=plt.subplots(figsize=(3.48,3.1));bottom=np.zeros(3);x=np.arange(3)
for k,color,label in zip(keys,colors,labels):
 vals=[statistics.mean(d[k] for d in g) for g in groups];ax.bar(x,vals,bottom=bottom,width=.57,color=color,label=label);bottom+=np.array(vals)
errs=[2.0452296421327*statistics.stdev(d['latency_ms'] for d in g)/math.sqrt(30) for g in groups]
ax.errorbar(x,bottom,yerr=errs,fmt='none',color='#222222',capsize=3,linewidth=.8)
ax.set_xticks(x,['On-demand\nfirst request','Pre-created\nfirst request','Subsequent\nwarm reuse']);ax.set_ylabel('End-to-end latency (ms)');ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True)
handles,legend_labels=ax.get_legend_handles_labels()
fig.legend(handles,legend_labels,loc='upper center',bbox_to_anchor=(.55,.995),frameon=False,fontsize=6.1)
fig.subplots_adjust(left=.15,right=.99,bottom=.15,top=.70)
ax.set_ylim(0,max(bottom)+27)
for i,(total,err) in enumerate(zip(bottom,errs)): ax.text(i,total+err+3,f'{total:.2f}',ha='center',fontsize=7)
for ext in ('pdf','png'):fig.savefig(a.out/f'lifecycle_paper.{ext}',dpi=240)
summary={mode:{k:dict(mean=statistics.mean(d[k] for d in g),sd=statistics.stdev(d[k] for d in g)) for k in keys+['latency_ms']} for mode,g in zip(('cold','warm','steady'),groups)}
(a.out/'lifecycle_plot_values.json').write_text(json.dumps(summary,indent=2)+'\n')
print(a.out)
