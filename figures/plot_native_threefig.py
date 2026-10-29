"""Editable plotting functions for accepted native three-figure measurements.

Data are supplied explicitly; this module neither runs workloads nor picks runs.
"""
import json,pathlib
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

COLORS={'edge':'#c67b39','cloud':'#3178a8','origin':'#498b66'}
T95_DF2=4.302652729911275

def mean_ci(values):
    a=np.asarray(values,dtype=float)
    assert len(a)==3
    return float(a.mean()),float(T95_DF2*a.std(ddof=1)/np.sqrt(3))

def setup():
    plt.rcdefaults();plt.rcParams.update({'font.family':'DejaVu Sans','font.size':8,'axes.labelsize':8,'axes.titlesize':8,'legend.fontsize':7,'xtick.labelsize':7,'ytick.labelsize':7,'axes.linewidth':.5,'lines.linewidth':.9,'pdf.fonttype':42})

def render_goodput(data,figsize=(6.5,2.25)):
    setup();fig,axes=plt.subplots(1,2,figsize=figsize);fig.subplots_adjust(left=.085,right=.985,bottom=.23,top=.84,wspace=.32)
    cells=data['sweep'];rates=sorted({r['rate_rps'] for r in cells});plotted={}
    for ax,key,title,color in zip(axes,['effective_throughput_rps','goodput_rps'],['(a) Completion throughput','(b) 350 ms goodput'],[COLORS['cloud'],COLORS['edge']]):
        stats=[mean_ci([r[key] for r in cells if r['rate_rps']==rate]) for rate in rates]
        means=[s[0] for s in stats];errors=[s[1] for s in stats]
        ax.plot([0,50],[0,50],':',color='.55',lw=.75,label='Offered rate')
        ax.errorbar(rates,means,yerr=errors,fmt='o-',ms=3,lw=1,capsize=2,color=color,label='Native Tide')
        ax.set(xlabel='Offered rate (req/s)',ylabel='Completed requests/s',title=title,xlim=(2,52),ylim=(0,55),xticks=[5,15,25,35,45,50]);ax.grid(axis='y',alpha=.2,lw=.5)
        ax.set_ylim(min(0,min(m-e for m,e in stats)-1),max(55,max(m+e for m,e in stats)+1))
        ax.legend(loc='upper left',frameon=False,fontsize=6.8)
        plotted[key]=dict(rates=rates,mean=means,ci95_halfwidth=errors)
    return fig,plotted

def render_placement(data,figsize=(3.5,2.25)):
    setup();fig,ax=plt.subplots(figsize=figsize);fig.subplots_adjust(left=.17,right=.98,bottom=.23,top=.84)
    cells=data['sweep'];rates=sorted({r['rate_rps'] for r in cells});plotted={}
    for role in ['edge','cloud','unknown','failed']:
        vals=[[100*r['placement_all_offered'][role]['fraction'] for r in cells if r['rate_rps']==rate] for rate in rates]
        if role in ['unknown','failed'] and all(v==0 for group in vals for v in group):continue
        stats=[mean_ci(v) for v in vals];mean=[s[0] for s in stats];err=[s[1] for s in stats]
        ax.errorbar(rates,mean,yerr=err,fmt='o-' if role=='edge' else 's--',ms=3,capsize=2,color=COLORS.get(role,'.4'),label=role.capitalize())
        plotted[role]=dict(rates=rates,mean=mean,ci95_halfwidth=err)
    ax.set(xlabel='Offered rate (req/s)',ylabel='Share of all offered requests (%)',xlim=(2,52),ylim=(-5,105),xticks=[5,15,25,35,45,50],yticks=[0,25,50,75,100]);ax.grid(axis='y',alpha=.2,lw=.5)
    tails=[(m-h,m+h) for series in plotted.values() for m,h in zip(series['mean'],series['ci95_halfwidth'])]
    ax.set_ylim(min(-5,min(a for a,b in tails)-1),max(105,max(b for a,b in tails)+1))
    ax.legend(loc='upper center',bbox_to_anchor=(.5,1.22),ncol=3,frameon=False)
    return fig,plotted

def render_processors(data,figsize=(6.5,2.65)):
    setup();fig,axes=plt.subplots(1,3,figsize=figsize,sharey=True);fig.subplots_adjust(left=.075,right=.985,bottom=.22,top=.76,wspace=.17)
    plotted={};maximum=max(q['committed_cpus'] for item in data['timelines'] for q in item['aggregate'] if q['valid'])
    for repeat,ax in enumerate(axes,1):
        item=next(row for row in data['timelines'] if row['repeat']==repeat);rows=item['aggregate']
        assert all(not(left['valid'] and right['offset_s']-left['offset_s']>.150000001) for left,right in zip(rows,rows[1:])), 'Missing explicit cadence-gap marker'
        x=[r['offset_s'] for r in rows]
        committed=[r['committed_cpus'] if r['valid'] else np.nan for r in rows]
        reserved=[r['reserved_cpus'] if r['valid'] else np.nan for r in rows]
        ax.step(x,committed,where='post',color=COLORS['cloud'],lw=1.,label='Committed (incl. borrowing)')
        ax.step(x,reserved,where='post',color=COLORS['edge'],lw=.95,ls='--',label='Reserved')
        plotted[str(repeat)]=dict(offset_s=x,committed=committed,reserved=reserved)
        ax.axvspan(0,60,color='.8',alpha=.25,zorder=-2);ax.axvline(0,color='.5',lw=.5);ax.axvline(60,color='.5',lw=.5)
        ax.set(title=f'Block {repeat}',xlabel='Time from load start (s)',xlim=(min(-10,min(x)),max(90,item['end_offset_s'],max(x))),ylim=(0,max(28,maximum+2)),yticks=list(range(0,int(max(28,maximum+2))+1,4)),xticks=[0,30,60,90]);ax.grid(axis='y',alpha=.2,lw=.5)
    axes[0].set_ylabel('Total logical CPUs')
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    handles=[Line2D([0],[0],color=COLORS['cloud'],label='Committed (incl. borrowing)'),Line2D([0],[0],color=COLORS['edge'],ls='--',label='Reserved'),Patch(facecolor='.8',alpha=.25,label='25 req/s offered')]
    fig.legend(handles=handles,loc='upper center',ncol=3,bbox_to_anchor=(.5,1.01),frameon=False,fontsize=7)
    return fig,plotted

def export(data,out):
    out=pathlib.Path(out);out.mkdir(parents=True,exist_ok=True);values={}
    for name,fn in [('native_goodput',render_goodput),('native_processors',render_processors),('native_placement',render_placement)]:
        fig,values[name]=fn(data);fig.savefig(out/(name+'.pdf'));fig.savefig(out/(name+'.png'),dpi=180);plt.close(fig)
    (out/'plotted-values.json').write_text(json.dumps(values,indent=2,allow_nan=True))
if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--data',type=pathlib.Path,required=True);p.add_argument('--out',type=pathlib.Path,required=True);a=p.parse_args();export(json.loads(a.data.read_text()),a.out)
