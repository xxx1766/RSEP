"""Restore verifiable historical means/P95 without reconstructing distributions."""
from pathlib import Path
import hashlib,json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def render_historical_latency(data, figsize=(3.5,2.4), colors=None):
    from matplotlib.ticker import LogLocator, NullFormatter
    colors=colors or ['#be3d35','#4d5965','#237bb5','#78b7d2','#55936b','#8873a9']
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':6.5,'axes.titlesize':7,
        'axes.labelsize':6.5,'xtick.labelsize':6,'ytick.labelsize':6,'legend.fontsize':5.8,
        'axes.linewidth':.5,'lines.linewidth':.8,'pdf.fonttype':42,'axes.axisbelow':True})
    fig,axs=plt.subplots(1,2,figsize=figsize,sharex=True,sharey=True)
    fig.subplots_adjust(left=.14,right=.985,bottom=.17,top=.81,wspace=.18)
    records=[]
    for ax,field,title in zip(axs,['mean_ms','p95_ms'],['Mean','95th percentile']):
        for series,color,marker in zip(data,colors,['o','s','^','v','D','P']):
            xs=[p['rate'] for p in series['points']];ys=[p[field] for p in series['points']]
            ax.plot(xs,ys,label=series['name'],color=color,marker=marker,markersize=2,lw=.8)
            records.extend(dict(series=series['name'],rate=x,metric=field,value=y) for x,y in zip(xs,ys))
        ax.set_yscale('log');ax.set_ylim(70,90000);ax.set_xticks([5,20,35,50]);ax.set_title(title,pad=3)
        ax.grid(axis='y',alpha=.3,lw=.4);ax.tick_params(length=2,width=.5,pad=1)
        ax.yaxis.set_minor_locator(LogLocator(base=10,subs=(2,5)));ax.yaxis.set_minor_formatter(NullFormatter())
        ax.spines[['top','right']].set_visible(False)
        for line in ax.lines:assert min(line.get_ydata())>=70 and max(line.get_ydata())<=90000
    axs[0].set_ylabel('Reported latency (ms)',labelpad=2)
    fig.supxlabel('Offered requests/s',y=.025,fontsize=6.5)
    fig.legend(*axs[0].get_legend_handles_labels(),loc='upper center',
        bbox_to_anchor=(.54,.995),ncol=3,frameon=False,
        fontsize=5.8,handlelength=1.1,handletextpad=.25,columnspacing=.8,labelspacing=.3)
    assert len(records)==120
    fig._historical_latency_values=records
    return fig


def main():
    here=Path(__file__).resolve().parent;source=here/'archived_latency_replot.json'
    meta=json.loads((here/'DATA_PROVENANCE.json').read_text())['archived_latency_replot.json']
    assert hashlib.sha256(source.read_bytes()).hexdigest()==meta['sha256']
    fig=render_historical_latency(json.loads(source.read_text())['series'])
    out=here.parent/'revision-figs/evaluation/archived_latency_main.pdf'
    fig.savefig(out);fig.savefig(out.with_suffix('.png'),dpi=180)
    (here/'HISTORICAL_LATENCY_MAIN_VALUES.json').write_text(json.dumps(fig._historical_latency_values,indent=2)+'\n')
    plt.close(fig)


if __name__=='__main__':main()
