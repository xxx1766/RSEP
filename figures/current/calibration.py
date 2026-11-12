import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

PROFILES = ('equal', 'resilience', 'latency')


def panel_values(data):
    grid = np.asarray(data['grid'], dtype=float)
    features = np.asarray([[r['GoodputNorm'], r['Resilience'], r['Util'],
                            -r['LatencyCost']] for r in data['calibration']])
    assert len(grid) == len(features) == 68
    assert all(np.allclose(grid[s['WeightID']], [0, .9, .1])
               for s in data['selection'])
    equal_id = next(i for i, w in enumerate(grid)
                    if np.allclose(w, [1 / 3, 1 / 3, 1 / 3]))
    panels = []
    for name in PROFILES:
        choice = next(s for s in data['selection'] if s['Name'] == name)
        lam = np.asarray(choice['Lambda'], dtype=float)
        lam = lam / lam.sum()
        panels.append({'name': name, 'lambda': lam.tolist(),
                       'selected_weight_id': choice['WeightID'],
                       'selected_weights': grid[choice['WeightID']].tolist(),
                       'equal_weight_id': equal_id,
                       'grid': grid.tolist(),
                       'objective': (features @ lam).tolist()})
    return panels


def render(data, figsize=(3.5, 1.55), cmap='viridis', choice_color='tab:red'):
    panels = panel_values(data)
    fig = plt.figure(figsize=figsize)
    axes = [fig.add_axes([i / 3 + .035, .35, .265, .45]) for i in range(3)]
    titles = ('Balanced objectives', 'Resilience', 'Latency')
    for index, (ax, panel, title) in enumerate(zip(axes, panels, titles)):
        grid = np.asarray(panel['grid'])
        x = grid[:, 1] + grid[:, 2] / 2
        y = grid[:, 2] * np.sqrt(3) / 2
        z = np.asarray(panel['objective'])
        sc = ax.scatter(x, y, c=z, cmap=cmap, s=3.6 * 1.5**2, marker='^',
                        linewidths=0, edgecolors='none',
                        vmin=float(z.min()), vmax=float(z.max()))
        ax.plot([0, 1, .5, 0], [0, 0, np.sqrt(3) / 2, 0], 'k-', lw=.4)
        wi, ei = panel['selected_weight_id'], panel['equal_weight_id']
        ax.scatter([x[wi]], [y[wi]], marker='*', s=20, c=choice_color,
                   edgecolors='k', linewidths=.3, label='Calibration choice', zorder=5)
        ax.scatter([x[ei]], [y[ei]], marker='o', s=10, facecolors='none',
                   edgecolors=choice_color, linewidths=.6,
                   label='Equal online weights', zorder=5)
        ax.annotate('$w_u$', (0, 0), xytext=(-1, -3), textcoords='offset points',
                    ha='right', va='top', fontsize=6.2, annotation_clip=False)
        ax.annotate('$w_l$', (1, 0), xytext=(1, -3), textcoords='offset points',
                    ha='left', va='top', fontsize=6.2, annotation_clip=False)
        ax.annotate('$w_d$', (.5, np.sqrt(3) / 2), xytext=(0, 3),
                    textcoords='offset points', ha='center', va='bottom',
                    fontsize=6.2, annotation_clip=False)
        lam_text = ','.join(f'{v:.2f}' for v in panel['lambda'])
        center = index / 3 + 1 / 6
        fig.text(center, .15, f'({chr(97 + index)}) {title}',
                 ha='center', va='top', fontsize=5.5)
        fig.text(center, .08, r'$\lambda=(' + lam_text + ')$',
                 ha='center', va='top', fontsize=5.1)
        ax.set(xlim=(-.035, 1.035), ylim=(-.02, .90), aspect='equal')
        ax.axis('off')
        cax = fig.add_axes([index / 3 + .055, .235, .22, .028])
        cb = fig.colorbar(sc, cax=cax, orientation='horizontal',
                          ticks=[z.min(), z.max()], format='%.2f')
        cb.ax.tick_params(labelsize=5.1, length=1.2, width=.4, pad=1)
        cb.outline.set_linewidth(.4)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='upper center', bbox_to_anchor=(.5, 1.0),
               ncol=2, frameon=False, fontsize=5.7, handletextpad=.25,
               columnspacing=.9, borderaxespad=0)
    return fig, panels


def draw(data, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 7,
                         'pdf.fonttype': 42, 'axes.linewidth': .5})
    fig, panels = render(data)
    stem = output / 'objective_weight_three_compact'
    for suffix in ('pdf', 'png', 'svg'):
        fig.savefig(stem.with_suffix('.' + suffix), dpi=180, facecolor='white')
    fields = ['profile', 'lambda_1', 'lambda_2', 'lambda_3', 'lambda_4',
              'weight_id', 'w_u', 'w_l', 'w_d', 'objective', 'selected']
    with (output / 'objective_weight_three_compact.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for panel in panels:
            for i, (weights, objective) in enumerate(zip(panel['grid'], panel['objective'])):
                writer.writerow(dict(zip(fields, [panel['name'], *panel['lambda'], i,
                                                 *weights, objective,
                                                 i == panel['selected_weight_id']])))
    (output / 'objective_weight_three_compact.json').write_text(json.dumps({
        'source_sha256': data['source_sha256'],
        'profiles': panels, 'layout': '1 row x 3 columns',
        'figsize_inches': [3.5, 1.55],
        'color_scales': 'independent per preference'}, indent=2) + '\n')
    plt.close(fig)
