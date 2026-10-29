#!/usr/bin/env python3
"""Export control-plane pressure/deadline plots and a typeset summary table."""
import argparse
import csv
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

p=argparse.ArgumentParser(description=__doc__); p.add_argument('--summary',type=Path,required=True); p.add_argument('--out',type=Path,required=True); a=p.parse_args()
with a.summary.open() as f: rows=list(csv.DictReader(f))
a.out.mkdir(parents=True,exist_ok=True)
counts=sorted({int(r['n']) for r in rows})
block_note=('3 blocks' if counts==[3] else 'PARTIAL: n='+','.join(map(str,counts))+' blocks')
colors={'tide':'#1865AC','ray':'#C85436','kubeedge':'#2B8664'}
labels={'tide':'Tide','ray':'Ray Serve','kubeedge':'KubeEdge'}
fig,axes=plt.subplots(2,3,figsize=(12,6.7),sharex=True)
for col,ddl in enumerate((50,200,1000)):
    for system in colors:
        selected=sorted([r for r in rows if r['system']==system and int(r['ddl_ms'])==ddl],key=lambda r:int(r['rps']))
        for row,metric,label in [(0,'framework_cpu_pct','Framework CPU (% of one core)'),(1,'framework_pss_mib','Framework PSS (MiB)')]:
            ax=axes[row,col]
            ax.errorbar([int(r['rps']) for r in selected],[float(r[metric]) for r in selected],
                        yerr=[[min(float(r[metric]),float(r[metric+'_ci95_half'] or 0)) for r in selected], [float(r[metric+'_ci95_half'] or 0) for r in selected]],
                        marker='o',capsize=3,color=colors[system],label=labels[system])
            ax.set_xscale('log',base=2); ax.set_xticks([5,20,80],labels=['5','20','80']); ax.grid(alpha=.2)
            if col==0: ax.set_ylabel(label)
            if row==1: ax.set_xlabel('Offered requests/s')
            if row==0: ax.set_title(f'DDL = {ddl} ms')
axes[0,0].legend(fontsize=9)
fig.suptitle('Steady-state framework overhead on NJ-k1; application kernels excluded ('+block_note+')')
fig.text(.5,.015,'95% t interval where n=3; lower ends clipped at zero. Baseline DDL is an evaluation threshold; Tide uses DDL admission.',ha='center',fontsize=8)
fig.tight_layout(rect=(0,.04,1,.95))
for ext in ('pdf','png'): fig.savefig(a.out/f'control_pressure_deadline.{ext}',dpi=200)
plt.close(fig)
# Separate dependency accounting plot: no implication of equivalent system features.
idle=[r for r in rows if int(r['rps'])==0]
fig,axes=plt.subplots(1,2,figsize=(8,3.7))
for ax,metric,label in zip(axes,('stack_cpu_pct','stack_pss_mib'),('CPU (% of one core)','PSS (MiB)')):
    ax.bar([labels[r['system']] for r in idle],[float(r[metric]) for r in idle],
           yerr=[float(r[metric+'_ci95_half'] or 0) for r in idle],capsize=4,color=[colors[r['system']] for r in idle])
    ax.set_ylabel(label); ax.grid(axis='y',alpha=.2)
fig.suptitle('Idle measured stack excluding application kernels ('+block_note+')')
fig.text(.5,.015,'Measured dependencies only; common host services and outer kind shims excluded.',ha='center',fontsize=8)
fig.tight_layout(rect=(0,.06,1,.93))
for ext in ('pdf','png'): fig.savefig(a.out/f'control_idle_stack.{ext}',dpi=200)
plt.close(fig)
headers=['System','req/s','DDL ms','Framework\nCPU (%)','Framework\nPSS (MiB)','Measured stack\nCPU (%)','Measured stack\nPSS (MiB)']
table=[]
for r in rows:
    table.append([labels[r['system']],r['rps'],r['ddl_ms']]+[f"{float(r[k]):.2f} ± {float(r[k+'_sd']):.2f}" for k in ('framework_cpu_pct','framework_pss_mib','stack_cpu_pct','stack_pss_mib')])
fig,ax=plt.subplots(figsize=(12,.32*len(table)+1.1)); ax.axis('off')
t=ax.table(cellText=table,colLabels=headers,loc='center',cellLoc='center'); t.auto_set_font_size(False); t.set_fontsize(9); t.scale(1,1.45)
for (r,c),cell in t.get_celld().items():
    cell.set_edgecolor('#cccccc'); cell.set_linewidth(.35)
    if r==0: cell.set_facecolor('#dce8f2'); cell.set_text_props(weight='bold'); cell.set_height(cell.get_height()*1.5)
    elif r%2==0: cell.set_facecolor('#f4f6f8')
ax.set_title('Control-plane overhead: mean ± SD ('+block_note+')',pad=16)
fig.text(.5,.02,'CPU: 100% = one logical core. Application kernels excluded. PSS avoids counting shared pages repeatedly.\nFramework: Tide scheduler + adapter; Ray core + Serve wrappers; KubeEdge EdgeCore + CloudCore.\nStack adds measured dedicated support; common host services and outer kind shims excluded.',ha='center',fontsize=8)
fig.tight_layout(rect=(0,.09,1,1))
for ext in ('pdf','png'): fig.savefig(a.out/f'control_table.{ext}',dpi=200,bbox_inches='tight')
tex=[r'\begin{tabular}{lrrrrrr}',r'\hline',r'System & req/s & DDL (ms) & CPU (\%) & PSS (MiB) & Stack CPU (\%) & Stack PSS (MiB) \\',r'\hline']
for r in table: tex.append(' & '.join(x.replace('±',r'$\pm$') for x in r)+r' \\')
tex.extend([r'\hline',r'\end{tabular}'])
(a.out/'control_table.tex').write_text('\n'.join(tex)+'\n')
print(a.out)

