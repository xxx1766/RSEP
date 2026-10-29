"""Editable historical redraws from published tables/native-vector summaries.

These are reported historical summaries, not recovered experimental event logs.
Original PDFs are retained separately under revision-figs/historical/.
"""
import math
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.lines import Line2D

HISTORICAL_ORDER = ['Bare Metal','KubeEdge','KubeEdge-2C4C','KubeEdge-3C8C',
                    'Ray Task','Ray-Actor-15','Ray-Actor-20','Ray-Actor-25','Ray-Actor-40','Tide']
HISTORICAL_COLORS = ['#2879ae','#27b4c5','#ff8a28','#eb78c4','#c0bd38',
                     '#ad73dc','#ad5557','#66a84e','#909090','#d62728']
GOODPUT_COLORS = ['#e2f0ed','#8dcec3','#58b6b2','#32978c','#cecee2',
                  '#c5d3e2','#9fc5d6','#72a6c9','#487fb3','#df3c3b']
PLACEMENT_ALIASES = {'KubeEdge-Full':'KubeEdge', 'Ray-Task':'Ray Task'}


def validate_historical(data):
    def require(ok, message):
        if not ok: raise ValueError(message)
    rows = data['placement']
    require(len(rows)==100, 'Expected all 100 placement summaries')
    require({(PLACEMENT_ALIASES.get(r['platform'],r['platform']),int(r['rate_rps_per_client'])) for r in rows}
            =={(p,t) for p in HISTORICAL_ORDER for t in range(5,51,5)}, 'Placement grid incomplete/duplicated')
    for r in rows:
        a,b=float(r['edge_percent']),float(r['cloud_percent'])
        require(0<=a<=100 and 0<=b<=100 and abs(a+b-100)<.001, 'Invalid placement proportions')
    rows=data['allocation']
    require(len(rows)==77, 'Expected all 77 native vector vertices')
    for platform,n in [('KubeEdge-2C4C',15),('KubeEdge-3C8C',13),('Tide',49)]:
        group=[r for r in rows if r['platform']==platform]
        require(len(group)==n and [int(r['vertex_index']) for r in group]==list(range(n)), 'Trace vertices missing/reordered')
        x=[float(r['time_s_from_pdf_vector']) for r in group]
        require(all(math.isfinite(t) and 0<=t<=100 for t in x) and all(a<=b for a,b in zip(x,x[1:])), 'Trace time nonmonotone')
        require(all(0<=float(r['allocated_cpus_from_pdf_vector'])<=36 for r in group), 'Trace CPU range')
    rows=data['goodput']
    require(len(rows)==50 and {(r['platform'],int(r['rate_rps_per_client'])) for r in rows}
            =={(p,t) for p in HISTORICAL_ORDER for t in [10,20,30,40,50]}, 'Goodput grid incomplete/duplicated')
    for r in rows:
        full=float(r['throughput_rps_from_pdf_vector']);good=float(r['goodput_rps_from_pdf_vector'])
        require(math.isfinite(full) and 0<=good<=full, 'Invalid throughput/goodput pair')
        require(str(r['pattern_patch_present']) in ['True','False'], 'Missing hatch-presence provenance')
        require(str(r['pattern_patch_present'])=='True' or good==0, 'Absent hatch must remain zero/unresolved')


def render_placement(data, figsize=(3.5,1.95), colors=None):
    validate_historical(data);colors=HISTORICAL_COLORS if colors is None else colors
    assert len(colors)==10
    fig,axs=plt.subplots(1,2,figsize=figsize)
    fig.subplots_adjust(left=.115,right=.99,bottom=.40,top=.97,wspace=.42)
    markers=['o','s','v','^','x','D','*','d','p','*']
    for ax,metric,title in zip(axs,['edge_percent','cloud_percent'],['Edge execution (%)','Cloud execution (%)']):
        for i,platform in enumerate(HISTORICAL_ORDER):
            rows=sorted([r for r in data['placement'] if PLACEMENT_ALIASES.get(r['platform'],r['platform'])==platform],key=lambda r:int(r['rate_rps_per_client']))
            line,=ax.plot([int(r['rate_rps_per_client']) for r in rows],[float(r[metric]) for r in rows],
                          color=colors[i],marker=markers[i],markersize=2.8,lw=.65,label=platform.replace('Ray-Actor-','Ray-'))
            line._source_platform=rows[0]['platform']
        ax.set_xlabel('RPS per client',labelpad=1.5);ax.set_ylabel(title,labelpad=1.5)
        ax.set_xticks([10,20,30,40,50]);ax.tick_params(length=2,pad=1.5,width=.5)
        ax.grid(alpha=.25,lw=.4);ax.set_xlim(3,52)
    axs[0].set_ylim(-4,104);axs[1].set_ylim(-4,86)
    fig.legend(*axs[0].get_legend_handles_labels(),loc='lower center',ncol=4,frameon=False,
               fontsize=5.0,columnspacing=.8,handletextpad=.3,handlelength=1.1,labelspacing=.35)
    return fig


