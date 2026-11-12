"""Native ablation and concurrency-control performance gain."""
from pathlib import Path
import argparse
import json
import math
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator

RATES=[1,2,4,8,16,32]
CELLS=['C1','C2','C3','C4','C5','C6']  
LABELS=['M1','M2','M3','M4','M5','M6']  


def absolute_rows(payload):
    assert payload['status']=='VERIFIED_ALL_PRESPECIFIED_RUNS'
    assert payload['runs']==180 and payload['offered']==37800 and payload['blocks']==5
    assert payload['valid']+payload['failed']==payload['offered']
    assert len(payload['cells'])==36
    assert {(c['cell'],c['rps']) for c in payload['cells']}=={(c,r) for c in CELLS for r in RATES}
    rows=[]
    for c in payload['cells']:
        assert c['repetitions']==5
        row=dict(cell=c['cell'],rps=c['rps'],offered=c['offered'],valid=c['valid'],success_yield=c['valid']/c['offered'])
        for target,source in [('avg_ms','mean_ms'),('p95_ms','p95_ms'),('std_ms','sd_ms'),('throughput','throughput_rps')]:
            row[target+'_mean']=c[source+'_mean']
            low,high=c[source+'_ci95_low'],c[source+'_ci95_high']
            row[target+'_ci95_half']=(high-low)/2 if low is not None and high is not None else None
        rows.append(row)
    return rows


