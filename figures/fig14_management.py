"""Process-role Fig.14; also embedded verbatim in the offline notebook."""
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator


def render_fig14_management(rows, figsize=(3.5, 1.55)):
    """Stack management and runtime means; error bars are paired-block totals."""
    fig, axs = plt.subplots(1, 2, figsize=figsize)
    fig.subplots_adjust(left=.12, right=.99, bottom=.28, top=.78, wspace=.62)
    systems = [('tide', 'Tide', '#F4B6B6'), ('kubeedge', 'KubeEdge', '#F7E6A2'),
               ('ray', 'Ray Serve', '#BFE3BE')]
    rates, width, records = [10, 20, 40, 80], .24, []
    for ax, metric, title, ylabel in zip(
            axs, ['cpu_pct', 'pss_mib'], ['(a) CPU', '(b) PSS'],
            ['CPU (% of one core)', 'PSS (MiB)']):
        for i, (system, label, color) in enumerate(systems):
            selected = []
            for rate in rates:
                match = [r for r in rows if r['system'] == system and int(r['rps']) == rate and int(r['ddl_ms']) == 200]
                assert len(match) == 1 and int(match[0]['n']) == 3
                selected.append(match[0])
            x = [j + (i - 1) * width for j in range(4)]
            management = [float(r['management_' + metric]) for r in selected]
            runtime = [float(r['runtime_' + metric]) for r in selected]
            totals = [float(r['management_runtime_' + metric]) for r in selected]
            errors = [float(r['management_runtime_' + metric + '_ci95_half']) for r in selected]
            for m, s, t in zip(management, runtime, totals):
                assert abs(m + s - t) < 1e-9
            ax.bar(x, management, width, color=color, edgecolor='#333333', linewidth=.3)
            ax.bar(x, runtime, width, bottom=management, color=color, hatch='////',
                   edgecolor='#333333', linewidth=.3)
            ax.errorbar(x, totals, yerr=[[min(m, e) for m, e in zip(totals, errors)], errors],
                        fmt='none', ecolor='#222222', elinewidth=.5, capsize=1, capthick=.5)
            for rate, m, s, t, e in zip(rates, management, runtime, totals, errors):
                records.append(dict(system=system, rps=rate, ddl_ms=200, metric=metric,
                                    management=m, runtime=s, mean=t, ci95_half=e))
        ax.set_ylim(bottom=0)
        ax.set_xlim(-.65, 3.65)
        ax.set_xticks(range(4), [str(rate) for rate in rates])
        ax.set_xlabel('Offered requests/s', labelpad=2)
        ax.set_title(title, pad=3)
        ax.set_ylabel(ylabel, labelpad=2)
        ax.grid(axis='y', alpha=.25, lw=.4)
        ax.tick_params(length=2, width=.5, pad=1)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=3))
        ax.spines[['top', 'right']].set_visible(False)
        assert all(r['mean'] + r['ci95_half'] <= ax.get_ylim()[1] for r in records if r['metric'] == metric)
    handles = [Patch(facecolor=c, edgecolor='#333333', linewidth=.3, label=l) for _, l, c in systems]
    handles += [Patch(facecolor='white', edgecolor='#333333', linewidth=.3, label='Management'),
                Patch(facecolor='white', edgecolor='#333333', linewidth=.3, hatch='////', label='Runtime support')]
    fig.legend(handles=handles, loc='upper center', bbox_to_anchor=(.52, 1.0),
               ncol=5, frameon=False, fontsize=5.5, borderaxespad=.2,
               handlelength=.95, columnspacing=.65, handletextpad=.28)
    return fig, records
