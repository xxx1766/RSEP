"""Restore all three archived ablation metrics from existing case summaries."""
from pathlib import Path
import hashlib,json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
HERE=Path(__file__).resolve().parent
meta=json.loads((HERE/'DATA_PROVENANCE.json').read_text())['archived_ablation_replot.json']
p=HERE/'archived_ablation_replot.json';assert hashlib.sha256(p.read_bytes()).hexdigest()==meta['sha256']
data=json.loads(p.read_text())['comparisons']
fields=['mean_latency_ratio_on_off','p95_latency_ratio_on_off','throughput_ratio_on_off']
plt.rcParams.update({'font.size':8,'axes.titlesize':9,'axes.labelsize':8,'xtick.labelsize':7.5,'ytick.labelsize':7.5,'legend.fontsize':7,'pdf.fonttype':42,'axes.axisbelow':True})
fig,axs=plt.subplots(3,3,figsize=(6.5,5.6),sharex=True,sharey='row')
fig.subplots_adjust(left=.14,right=.99,bottom=.20,top=.94,hspace=.20,wspace=.18)
records=[]
for c,group in enumerate(data):
 for r,field in enumerate(fields):
  ax=axs[r,c];ax.axhline(1,color='#777',ls='--',lw=.7)
  for i,line in enumerate(group['lines']):
   xs=[p['rate'] for p in line['points']];ys=[p[field] for p in line['points']]
   assert all(y>0 for y in ys)
   ax.plot(xs,ys,color=['#24608a','#b15943','#55805e'][i],marker=['o','s','^'][i],ms=3,lw=1,label=line['condition'])
   records.extend({'mechanism':group['feature'],'condition':line['condition'],'rate':x,'metric':field,'value':y} for x,y in zip(xs,ys))
  ax.set_xscale('log',base=2);ax.set_yscale('log');ax.set_xticks([1,2,4,8,16],['1','2','4','8','16']);ax.grid(axis='y',alpha=.25)
  ax.tick_params(length=2,pad=2);ax.spines[['top','right']].set_visible(False)
  if r==0:ax.set_title(group['feature'])
  if r==2:ax.set_xlabel('Offered requests/s (log)')
 axs[2,c].legend(loc='upper center',bbox_to_anchor=(.5,-.48),frameon=False,handlelength=1.2,handletextpad=.3,labelspacing=.25,borderaxespad=0)
for ax,label in zip(axs[:,0],['Mean latency ratio\nLower is better','P95 latency ratio\nLower is better','Throughput ratio\nHigher is better']):ax.set_ylabel(label)
for row in axs:
 lo,hi=row[0].get_ylim()
 for ax in row:
  for line in ax.lines:assert min(line.get_ydata())>=lo and max(line.get_ydata())<=hi
out=HERE.parent/'revision-figs/evaluation/archived_ablation_full_metrics.pdf';fig.savefig(out);plt.close(fig)
(HERE/'HISTORICAL_ABLATION_VALUES.json').write_text(json.dumps(records,indent=2)+'\n')
print('Restored mean/P95/throughput ratios: 105 points from archived summaries, no invented uncertainty.')
