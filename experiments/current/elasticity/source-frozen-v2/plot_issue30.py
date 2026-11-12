"""Allocation response from every pre-registered paired trial, never utilization."""
import argparse,json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

def compact_data(data):
 return dict(protocol={k:data['protocol'][k] for k in ['high_rate_rps','baseline_s','duration_s','recovery_s','units_cpu','cpu_per_host','image_manifest','input_sha256','binary_sha256']},runs=data['runs'],timelines={k:[{key:r.get(key) for key in ['time_s','valid','allocated_cpus','additional_cpus','ready_cpus']} for r in rows] for k,rows in data['timelines'].items()},selection=data['selection'],ordinate=data['ordinate'])
def grid_values(rows,grid,key,baseline,max_age):
 t=np.array([r['time_s'] for r in rows]);v=np.array([r.get(key) if r['valid'] and r.get(key) is not None else np.nan for r in rows],dtype=float)
 index=np.searchsorted(t,grid,side='right')-1;indices=np.clip(index,0,len(t)-1);value=v[indices]-baseline
 value[(index<0)|(grid-t[indices]>max_age)]=np.nan
 return value

def render_elasticity(data,figsize=(3.5,1.65)):
 grid=np.arange(-20,420.01,.1);colors={'tide':'#2875a3','hpa':'#bd7847'}
 with plt.rc_context({'font.family':'DejaVu Sans','font.size':7,'axes.labelsize':7,'xtick.labelsize':6.5,'ytick.labelsize':6.5,'axes.linewidth':.5,'pdf.fonttype':42,'ps.fonttype':42}):
  fig,ax=plt.subplots(figsize=figsize);fig.subplots_adjust(left=.14,right=.99,bottom=.23,top=.81)
  ax.axvspan(0,60,color='.9',linewidth=0);ax.text(30,.98,'Load',transform=ax.get_xaxis_transform(),ha='center',va='top',fontsize=6,color='.3')
  for arm,label in [('tide','Tide'),('hpa','K8s HPA')]:
   values=[];readies=[]
   runs=[r for r in data['runs'] if r['arm']==arm];assert len(runs)==3
   for r in runs:
    rows=data['timelines'][r['run']];v=grid_values(rows,grid,'additional_cpus',0,1 if arm=='tide' else 3)
    ax.step(grid,v,where='post',color=colors[arm],alpha=.28,linewidth=.45);values.append(v)
    if arm=='hpa':readies.append(grid_values(rows,grid,'ready_cpus',r['baseline_allocated_cpus'],3))
   stack=np.stack(values);all_observed=np.isfinite(stack).all(axis=0);median=np.full(grid.shape,np.nan);median[all_observed]=np.median(stack[:,all_observed],axis=0)
   ax.step(grid,median,where='post',color=colors[arm],linewidth=.9,label=label)
   if readies:
    ready=np.stack(readies);ok=np.isfinite(ready).all(axis=0);m=np.full(grid.shape,np.nan);m[ok]=np.median(ready[:,ok],axis=0);ax.step(grid,m,where='post',color=colors[arm],linestyle=':',linewidth=.8)
  ax.set(xlim=(-20,420),xlabel='Time from load onset (s)',ylabel='Additional allocated CPUs');ax.set_xticks([0,60,180,300,420]);ax.set_ylim(bottom=-.5);ax.grid(axis='y',linewidth=.4,alpha=.22);ax.spines[['top','right']].set_visible(False);ax.tick_params(length=2,width=.5,pad=2);ax.set_ylabel('Additional allocated CPUs',labelpad=2);ax.set_xlabel('Time from load onset (s)',labelpad=2)
  fig.legend(*ax.get_legend_handles_labels(),loc='upper center',ncol=2,frameon=False,fontsize=7,handlelength=1.6,columnspacing=1.5,borderaxespad=0)
  return fig
if __name__=='__main__':
 root=Path(__file__).resolve().parents[1];p=argparse.ArgumentParser();p.add_argument('--data',type=Path,default=root/'plot_data.json');p.add_argument('--out',type=Path,default=root/'figures/fig7_elastic_allocation');a=p.parse_args();d=compact_data(json.loads(a.data.read_text()));fig=render_elasticity(d);a.out.parent.mkdir(exist_ok=True,parents=True)
 for ext in ['pdf','png','svg']:fig.savefig(a.out.with_suffix('.'+ext),dpi=300)
 a.out.with_suffix('.data.json').write_text(json.dumps(d)+'\n');plt.close(fig)
