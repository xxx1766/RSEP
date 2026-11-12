"""WAN goodput and deadline yield."""
import argparse
import hashlib
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

CAPTION = (
    '3+3 nested-VM pilot with emulated 65-ms remote RTT and a 135-ms deadline. '
    'Tide is compared with the native Kubernetes default ClusterIP Service over '
    'the same six prestarted inference workers. Each bar represents one '
    '1,000-request run; no confidence interval is estimated. Goodput and deadline '
    'yield count correct returns within 135 ms of actual submission and within '
    'the common arrival window plus 135 ms. Tide/default-Service goodput ratios '
    'are 2.02 and 2.66 for gradual and burst arrivals, respectively. All successful '
    'Tide executions are local in these runs, so this comparison does not '
    'demonstrate remote-capacity augmentation or superiority over locality-aware '
    'Kubernetes policies.'
)


def load_comparison(root):
    root = Path(root)
    manifest = json.loads((root / 'ARTIFACT_SHA256.json').read_text())['files']
    verified = {}

    def read_verified(relative):
        body = (root / relative).read_bytes()
        meta = manifest[relative]
        digest = hashlib.sha256(body).hexdigest()
        assert len(body) == meta['bytes'] and digest == meta['sha256'], relative
        verified[relative] = digest
        return body

    summary = json.loads(read_verified('pilot_summary.json'))
    assert summary['status']['status'] == 'COMPLETE'
    valid = {r['name'] for r in summary['records'] if r['infrastructure_qualified']}
    cases = []
    for pattern in ['gradual', 'burst']:
        systems = {}
        prior_end = None
        gap = None
        trace_sha = None
        for arm in ['tide', 'k0']:
            name = f'original65-{pattern}-{arm}'
            assert name in valid
            prefix = f'runs/{name}/'
            audit = json.loads(read_verified(prefix + 'audit.json'))
            run = json.loads(read_verified(prefix + 'run.json'))
            requests = [json.loads(x) for x in read_verified(prefix + 'requests.jsonl').splitlines()]
            trace = read_verified(f'traces/{pattern}.jsonl')
            digest = hashlib.sha256(trace).hexdigest()
            assert digest == audit['trace_sha256'] == run['trace_sha256']
            if trace_sha is not None:
                assert trace_sha == digest
            trace_sha = digest
            assert len(requests) == len({r['id'] for r in requests}) == 1000
            start = run['start_unix_ns']
            end = start + int(audit['observation_window_ms'] * 1e6)
            timely = sum(r['status'] == 'success' and r['latency_ms'] <= 135
                         and r['reply_unix_ns'] <= end for r in requests)
            assert timely == audit['on_time']
            goodput = timely / (audit['observation_window_ms'] / 1000)
            assert math.isclose(goodput, audit['goodput_rps'], abs_tol=1e-12)
            if prior_end is not None:
                gap = (start - prior_end) / 1e9
                assert 0 <= gap < 300, 'selected Tide/default pair is not consecutive'
            prior_end = max(r['reply_unix_ns'] for r in requests)
            systems[arm] = dict(name=name, offered=1000, on_time=timely,
                                goodput_rps=goodput, yield_pct=timely / 10,
                                repeats=1, ci95_halfwidth=None,
                                exec_local=audit['exec_local'], exec_remote=audit['exec_remote'],
                                admission_rejections=audit['admission_rejections'])
        cases.append(dict(pattern=pattern, systems=systems, trace_sha256=trace_sha,
                          pair_idle_s=gap,
                          ratio_tide_over_default=systems['tide']['goodput_rps'] / systems['k0']['goodput_rps']))
    return dict(rtt_ms=65, deadline_ms=135, compute_vms='3 local + 3 remote',
                baseline='native Kubernetes default ClusterIP Service (k0)',
                selection='both original arrival patterns at 65 ms; fixed r1, no best-repeat selection',
                scope='single-repeat pilot; simulated WAN; main-text Figure 9',
                cases=cases, source_sha256=verified, caption=CAPTION)