def render_allocation(data, figsize=(3.5,1.55), colors=None):
    validate_historical(data);colors=colors or ['#0070c0','#008000','#c00000']
    fig,ax=plt.subplots(figsize=figsize);fig.subplots_adjust(left=.13,right=.99,bottom=.27,top=.79)
    for platform,color,style in zip(['KubeEdge-2C4C','KubeEdge-3C8C','Tide'],colors,['-.','--','-']):
        rows=[r for r in data['allocation'] if r['platform']==platform]
        ax.plot([float(r['time_s_from_pdf_vector']) for r in rows],
                [float(r['allocated_cpus_from_pdf_vector']) for r in rows],
                color=color,linestyle=style,lw=.9,label=platform,drawstyle='default')
    ax.set_xlim(-2,102);ax.set_ylim(-1,38);ax.set_xticks(range(0,101,10));ax.set_yticks(range(0,36,5))
    ax.set_xlabel('Time since startup (s)',labelpad=1.5);ax.set_ylabel('Allocated CPUs',labelpad=1.5)
    ax.grid(alpha=.35,lw=.4,linestyle=':');ax.tick_params(length=2,pad=1.5,width=.5)
    fig.legend(*ax.get_legend_handles_labels(),loc='upper center',bbox_to_anchor=(.55,.995),
               ncol=3,frameon=False,fontsize=5.5,columnspacing=.8,handletextpad=.3,handlelength=1.5)
    return fig


def render_goodput(data, figsize=(7.16,1.95), colors=None):
    validate_historical(data);colors=GOODPUT_COLORS if colors is None else colors
    assert len(colors)==10
    fig,axs=plt.subplots(1,5,figsize=figsize)
    fig.subplots_adjust(left=.06,right=.995,bottom=.36,top=.87,wspace=.28)
    ymax=[38.51,76.68,114.19,148.99,187.42]
    ticks=[[0,10,20,30],[0,20,40,60],[0,20,40,60,80,100],[0,25,50,75,100,125],[0,50,100,150]]
    for ax,rate,top,yticks in zip(axs,[10,20,30,40,50],ymax,ticks):
        for i,platform in enumerate(HISTORICAL_ORDER):
            row=next(r for r in data['goodput'] if r['platform']==platform and int(r['rate_rps_per_client'])==rate)
            full=ax.bar(i,float(row['throughput_rps_from_pdf_vector']),width=.76,color=colors[i],edgecolor='#222222',linewidth=.55)[0]
            full._source_platform=platform;full._metric='throughput'
            if str(row['pattern_patch_present'])=='True':
                good=ax.bar(i,float(row['goodput_rps_from_pdf_vector']),width=.76,
                            facecolor='white',edgecolor=colors[i],linewidth=.5,hatch='////')[0]
                good._source_platform=platform;good._metric='goodput'
        ax.axhline(3*rate,color='#ef7b32',ls='--',lw=.65)
        ax.set_title(f'RPS = {rate}',fontsize=7.5,fontweight='bold',pad=3)
        ax.set_xticks([]);ax.set_ylim(0,top);ax.set_yticks(yticks)
        ax.grid(axis='y',alpha=.35,lw=.4,linestyle='--');ax.tick_params(length=2,pad=1.5,width=.5)
    axs[0].set_ylabel('Tasks/s',labelpad=2)
    fig.legend(handles=[Patch(facecolor='white',edgecolor='#222222',label='Throughput'),
                        Patch(facecolor='white',edgecolor='#555555',hatch='////',label='Goodput')],
               loc='lower center',bbox_to_anchor=(.5,.21),ncol=2,frameon=False,fontsize=6.2)
    handles=[Patch(facecolor=c,edgecolor='#222222',linewidth=.5,label=p.replace('Ray-Actor-','Ray-')) for p,c in zip(HISTORICAL_ORDER,colors)]
    handles.append(Line2D([],[],color='#ef7b32',ls='--',lw=.65,label='Ideal = 3r'))
    fig.legend(handles=handles,loc='lower center',bbox_to_anchor=(.5,.005),ncol=6,frameon=False,
               fontsize=5.9,handletextpad=.4,columnspacing=1.1,labelspacing=.4)
    return fig
