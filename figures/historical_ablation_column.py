"""Render all 105 historical ratios at native single-column width."""
from pathlib import Path
import hashlib,json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def render_historical_ablation(data, figsize=(3.5,2.4), colors=None):
    from matplotlib.ticker import FixedLocator, FixedFormatter, NullFormatter
    colors=colors or ['#24608a','#b15943','#55805e']
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':6.5,'axes.titlesize':7,
        'axes.labelsize':6.5,'xtick.labelsize':6,'ytick.labelsize':6,'legend.fontsize':5.7,
        'axes.linewidth':.5,'lines.linewidth':.8,'pdf.fonttype':42,'axes.axisbelow':True})
    fig,axs=plt.subplots(3,3,figsize=figsize,sharex=True,sharey='row')
    fig.subplots_adjust(left=.16,right=.99,bottom=.15,top=.79,hspace=.21,wspace=.18)
    records=[]
    fields=['mean_latency_ratio_on_off','p95_latency_ratio_on_off','throughput_ratio_on_off']
    for c,group in enumerate(data):
        for r,field in enumerate(fields):
            ax=axs[r,c];ax.axhline(1,color='#777',ls='--',lw=.5)
            for i,line in enumerate(group['lines']):
                xs=[p['rate'] for p in line['points']];ys=[p[field] for p in line['points']]
                assert all(y>0 for y in ys)
                ax.plot(xs,ys,color=colors[i],marker=['o','s','^'][i],ms=2,lw=.7,
                    label=line['condition'].replace(' off','=0').replace(' on','=1').replace(', ',','))
                records.extend({'mechanism':group['feature'],'condition':line['condition'],
                    'rate':x,'metric':field,'value':y} for x,y in zip(xs,ys))
            ax.set_xscale('log',base=2);ax.set_yscale('log')
            ax.set_xticks([1,2,4,8,16],['1','2','4','8','16'])
            ax.tick_params(length=2,width=.5,pad=1);ax.grid(axis='y',alpha=.25,lw=.4)
            ax.spines[['top','right']].set_visible(False)
            if r==0:ax.set_title(['CC','PI','EI'][c],pad=2)
        center=(axs[0,c].get_position().x0+axs[0,c].get_position().x1)/2
        fig.legend(*axs[0,c].get_legend_handles_labels(),loc='upper center',
            bbox_to_anchor=(center,.995),frameon=False,fontsize=5.5,
            handlelength=1,handletextpad=.2,labelspacing=.2,borderaxespad=0)
    for ax,label in zip(axs[:,0],['Mean\nlatency','P95\nlatency','Throughput']):
        ax.set_ylabel(label,labelpad=2)
    for ax in axs[2,:]:
        ax.yaxis.set_major_locator(FixedLocator([.5,1,2,4]))
        ax.yaxis.set_major_formatter(FixedFormatter(['0.5','1','2','4']))
        ax.yaxis.set_minor_formatter(NullFormatter())
    fig.supxlabel('Offered requests/s (log); ratios on/off',y=.025,fontsize=6.5)
    assert len(records)==105
    for row in axs:
        lo,hi=row[0].get_ylim()
        for ax in row:
            for line in ax.lines:assert min(line.get_ydata())>=lo and max(line.get_ydata())<=hi
    fig._historical_ablation_values=records
    return fig


def main():
    here=Path(__file__).resolve().parent;source=here/'archived_ablation_replot.json'
    meta=json.loads((here/'DATA_PROVENANCE.json').read_text())['archived_ablation_replot.json']
    assert hashlib.sha256(source.read_bytes()).hexdigest()==meta['sha256']
    fig=render_historical_ablation(json.loads(source.read_text())['comparisons'])
    out=here.parent/'revision-figs/evaluation/archived_ablation_main.pdf'
    fig.savefig(out);fig.savefig(out.with_suffix('.png'),dpi=180)
    (here/'HISTORICAL_ABLATION_MAIN_VALUES.json').write_text(json.dumps(fig._historical_ablation_values,indent=2)+'\n')
    plt.close(fig)


if __name__=='__main__':main()
