"""Executor elasticity and lifecycle measurements."""
from pathlib import Path
import argparse,hashlib,json,statistics,math,ast
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.transforms import Bbox


def lifecycle_summary(records):
 groups=[]
 keys=['executor_acquisition','binding','worker_ms','other_control','residual']
 for mode in ['cold','warm','steady']:
  blocks=[]
  for block in range(1,31):
   selected=[r for r in records if r['kind']=='lifecycle' and int(r['block'])==block and (int(r['index'])>0 if mode=='steady' else int(r['index'])==0 and int(r['initial'])==(0 if mode=='cold' else 1))]
   assert len(selected)==(18 if mode=='steady' else 1)
   d={k:statistics.mean(float(r[k]) for r in selected) for k in ['latency_ms','executor_acquisition','binding','worker_ms','external_ms','ipc_residual_ms']}
   d['other_control']=statistics.mean(sum(float(r[k]) for k in ['adapter_input','admission','dispatch_wait','resource_wait','handoff','result_delivery','adapter_output']) for r in selected)
   d['residual']=d['external_ms']+d['ipc_residual_ms'];assert abs(sum(d[k] for k in keys)-d['latency_ms'])<1e-8;blocks.append(d)
  groups.append(blocks)
 stages=['executor_acquisition','binding','worker_ms','other_overhead']
 values={k:[statistics.mean(d['other_control']+d['residual'] if k=='other_overhead' else d[k] for d in g) for g in groups] for k in stages}
 totals=[statistics.mean(d['latency_ms'] for d in g) for g in groups];ci=[2.0452296421327*statistics.stdev(d['latency_ms'] for d in g)/math.sqrt(30) for g in groups]
 assert np.allclose(np.array(list(values.values())).sum(axis=0),totals)
 return dict(values=values,totals=totals,ci95_half=ci,blocks=groups,records=600,repeats=30)


def load_data(project):
 root=Path(project)/'RSEP/results-issue30-elastic-allocation-20261101';raw=(root/'plot_data.json').read_bytes();manifest=json.loads((root/'ARTIFACT_SHA256.json').read_text())['files']['plot_data.json'];assert hashlib.sha256(raw).hexdigest()==manifest['sha256']
 elastic=json.loads(raw)
 nb=json.loads((Path(project)/'TMC_slimmed/slimmed_experiment_figures.ipynb').read_text());records=None
 for c in nb['cells']:
  if c['cell_type']!='code' or not ''.join(c['source']).startswith('DATA = json.loads('):continue
  statement=ast.parse(''.join(c['source'])).body[0];records=json.loads(ast.literal_eval(statement.value.args[0]))['lifecycle'];break
 assert records is not None and len(records)==600
 compact=dict(runs=elastic['runs'],timelines={name:[{k:r.get(k) for k in ['time_s','valid','additional_cpus','ready_cpus']} for r in rows] for name,rows in elastic['timelines'].items()})
 return dict(elastic=compact,lifecycle=lifecycle_summary(records),provenance=dict(elastic_plot_data_sha256=manifest['sha256'],lifecycle_raw_sha256=hashlib.sha256(json.dumps(records,sort_keys=True).encode()).hexdigest(),scope='Independent three-host YOLOX elasticity and single-host Go lifecycle experiments; no causal HPA-delay decomposition.'))


def sample_grid(rows,grid,key,baseline,age):
 t=np.array([r['time_s'] for r in rows]);v=np.array([r.get(key) if r['valid'] and r.get(key) is not None else np.nan for r in rows],float);i=np.searchsorted(t,grid,side='right')-1;j=np.clip(i,0,len(t)-1);out=v[j]-baseline;out[(i<0)|(grid>t[-1])|(grid-t[j]>age)]=np.nan;return out


