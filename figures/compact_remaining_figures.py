"""Plot control costs and archived results from unchanged summaries.
Use --control-only to regenerate the current Fig. 14 without archived figures.
"""
from pathlib import Path
import csv,hashlib,json,sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import LogLocator,NullFormatter,MaxNLocator
HERE=Path(__file__).resolve().parent;OUT=HERE/'rendered/evaluation'
meta=json.loads((HERE/'DATA_PROVENANCE.json').read_text())
CONTROL_SOURCE='control_management_fig14.csv'
CONTROL_RATES=[10,20,40,80]
for name in [CONTROL_SOURCE,'archived_ablation_replot.json','archived_latency_replot.json']:
 assert hashlib.sha256((HERE/name).read_bytes()).hexdigest()==meta[name]['sha256']
plt.rcParams.update({'font.size':6.5,'axes.titlesize':7,'axes.labelsize':6.5,'xtick.labelsize':6,
 'ytick.labelsize':6,'legend.fontsize':6,'axes.linewidth':.5,'lines.linewidth':.8,
 'pdf.fonttype':42,'savefig.dpi':200,'axes.axisbelow':True})
records={}
rows=list(csv.DictReader((HERE/CONTROL_SOURCE).open()))
from fig14_management import render_fig14_management
fig, control = render_fig14_management(rows)
fig.savefig(OUT/'control_pressure_compact.pdf')
fig.savefig(OUT/'control_pressure_compact.png', dpi=180)
plt.close(fig)
records['control']=control
if '--control-only' in sys.argv:
 path=HERE/'REMAINING_PLOTTED_VALUES.json'
 previous=json.loads(path.read_text())
 previous['control']=control
 path.write_text(json.dumps(previous,indent=2)+'\n')
 print('Replotted Fig. 14: two stacked management/runtime panels with paired-block intervals at DDL=200 ms.')
 sys.exit(0)

ablation=json.loads((HERE/'archived_ablation_replot.json').read_text())['comparisons']
fig,axs=plt.subplots(1,3,figsize=(3.5,1.95),sharex=True,sharey=True)
fig.subplots_adjust(left=.14,right=.99,bottom=.35,top=.86,wspace=.18)
colors=['#24608a','#b15943','#55805e'];markers=['o','s','^']
for ax,group,title in zip(axs,ablation,['CC','PI','EI']):
 ax.axhline(1,color='#777',ls='--',lw=.6)
 for i,line in enumerate(group['lines']):
  ax.plot([p['rate'] for p in line['points']],[p['mean_latency_ratio_on_off'] for p in line['points']],color=colors[i],marker=markers[i],markersize=2,label=line['condition'].replace(' off','=0').replace(' on','=1').replace(', ',','))
 ax.set_title(title,pad=3);ax.set_xscale('log',base=2);ax.set_xticks([1,2,4,8,16],['1','2','4','8','16']);ax.tick_params(length=2,width=.5,pad=1)
 ax.set_yscale('log');ax.set_ylim(.015,3.5);ax.grid(axis='y',alpha=.3,lw=.4)
 ax.spines[['top','right']].set_visible(False)
 ax.legend(loc='upper center',bbox_to_anchor=(.5,-.28),frameon=False,fontsize=5.7,handlelength=.9,handletextpad=.2,labelspacing=.2,borderaxespad=0)
axs[0].set_ylabel('Latency ratio (on/off)',labelpad=2)
fig.supxlabel('Offered requests/s (log scale)',y=.22,fontsize=6.5)
fig.savefig(OUT/'archived_ablation_compact.pdf');plt.close(fig);records['ablation']=ablation

latency=json.loads((HERE/'archived_latency_replot.json').read_text())['series']
fig,axs=plt.subplots(1,2,figsize=(3.5,2.05),sharex=True,sharey=True)
fig.subplots_adjust(left=.14,right=.985,bottom=.36,top=.87,wspace=.18)
colors=['#be3d35','#4d5965','#237bb5','#78b7d2','#55936b','#8873a9'];markers=['o','s','^','v','D','P']
for ax,field,title in zip(axs,['mean_ms','p95_ms'],['Mean','95th percentile']):
 for series,color,marker in zip(latency,colors,markers):
  ax.plot([p['rate'] for p in series['points']],[p[field] for p in series['points']],label=series['name'],color=color,marker=marker,markersize=2)
 ax.set_yscale('log');ax.set_ylim(70,90000);ax.set_xticks([5,20,35,50]);ax.set_title(title,pad=3)
 ax.grid(axis='y',alpha=.3,lw=.4);ax.tick_params(length=2,width=.5,pad=1)
 ax.yaxis.set_minor_locator(LogLocator(base=10,subs=(2,5)));ax.yaxis.set_minor_formatter(NullFormatter())
 ax.spines[['top','right']].set_visible(False)
axs[0].set_ylabel('Latency (ms)',labelpad=2);fig.supxlabel('Offered requests/s',y=.23,fontsize=6.5)
fig.legend(*axs[0].get_legend_handles_labels(),loc='lower center',ncol=3,frameon=False,fontsize=5.8,handlelength=1.1,handletextpad=.25,columnspacing=.8,labelspacing=.3)
fig.savefig(OUT/'archived_latency_compact.pdf');plt.close(fig);records['latency']=latency
(HERE/'REMAINING_PLOTTED_VALUES.json').write_text(json.dumps(records,indent=2)+'\n')
print('Replotted control costs (18 values + intervals at DDL=200 ms) and archived ablation/latency figures.')