# Component table makes process-accounting boundaries directly reviewable.
component_path=a.summary.with_name('component_summary.csv')
if component_path.exists():
    with component_path.open() as f: components=list(csv.DictReader(f))
    selected=[r for r in components if int(r['rps'])==0 and r['group'] not in ('framework','stack_ex_application')]
    group_labels={'core':'Core / launcher / agents','request_path':'Serve proxy + replica wrapper',
        'edgecore':'EdgeCore','cloudcore':'CloudCore','dedicated_cluster_support':'Dedicated kind cluster + runtime',
        'application':'Matched application kernel','runtime_support':'Per-container runtime shim',
        'shared_host_runtime':'Shared host containerd'}
    body=[]
    for r in selected:
        framework=r['group'] in ('core','request_path','edgecore','cloudcore')
        stack=r['group'] not in ('application','shared_host_runtime')
        group_label='Tide scheduler + HTTP adapter' if r['system']=='tide' and r['group']=='core' else group_labels[r['group']]
        body.append([labels[r['system']],group_label]+
                    [f"{float(r[k]):.2f} ± {float(r[k+'_sd']):.2f}" for k in ('cpu_pct','rss_mib','pss_mib')]+
                    ['Yes' if framework else 'No','Yes' if stack else 'No'])
    fig,ax=plt.subplots(figsize=(13,.36*len(body)+1.5)); ax.axis('off')
    t=ax.table(cellText=body,colLabels=['System','Process group','CPU %','RSS MiB','PSS MiB','Framework','Stack'],
               colWidths=[.1,.29,.14,.14,.14,.09,.09],loc='center',cellLoc='center')
    t.auto_set_font_size(False); t.set_fontsize(8.5); t.scale(1,1.55)
    for (row,col),cell in t.get_celld().items():
        cell.set_edgecolor('#cccccc'); cell.set_linewidth(.35)
        if row==0: cell.set_facecolor('#dce8f2'); cell.set_text_props(weight='bold')
        elif row%2==0: cell.set_facecolor('#f4f6f8')
    ax.set_title('Idle process accounting: mean ± SD ('+block_note+')',pad=12)
    fig.text(.5,.025,'CPU: 100% = one logical core. RSS may count shared pages multiple times; PSS is the primary summed-memory measure.\nKubeEdge controllers persist across blocks; Tide/Ray restart. These are distinct serving architectures, not feature-equivalent schedulers.',ha='center',fontsize=8)
    fig.tight_layout(rect=(0,.08,1,1))
    for ext in ('pdf','png'): fig.savefig(a.out/f'control_components.{ext}',dpi=200,bbox_inches='tight')
    component_tex=[r'\begin{tabular}{llrrrrr}',r'\hline','System & Process group & CPU (\\%) & RSS & PSS & Framework & Stack '+r'\\',r'\hline']
    component_tex += [' & '.join(x.replace('±',r'$\pm$') for x in r)+r' \\' for r in body]
    component_tex += [r'\hline',r'\end{tabular}']
    (a.out/'control_components.tex').write_text('\n'.join(component_tex)+'\n')

# Independent y scales reveal within-system sensitivity hidden by large scale gaps.
systems=[s for s in ('tide','kubeedge','ray') if any(r['system']==s and int(r['rps'])>0 for r in rows)]
fig,axes=plt.subplots(2,len(systems),figsize=(4*len(systems),6.4),squeeze=False)
ddl_colors={50:'#AA4057',200:'#D19624',1000:'#2B6B9D'}
for col,system in enumerate(systems):
    for ddl,color in ddl_colors.items():
        selected=sorted([r for r in rows if r['system']==system and int(r['ddl_ms'])==ddl],key=lambda r:int(r['rps']))
        for row,metric,label in [(0,'framework_cpu_pct','Framework CPU (% of one core)'),(1,'framework_pss_mib','Framework PSS (MiB)')]:
            ax=axes[row,col]
            ax.errorbar([int(r['rps']) for r in selected],[float(r[metric]) for r in selected],
                        yerr=[[min(float(r[metric]),float(r[metric+'_ci95_half'] or 0)) for r in selected], [float(r[metric+'_ci95_half'] or 0) for r in selected]],
                        marker='o',capsize=3,color=color,label=f'{ddl} ms')
            ax.set_xscale('log',base=2); ax.set_xticks([5,20,80],labels=['5','20','80']); ax.grid(alpha=.2)
            ax.set_ylabel(label)
            if row==1: ax.set_xlabel('Offered requests/s')
            if row==0: ax.set_title(labels[system]); ax.legend(title='DDL',fontsize=8,title_fontsize=8)
fig.suptitle('Within-system pressure and DDL sensitivity ('+block_note+')')
fig.text(.5,.015,'Each column has its own y scale. 95% t intervals, lower ends clipped at zero; baseline DDL is only an evaluation threshold.',ha='center',fontsize=8)
fig.tight_layout(rect=(0,.05,1,.95))
for ext in ('pdf','png'): fig.savefig(a.out/f'control_sensitivity.{ext}',dpi=200)
plt.close(fig)