def render_ablation_main(rows, figsize=(7.16, 3.0), colors=None):
    from matplotlib.patches import Patch
    from matplotlib.ticker import FuncFormatter, MaxNLocator
    colors = colors or ['#b5b5b5', '#e3a839', '#75a868', '#4d88bf', '#df7063', '#8d69b5']
    cells = ['C1', 'C2', 'C3', 'C4', 'C5', 'C6']
    rates = [1, 2, 4, 8, 16, 32]
    metrics = [('avg_ms_mean', 'Mean latency (ms)'),
               ('p95_ms_mean', 'P95 latency (ms)'),
               ('std_ms_mean', 'Latency SD (ms)'),
               ('throughput_mean', 'Throughput (req/s)')]
    values = {(int(r['rps']), r['cell']): r for r in rows}
    assert len(rows) == 36 and set(values) == {
        (r, c) for r in rates for c in cells}
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 5.7,
                         'axes.linewidth': .45, 'lines.linewidth': .65,
                         'pdf.fonttype': 42, 'axes.axisbelow': True})

    def tick_text(value, position):
        if value == 0:
            return '0'
        if .001 <= abs(value) < 100000:
            return f'{value:g}'
        mantissa, exponent = f'{value:.2e}'.split('e')
        return f"{mantissa.rstrip('0').rstrip('.')}e{int(exponent)}"

    fig = plt.figure(figsize=figsize)
    outer = fig.add_gridspec(2, 3, left=.053, right=.993, top=.925,
                             bottom=.065, wspace=.23, hspace=.23)
    plotted = []
    for idx, rate in enumerate(rates):
        inner = outer[idx // 3, idx % 3].subgridspec(
            3, 2, height_ratios=[.12, 1, 1], wspace=.40, hspace=.50)
        title_ax = fig.add_subplot(inner[0, :])
        title_ax.axis('off')
        title_ax.text(.5, .5, f'Offered rate: {rate} req/s', ha='center',
                      va='center', fontsize=6.3, fontweight='normal')
        for mi, (metric, title) in enumerate(metrics):
            ax = fig.add_subplot(inner[1 + mi // 2, mi % 2])
            ci_key = metric.removesuffix('_mean') + '_ci95_half'
            y = [values[rate, c][metric] for c in cells]
            ci = [values[rate, c][ci_key] for c in cells]
            valid = [i for i, v in enumerate(y) if v is not None]
            assert all(ci[i] is not None and math.isfinite(float(ci[i]))
                       and float(ci[i]) >= 0 for i in valid)
            ax.bar(valid, [float(y[i]) for i in valid],
                   color=[colors[i] for i in valid], width=.72,
                   yerr=[float(ci[i]) for i in valid],
                   error_kw={'elinewidth': .45, 'capsize': 1.1, 'capthick': .45})
            for i, value in enumerate(y):
                if value is None:
                    ax.text(i, .05, 'undefined', rotation=90,
                            transform=ax.get_xaxis_transform(),
                            ha='center', va='bottom', fontsize=5)
                plotted.append({'rps': rate, 'cell': cells[i], 'metric': metric,
                                'mean': value, 'ci95_half': ci[i]})
            ax.set_title(title, fontsize=5.7, pad=2)
            ax.set_xticks(range(6), LABELS, fontsize=5.1)
            ax.tick_params(axis='both', labelsize=5.1, length=1.5, width=.45, pad=1)
            if mi < 2:
                ax.tick_params(axis='x', labelbottom=False, bottom=False)
            ax.set_ylim(bottom=min([0] + [float(y[i]) - float(ci[i]) for i in valid]))
            ax.grid(axis='y', alpha=.25, lw=.35)
            ax.yaxis.set_major_locator(MaxNLocator(nbins=3, min_n_ticks=2))
            ax.yaxis.set_major_formatter(FuncFormatter(tick_text))
            ax.spines[['top', 'right']].set_visible(False)
    descriptions = ['CC off / PI off / EI off', 'CC on / PI off / EI off',
                    'CC off / PI off / EI on', 'CC on / PI off / EI on',
                    'CC off / PI on / EI on', 'CC on / PI on / EI on']
    fig.legend(handles=[Patch(facecolor=colors[i], label=f'{LABELS[i]}: {descriptions[i]}')
                        for i in range(len(cells))], ncol=6, loc='upper center',
               bbox_to_anchor=(.5, .998), fontsize=5.8, frameon=False,
               handlelength=.9, handletextpad=.3, columnspacing=1.2,
               labelspacing=.3, borderaxespad=0)
    fig._ablation_main_values = plotted
    return fig


def switch_gain_records(payload, mechanism):
    settings = {
        'CC': [('C1','C2','PI off, EI off'), ('C3','C4','PI off, EI on'), ('C5','C6','PI on, EI on')],
        'PI': [('C4','C6','CC on, EI on'), ('C3','C5','CC off, EI on')],
        'EI': [('C2','C4','CC on, PI off'), ('C1','C3','CC off, PI off')],
    }
    records=[]
    for off,on,condition in settings[mechanism]:
        for metric in ['mean_ms','p95_ms','throughput_rps']:
            rows=sorted([r for r in payload['contrasts'] if r['off']==off and r['on']==on and r['metric']==metric],key=lambda r:r['rps'])
            assert [r['rps'] for r in rows]==RATES
            for r in rows:
                assert r['ci95_low']>0 and len(r['values'])==5
                if metric=='throughput_rps': m,lo,hi=r['mean'],r['ci95_low'],r['ci95_high']
                else: m,lo,hi=1/r['mean'],1/r['ci95_high'],1/r['ci95_low']
                records.append(dict(mechanism=mechanism,off=off,on=on,condition=condition,rps=r['rps'],metric=metric,
                                    gain_mean=m,ci95_low=lo,ci95_high=hi,original_ratio_mean=r['mean']))
    return records


def render_switch_gain(payload, mechanism='CC', style='bars'):
    
    assert style in ['bars','points']
    records=switch_gain_records(payload,mechanism)
    plt.rcdefaults()
    plt.rcParams.update({'font.family':'DejaVu Serif','font.size':6.1,'axes.linewidth':.5,'pdf.fonttype':42,'ps.fonttype':42})
    fig,axes=plt.subplots(1,3,figsize=(3.5,1.72),sharex=True)
    fig.subplots_adjust(left=.125,right=.99,bottom=.27,top=.76,wspace=.38)
    conditions=list(dict.fromkeys(r['condition'] for r in records))
    colors=(['#ECA0A0','#EED27D','#A3CFA0'] if mechanism=='CC' and style=='points' else ['#3266a8','#cf7a24','#9063aa']);width=.23 if len(conditions)==3 else .31
    for ax,metric,title in zip(axes,['mean_ms','p95_ms','throughput_rps'],['Mean latency','P95 latency','Throughput']):
        selected=[r for r in records if r['metric']==metric]
        lo=2**min(-1,math.floor(math.log2(min(r['ci95_low'] for r in selected)*.92)))
        hi=2**max(1,math.ceil(math.log2(max(r['ci95_high'] for r in selected)*1.08)))
        ax.set_yscale('log',base=2);ax.set_ylim(lo,hi)
        for j,condition in enumerate(conditions):
            data=[r for r in selected if r['condition']==condition]
            assert [r['rps'] for r in data]==RATES
            offset=.18 if style=='points' else width
            x=[i+(j-(len(conditions)-1)/2)*offset for i in range(6)]
            gain=[r['gain_mean'] for r in data]
            errors=[[r['gain_mean']-r['ci95_low'] for r in data],[r['ci95_high']-r['gain_mean'] for r in data]]
            if style=='points':
                point_edge='#666666' if mechanism=='CC' else colors[j]
                ax.errorbar(x,gain,yerr=errors,fmt=['o','s','^'][j],linestyle='none',
                            color=colors[j],ecolor=point_edge,mfc=colors[j],mec=point_edge,ms=3.2,mew=.5,
                            elinewidth=.7,capsize=1.5,capthick=.6,zorder=4,label=condition)
            else:
                ax.bar(x,[g-1 for g in gain],bottom=1,width=width*.86,color=colors[j],edgecolor=colors[j],linewidth=.35,zorder=3,label=condition)
                ax.errorbar(x,gain,yerr=errors,fmt='none',ecolor='#303030',elinewidth=.45,capsize=1.0,capthick=.45,zorder=4)
        if style=='points':
            for boundary in [.5,1.5,2.5,3.5,4.5]:
                ax.axvline(boundary,color='#d1d1d1',lw=.4,zorder=0)
        ax.axhline(1,color='#444',lw=.65,ls='--',zorder=5)
        ticks=([.5,1,4,16,64] if hi>=64 else ([.5,1,2,4,8,16] if lo>=.5 else [.01,.1,1,10]))
        ticks=[t for t in ticks if lo<=t<=hi]
        ax.set_yticks(ticks,[f'{t:g}' for t in ticks]);ax.yaxis.set_minor_locator(matplotlib.ticker.NullLocator())
        ax.set_xticks(range(6),[str(r) for r in RATES]);ax.set_xlim(-.55,5.55)
        ax.set_title(title,fontsize=6.4,pad=3)
        ax.tick_params(length=2,width=.5,pad=1.3,labelsize=5.5)
        ax.grid(axis='y',color='#d8d8d8',lw=.35);ax.set_axisbelow(True)
        ax.spines[['top','right']].set_visible(False)
        assert all(lo<=r['ci95_low']<=r['ci95_high']<=hi for r in selected)
    axes[0].set_ylabel('Performance gain (×)',labelpad=3,fontsize=6.2)
    fig.text(.56,.13 if style=='points' else .055,'Offered load (req/s)',ha='center',fontsize=6.2)
    handles,labels=axes[0].get_legend_handles_labels()
    fig.legend(handles,labels,loc='upper center',bbox_to_anchor=(.55,.925 if style=='points' else .99),ncol=len(conditions),frameon=False,
               fontsize=5.6,handlelength=1.,handletextpad=.35,columnspacing=.85,borderaxespad=0)
    fig._cc_relative_values=records
    fig._switch_gain_values=records
    return fig


def render_cc_relative(payload):
    
    return render_switch_gain(payload,'CC',style='points')


def draw_switch_gains(payload, output_dir):
    output_dir=Path(output_dir);output_dir.mkdir(parents=True,exist_ok=True)
    for mechanism in ['CC','PI','EI']:
        fig=render_switch_gain(payload,mechanism)
        stem=mechanism.lower()+'_switch_gain_bars'
        for suffix in ['pdf','png','svg']:
            fig.savefig(output_dir/(stem+'.'+suffix),dpi=280,bbox_inches='tight',pad_inches=.02)
        (output_dir/(stem+'.json')).write_text(json.dumps(fig._switch_gain_values,indent=2)+'\n')
        plt.close(fig)
    meta={'source':payload['provenance'],'runs':180,'rates':RATES,'main_mechanism':'CC','supplement_mechanisms':['PI','EI'],
          'latency_gain':'1 / mean of five paired enabled/disabled latency ratios',
          'throughput_gain':'mean of five paired enabled/disabled throughput ratios',
          'intervals':'Original pointwise 95% t(4); latency bounds inverted and reversed',
          'bar_baseline':1,'axis':'logarithmic; higher is better; all bounds retained'}
    (output_dir/'provenance.json').write_text(json.dumps(meta,indent=2)+'\n')
    return meta


def draw_all(payload,output_dir):
    output_dir=Path(output_dir);output_dir.mkdir(parents=True,exist_ok=True)
    fig=render_ablation_main(absolute_rows(payload))
    for suffix in ['pdf','png','svg']:fig.savefig(output_dir/('fig5_dynamic_binding_2x3.'+suffix),dpi=240,facecolor='white')
    (output_dir/'fig5_dynamic_binding_2x3.json').write_text(json.dumps(fig._ablation_main_values,indent=2)+'\n')
    plt.close(fig)
    fig=render_cc_relative(payload)
    for suffix in ['pdf','png','svg']:fig.savefig(output_dir/('cc_confirmation_current.'+suffix),dpi=240,bbox_inches='tight',pad_inches=.02)
    (output_dir/'cc_confirmation_current.json').write_text(json.dumps(fig._cc_relative_values,indent=2)+'\n')
    plt.close(fig)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data',type=Path,default=Path(__file__).resolve().parents[2]/'data/compact/current/native_pytorch/plot_data.json')
    parser.add_argument('--output-dir',type=Path,required=True);args=parser.parse_args()
    draw_all(json.loads(args.data.read_text()),args.output_dir)


if __name__=='__main__':main()