def render_three_pair_wan(data, figsize=(3.5, 1.5), colors=None):
    """Legacy Fig. 9 layout: shared top legend, goodput left, yield right."""
    colors = colors or ['#F4B6B6', '#F7E6A2']
    assert data['rtt_ms'] == 65 and data['deadline_ms'] == 135
    assert [c['pattern'] for c in data['cases']] == ['gradual', 'burst']
    style = {'font.size': 7, 'axes.titlesize': 7, 'axes.labelsize': 6.5,
             'xtick.labelsize': 6.5, 'ytick.labelsize': 6.5,
             'font.family': 'DejaVu Sans', 'axes.linewidth': .5,
             'pdf.fonttype': 42, 'ps.fonttype': 42, 'axes.axisbelow': True}
    ledger = []
    with plt.rc_context(style):
        fig, axes = plt.subplots(1, 2, figsize=figsize)
        fig.subplots_adjust(left=.10, right=.99, bottom=.18, top=.71, wspace=.42)
        for ax, metric, title, unit, ylim, ticks in zip(
                axes, ['goodput_rps', 'yield_pct'], ['(a) Goodput', '(b) Deadline yield'],
                ['Requests/s', '%'], [4.15, 108], [[0, 2, 4], [0, 50, 100]]):
            for j, (arm, label, color) in enumerate(zip(
                    ['tide', 'k0'], ['RSEP', 'K8s default'], colors)):
                rows = [c['systems'][arm] for c in data['cases']]
                assert all(r['repeats'] == 1 and r['ci95_halfwidth'] is None for r in rows)
                values = [r[metric] for r in rows]
                bars = ax.bar(np.arange(2) + (j - .5) * .34, values,
                              width=.24, color=color, edgecolor='#444444', linewidth=.4, label=label)
                text = [f'{v:.3f}' if metric == 'goodput_rps' else f'{v:.1f}' for v in values]
                ax.bar_label(bars, labels=text, padding=2, fontsize=5.8)
                ledger.extend(dict(pattern=c['pattern'], arm=arm, metric=metric,
                                   value=v, repeats=1, ci95_halfwidth=None)
                              for c, v in zip(data['cases'], values))
            ax.set_title(title, pad=5)
            ax.set_xticks([0, 1], ['Gradual', 'Burst'])
            ax.set_ylim(0, ylim)
            ax.set_yticks(ticks)
            ax.set_ylabel(unit, labelpad=1)
            ax.grid(axis='y', alpha=.25, linewidth=.4)
            ax.spines[['top', 'right']].set_visible(False)
            ax.tick_params(length=2, width=.5, pad=2)
        fig.legend(*axes[0].get_legend_handles_labels(), loc='upper center', ncol=2,
                   frameon=False, fontsize=7, handlelength=1.1, columnspacing=1.5)
        fig._wan29_values = ledger
    return fig


def main():
    project = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path,
                        default=project / 'data/compact/current/wan/plot_data.json')
    parser.add_argument('--out', type=Path, default=project / 'figures/rendered/current/wan_3plus3_65ms_tide_k8s')
    args = parser.parse_args()
    data = json.loads(args.data.read_text())
    fig = render_three_pair_wan(data)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    for ext in ['pdf', 'png', 'svg']:
        fig.savefig(args.out.with_suffix('.' + ext), dpi=300)
    args.out.with_suffix('.data.json').write_text(json.dumps(data, indent=2) + '\n')
    args.out.with_suffix('.values.json').write_text(json.dumps(fig._wan29_values, indent=2) + '\n')
    args.out.with_suffix('.caption.md').write_text(data['caption'] + '\n')
    plt.close(fig)
    print(json.dumps(dict(pdf=str(args.out.with_suffix('.pdf')),
                          ratios={c['pattern']: c['ratio_tide_over_default'] for c in data['cases']})))


if __name__ == '__main__':
    main()
