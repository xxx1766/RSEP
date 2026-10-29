"""Reflow existing measurements for a 3.5-inch IEEE column; no new experiments.
Run: python plotting/compact_figures.py (requires numpy and matplotlib).
"""
from pathlib import Path
import csv, hashlib, json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

HERE=Path(__file__).resolve().parent
OUT=HERE/'rendered/evaluation'
for name, meta in json.loads((HERE/'DATA_PROVENANCE.json').read_text()).items():
    assert hashlib.sha256((HERE/name).read_bytes()).hexdigest()==meta['sha256']
plt.rcParams.update({'font.size':7,'axes.titlesize':7,'axes.labelsize':7,
 'xtick.labelsize':6.5,'ytick.labelsize':6.5,'legend.fontsize':6.5,
 'axes.linewidth':.5,'lines.linewidth':.8,'pdf.fonttype':42,
 'axes.axisbelow':True,'savefig.dpi':180})
systems=['bare','kubeedge','ray','tide']
labels=['Bare','KubeEdge','Ray Serve','Tide']
colors=['tab:blue','tab:orange','tab:green','tab:red']
cell=list(csv.DictReader((HERE/'per_cell.csv').open()))
mixed=list(csv.DictReader((HERE/'mixed_per_cell.csv').open()))
plotted=[]
def result(table,**keys):
    rows=[r for r in table if r['metric']=='on_time_goodput' and all(r[k]==str(v) for k,v in keys.items())]
    assert len(rows)==1,keys
    r=rows[0];mean=float(r['mean']);lo=float(r['ci95_low']);hi=float(r['ci95_high'])
    plotted.append(dict(keys,mean=mean,ci95_low=lo,ci95_high=hi))
    return mean,max(0,mean-lo),max(0,hi-mean)
def panels():
    fig,axs=plt.subplots(1,2,figsize=(3.5,1.55),sharey=True)
    fig.subplots_adjust(left=.135,right=.99,bottom=.24,top=.73,wspace=.16)
    axs[0].set_ylabel('On-time goodput (req/s)',labelpad=2)
    for ax in axs:
        ax.grid(axis='y',alpha=.25,lw=.4)
        ax.tick_params(length=2,pad=1.5,width=.5)
    return fig,axs
fig,axs=panels()
for ax,rate,letter in zip(axs,[20,40],'ab'):
    x=np.arange(3)
    for i,system in enumerate(systems):
        values=np.array([result(cell,system=system,workload=w,rps=rate) for w in ['cpu','memory','io']])
        ax.bar(x+(i-1.5)*.20,values[:,0],.20,yerr=values[:,1:].T,capsize=1,
               error_kw={'elinewidth':.5,'capthick':.5},color=colors[i],label=labels[i])
    ax.set_xticks(x,['CPU','Memory','I/O']);ax.set_title(f'({letter}) {rate} req/s',pad=3)
axs[0].set_ylim(bottom=0)
assert axs[0].get_ylim()[1] >= 40
handles,names=axs[0].get_legend_handles_labels()
fig.legend(handles,names,loc='upper center',bbox_to_anchor=(.53,1.01),ncol=4,frameon=False,
           handlelength=1.1,handletextpad=.3,columnspacing=.7)
fig.savefig(OUT/'figD1_heterogeneous_compact.pdf');plt.close(fig)
fig,axs=panels()
for ax,qos,letter in zip(axs,[100,500],'ab'):
    for i,system in enumerate(systems):
        values=np.array([result(mixed,system=system,rps=r,qos_ms=qos) for r in [20,40,80]])
        ax.errorbar([20,40,80],values[:,0],yerr=values[:,1:].T,marker='o',markersize=2,
                     capsize=1,elinewidth=.5,color=colors[i],label=labels[i])
    ax.plot([20,40,80],[10,20,40],':',color='black',lw=.7,label='Offered / 2')
    ax.set_xticks([20,40,80]);ax.set_title(f'({letter}) {qos} ms deadline',pad=3)
fig.supxlabel('Total offered rate (req/s)',y=.015,fontsize=7)
axs[0].set_ylim(bottom=0)
assert axs[0].get_ylim()[1] >= 40
handles,names=axs[0].get_legend_handles_labels()
fig.legend(handles,names,loc='upper center',bbox_to_anchor=(.53,1.025),ncol=3,frameon=False,
           handlelength=1.2,handletextpad=.3,columnspacing=.9,labelspacing=.15)
fig.subplots_adjust(top=.65)
fig.savefig(OUT/'figD2_mixed_qos_compact.pdf');plt.close(fig)

# Three existing preference panels; all six profiles remain in Supplementary Fig. S1.
from calibration_panels import render_calibration_panels, write_panel_values

data=json.loads((HERE/'plot_data.json').read_text())
fig,calibration_panels=render_calibration_panels(data)
fig.savefig(OUT/'objective_weight_three_compact.pdf')
fig.savefig(OUT/'objective_weight_three_compact.png',dpi=180)
fig.savefig(OUT/'objective_weight_three_compact.svg')
write_panel_values(calibration_panels,OUT/'objective_weight_three_compact.csv')
plt.close(fig)
# Keep the original equal-profile summary fields for consumers of this ledger.
equal_panel=calibration_panels[0]
lam=np.array(equal_panel['lambda']);wi=equal_panel['selected_weight_id']
grid=np.array(equal_panel['grid']);z=np.array(equal_panel['objective'])
(HERE/'PLOTTED_VALUES.json').write_text(json.dumps({'fourway_cells':plotted,'calibration':{'lambda':lam.tolist(),'weight_id':wi,'grid':grid.tolist(),'objective':z.tolist(),'all_six_selected_weights':[data['grid'][s['WeightID']] for s in data['selection']]}},indent=2)+'\n')
print('Replotted 48 existing four-way cells and three existing calibration surfaces; all six selections agree.')