def render_pair(data,figsize=(3.5,1.55)):
 style={'font.family':'DejaVu Sans','font.size':6.5,'font.weight':'normal','axes.labelsize':6.5,'axes.labelweight':'normal','xtick.labelsize':6,'ytick.labelsize':6,'text.color':'black','axes.labelcolor':'black','xtick.color':'black','ytick.color':'black','axes.linewidth':.5,'pdf.fonttype':42,'ps.fonttype':42}
 with plt.rc_context(style):
  fig=plt.figure(figsize=figsize)
  left=fig.add_axes([.10,.245,.34,.535]);right=fig.add_axes([.63,.245,.355,.535])
  grid=np.arange(-20,420.01,.1);curves={}
  left.axvspan(0,60,color='#e5f1fb',lw=0,zorder=0)
  left.text(30,24.6,'Load on',ha='center',va='bottom',fontsize=6,color='black',clip_on=False)
  for arm,label,color in [('tide','RSEP','#2875a3'),('hpa','K8s HPA','#bd7847')]:
   runs=[r for r in data['elastic']['runs'] if r['arm']==arm];assert len(runs)==3
   values=[sample_grid(data['elastic']['timelines'][r['run']],grid,'additional_cpus',0,1 if arm=='tide' else 3) for r in runs]
   samples=np.stack(values);valid=np.isfinite(samples).all(axis=0)
   median=np.full(grid.shape,np.nan);low=median.copy();high=median.copy()
   median[valid]=np.median(samples[:,valid],axis=0)
   low[valid]=np.min(samples[:,valid],axis=0);high[valid]=np.max(samples[:,valid],axis=0)
   left.fill_between(grid,low,high,step='post',color=color,alpha=.16,lw=0,zorder=1)
   left.step(grid,median,where='post',color=color,lw=1.05,label=label,zorder=3)
   last=np.flatnonzero(np.isfinite(median))[-1]
   if any(r['recovery_right_censored'] for r in runs):
    left.plot(grid[last],median[last],marker='s',mfc='white',mec=color,mew=.8,ms=3.1,linestyle='none',clip_on=False,zorder=4)
    left.annotate('Not recovered',xy=(grid[last],median[last]),xytext=(410,10.55),
                  ha='right',va='bottom',fontsize=6,color='black',
                  arrowprops={'arrowstyle':'->','color':'#666666','lw':.45,'shrinkA':2,'shrinkB':3})
   curves[arm]={'time_s':grid.tolist(),'median':median.tolist(),'min':low.tolist(),'max':high.tolist()}
  left.set(xlim=(-20,420),ylim=(-.5,24),xticks=[0,60,180,300,420],yticks=[0,10,20])
  left.get_xticklabels()[-1].set_ha('right')
  left.set_xlabel('Time (s)',labelpad=2);left.set_ylabel('Extra reserved CPUs',labelpad=2)
  left.tick_params(axis='x',labelsize=6)
  fig.legend(*left.get_legend_handles_labels(),loc='upper center',bbox_to_anchor=(.275,.985),ncol=2,
             fontsize=6,frameon=False,handlelength=1.15,handletextpad=.35,columnspacing=.9,borderaxespad=0)
  y=np.arange(3);total=np.zeros(3)
  for key,label,color in [('executor_acquisition','Acquire','#F4B6B6'),('binding','Bind','#F7E6A2'),('worker_ms','Execute','#BFE3BE'),('other_overhead','Other','#D9DDE3')]:
   vals=data['lifecycle']['values'][key];right.barh(y,vals,left=total,height=.49,label=label,color=color,edgecolor='#666666',linewidth=.25);total+=np.asarray(vals)
  assert np.allclose(total,data['lifecycle']['totals'])
  err=data['lifecycle']['ci95_half'];right.errorbar(total,y,xerr=err,fmt='none',color='#222222',lw=.6,capsize=1.6)
  right.set(xlim=(0,210),ylim=(2.55,-.55),xticks=[0,100,200],yticks=y)
  right.set_yticklabels(['Cold','Precreated','Reuse'],rotation=-30,ha='right',va='center',rotation_mode='anchor',fontsize=6)
  right.set_xlabel('Latency (ms)',labelpad=2)
  for i,(val,error) in enumerate(zip(total,err)):right.text(val+error+3,i,f'{val:.1f}',fontsize=6,va='center')
  fig.legend(*right.get_legend_handles_labels(),loc='upper center',bbox_to_anchor=(.79,.985),ncol=2,
             fontsize=6,frameon=False,handlelength=.95,handletextpad=.35,columnspacing=.8,labelspacing=.35,borderaxespad=0)
  for ax in [left,right]:
   ax.grid(axis='y' if ax is left else 'x',color='#dedede',lw=.35);ax.set_axisbelow(True)
   ax.spines[['top','right']].set_visible(False);ax.tick_params(length=2,width=.5,pad=1.5)
  fig.text(.27,.045,'(a) Resource elasticity',ha='center',fontsize=6)
  fig.text(.79,.045,'(b) First use / reuse',ha='center',fontsize=6)
  fig._elasticity_curves=curves
  fig._lifecycle_values=data['lifecycle']
  return fig


def save_pair(fig,base):
 base=Path(base);base.parent.mkdir(parents=True,exist_ok=True)
 
 with plt.rc_context({'pdf.fonttype':42,'ps.fonttype':42}):
  for ext in ['pdf','png','svg']:fig.savefig(base.with_suffix('.'+ext),dpi=300)
  
  
  width,height=fig.get_size_inches()
  for panel,x in [('a',0),('b',width/2)]:fig.savefig(base.with_name(base.name+'_'+panel).with_suffix('.pdf'),bbox_inches=Bbox.from_bounds(x,0,width/2,height))

if __name__ == '__main__':
 artifact = Path(__file__).resolve().parents[2]
 parser = argparse.ArgumentParser()
 parser.add_argument('--data',type=Path,default=artifact/'data/compact/current/elasticity/plot_data.json')
 parser.add_argument('--out',type=Path,default=artifact/'figures/rendered/current/elasticity_lifecycle_pair')
 args=parser.parse_args()
 data=json.loads(args.data.read_text())
 fig=render_pair(data)
 save_pair(fig,args.out)
 plt.close(fig)
