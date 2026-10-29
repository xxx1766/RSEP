"""Synthetic fixtures only; no publication evidence or canonical assets are written."""
import ast
import copy
import csv
import io
import itertools
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import update_fig7_cluster_mixed_notebook as update
import fig7_cluster_mixed as plot


def csvbytes(rows):
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader(); writer.writerows(rows)
    return out.getvalue().encode()


def fixture():
    jobs = [dict(id=f'fixture-{i}', kind='cpu', level=level, system=system,
                 repeat=repeat, rate_rps={'low': 116, 'high': 309, 'overload': 463}[level])
            for i, (level, system, repeat) in enumerate(itertools.product(
                plot.LEVELS, plot.SYSTEMS, range(1, 6)))]
    manifest = dict(campaign='SYNTHETIC_FIXTURE', repetitions=5, jobs=jobs,
        capacity_rps={'cpu': 385.68182540520377},
        rates_rps={'cpu': {'low': 116, 'high': 309, 'overload': 463}},
        duration_s=10, load_factors=plot.FACTORS, deadline_classes_ms=[100, 500],
        slots={'nj1': 2, 'nj2': 1, 'nj3': 1},
        source_sha256={'fig7_cluster_mixed_audit.py': 'f' * 64})
    runs = [dict(job_id=j['id'], kind='cpu', level=j['level'], system=j['system'],
        repeat=j['repeat'], deadline_ms=deadline, function=f'cpu-qos{deadline}',
        load_factor=plot.FACTORS[j['level']], rate_rps=j['rate_rps'], duration_s=10,
        goodput_rps=j['repeat'], on_time_in_window=10*j['repeat'],
        offered_rps=j['rate_rps']/2, offered=5*j['rate_rps'])
        for j in jobs for deadline in (100, 500)]
    half = plot.T4 / math.sqrt(2)
    cells = [dict(kind='cpu', level=level, system=system, deadline_ms=deadline,
        function=f'cpu-qos{deadline}', load_factor=plot.FACTORS[level],
        rate_rps=manifest['rates_rps']['cpu'][level], n=5, goodput_rps_n=5,
        goodput_rps_mean=3, goodput_rps_sd=math.sqrt(2.5), goodput_rps_se=math.sqrt(.5),
        goodput_rps_ci95_halfwidth=half, goodput_rps_ci95_low=3-half,
        goodput_rps_ci95_high=3+half)
        for level, system, deadline in itertools.product(plot.LEVELS, plot.SYSTEMS, (100, 500))]
    blobs = {'manifest.json': json.dumps(manifest).encode(), 'runs.csv': csvbytes(runs),
             'cells.csv': csvbytes(cells), 'README.md': b'SYNTHETIC FIXTURE ONLY\n'}
    audit = dict(status='PASS', test_only=False, campaign=manifest['campaign'], treatments=60,
        origin_runs=120, class_run_rows=120, cells=24, repetitions=5, offered_total=177600,
        job_details={j['id']: {} for j in jobs}, source_sha256=manifest['source_sha256'],
        auditor_sha256='f'*64, manifest_sha256=plot.sha(blobs['manifest.json']),
        export_sha256={n: plot.sha(blobs[n]) for n in ('runs.csv', 'cells.csv', 'README.md')})
    blobs['audit.json'] = json.dumps(audit).encode()
    return blobs


class PublicationTests(unittest.TestCase):
    def test_dynamic_main_order_counts_captions_and_ignores_comments_and_mixed(self):
        self.assertTrue(callable(getattr(update, 'derive_paper_keys', None)),
                        'Updater must derive semantic figure numbers from expanded main TeX')
        with tempfile.TemporaryDirectory(prefix='figS7-order-test-') as tmp:
            root = Path(tmp)
            def fig(label):
                return '\\begin{figure}\\caption{caption}\\label{'+label+'}\\end{figure}\n'
            prefix = ''.join(fig('fig:prefix'+str(i)) for i in range(4))
            prefix += fig('fig:nj-image') + fig('fig:revision-fourway-workloads')
            prefix += fig('fig:revision-fourway-mixed') + fig('fig:resp_valid')
            paired = ('\\begin{figure*}\\caption{control}\\label{fig:control-pressure}'
                      '\\caption{lifecycle}\\label{fig:control-lifecycle}\\end{figure*}')
            (root / 'main.tex').write_text(prefix+'% \\input{missing}\n\\input{nested/order}')
            (root / 'nested').mkdir()
            previous = None
            for first in (False, True):
                calibration = fig('fig:objective-calibration')
                (root / 'nested/order.tex').write_text('\\input{leaf}')
                (root / 'nested/leaf.tex').write_text(
                    '% '+fig('fig:commented').strip()+'\n'
                    + (calibration+paired if first else paired+calibration))
                keys = update.derive_paper_keys(root / 'main.tex')
                expected = {'7': 'S7', '8': '7', '9': '9' if first else '8',
                            '10': '10' if first else '9', '11': '8' if first else '10',
                            '12': 'S5', '13': 'S6'}
                self.assertEqual(keys, expected)
                nb = json.loads((update.HERE.parent/'slimmed_experiment_figures.ipynb').read_bytes())
                cells, manifest, hashes = plot.validate_publication(fixture())
                helper = (update.HERE/'fig7_cluster_mixed.py').read_text()
                prepared, _ = update.prepare(nb, cells, manifest, hashes, helper, paper_keys=keys)
                again, changed = update.prepare(prepared, cells, manifest, hashes, helper, paper_keys=keys)
                self.assertEqual(changed, [])
                self.assertEqual(prepared, again)
                if previous is not None:
                    transitioned, _ = update.prepare(previous, cells, manifest, hashes, helper, paper_keys=keys)
                    self.assertEqual(transitioned, prepared)
                previous = prepared
                headings = [update.source(c).splitlines()[0] for c in prepared['cells']
                            if c['cell_type'] == 'markdown' and update.source(c).startswith('## Fig.')]
                self.assertEqual([h.split(' —')[0] for h in headings],
                    [f'## Fig. {k}' for k in ('5','6','7','8','9','10','S1','S2','S5','S6','S7')])
                # The final post-move manuscript must derive the same numbering.
                (root / 'main.tex').write_text(prefix.replace(fig('fig:revision-fourway-mixed'), '')
                                               +'\\input{nested/order}')
                self.assertEqual(update.derive_paper_keys(root/'main.tex'), expected)
                (root / 'main.tex').write_text(prefix+'% \\input{missing}\n\\input{nested/order}')

    def test_complete_fixture_validates(self):
        cells, manifest, hashes = plot.validate_publication(fixture())
        self.assertEqual(len(cells), 24)
        self.assertEqual(len(manifest['jobs']), 60)
        self.assertEqual(len(hashes), 5)

    def test_rejects_incomplete_test_only_and_hash_mismatch(self):
        for change in ('audit', 'test_only', 'cells_hash', 'manifest_hash', 'readme_hash'):
            with self.subTest(change=change):
                blobs = fixture(); audit = json.loads(blobs['audit.json'])
                if change == 'audit': audit['origin_runs'] = 119
                elif change == 'test_only': audit['test_only'] = True
                elif change == 'cells_hash': blobs['cells.csv'] += b'\n'
                elif change == 'manifest_hash': blobs['manifest.json'] += b'\n'
                else: blobs['README.md'] += b'changed'
                blobs['audit.json'] = json.dumps(audit).encode()
                with self.assertRaises(ValueError): plot.validate_publication(blobs)

    def test_rejects_missing_or_incorrect_total_offered_count(self):
        for value in (None, 1, 177599):
            blobs = fixture(); audit = json.loads(blobs['audit.json'])
            if value is None:
                audit.pop('offered_total', None)
            else:
                audit['offered_total'] = value
            blobs['audit.json'] = json.dumps(audit).encode()
            with self.assertRaises(ValueError):
                plot.validate_publication(blobs)

    def test_rejects_wrong_run_counts_ci_offered_rate_and_duplicate_cells(self):
        for change in ('missing_run', 'duplicate_repeat', 'count', 'offered', 'ci', 'duplicate_cell'):
            with self.subTest(change=change):
                blobs = fixture(); audit = json.loads(blobs['audit.json'])
                runs = list(csv.DictReader(io.StringIO(blobs['runs.csv'].decode())))
                cells = list(csv.DictReader(io.StringIO(blobs['cells.csv'].decode())))
                if change == 'missing_run': runs.pop()
                elif change == 'duplicate_repeat': runs[0]['repeat'] = '2'
                elif change == 'count': runs[0]['on_time_in_window'] = '1'
                elif change == 'offered': runs[0]['offered_rps'] = '1'
                elif change == 'ci': cells[0]['goodput_rps_ci95_low'] = '0'
                else: cells[-1] = dict(cells[0])
                blobs['runs.csv'] = csvbytes(runs); blobs['cells.csv'] = csvbytes(cells)
                audit['export_sha256'] = {n: plot.sha(blobs[n]) for n in audit['export_sha256']}
                blobs['audit.json'] = json.dumps(audit).encode()
                with self.assertRaises(ValueError): plot.validate_publication(blobs)

    def test_notebook_preservation_offline_execution_and_idempotence(self):
        # This migration test requires the legacy cell order. The canonical
        # notebook is already migrated and executed after publication.
        root = update.HERE.parent.parent
        pointer = root / 'code/Tide/results-fig7-cluster-mixed-20261016/publication-backup.json'
        path = root / json.loads(pointer.read_text())['path'] / 'slimmed_experiment_figures.ipynb'
        original = path.read_bytes(); nb = json.loads(original)
        cells, manifest, hashes = plot.validate_publication(fixture())
        helper = (update.HERE / 'fig7_cluster_mixed.py').read_text()
        updated, changed = update.prepare(nb, cells, manifest, hashes, helper)
        allowed = {0, 1, 7, *range(9, 23)}
        by_id = {c['id']: c for c in updated['cells']}
        original_ids = [c['id'] for c in nb['cells']]
        self.assertEqual(set(by_id), set(original_ids))
        self.assertEqual(by_id[original_ids[8]], nb['cells'][8])
        for i in set(range(len(nb['cells']))) - allowed:
            self.assertEqual(by_id[original_ids[i]], nb['cells'][i])
        keys = update.derive_paper_keys(update.HERE.parent/'main_slimmed.tex')
        for index, old in zip((12, 14, 16, 18, 20, 22), ('8', '9', '10', '11', '12', '13')):
            expected = update.source(nb['cells'][index]).replace("key = '"+old+"'",
                                        "key = '"+keys[old]+"'", 1)
            self.assertEqual(update.source(by_id[original_ids[index]]), expected)
            self.assertEqual(by_id[original_ids[index]]['outputs'], nb['cells'][index]['outputs'])
        def assignment(cell, name):
            return ast.literal_eval(update.dictionary(update.source(cell), name))
        old_files = assignment(nb['cells'][1], 'FIGURE_FILES')
        new_files = assignment(updated['cells'][1], 'FIGURE_FILES')
        self.assertEqual(new_files, {keys.get(k, k):
            plot.OUTPUT if k == '7' else v for k, v in old_files.items()})
        self.assertEqual(update.prepare(updated, cells, manifest, hashes, helper)[1], [])
        self.assertEqual(path.read_bytes(), original)
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np
        space = dict(json=json, np=np, plt=plt, DATA={}, PROVENANCE={},
            FIGURE_STYLE={'S7': {'figsize': [6.5, 2.7]}},
            PALETTES={'fourway': plot.COLORS}, PLOTTED_VALUES={},
            start_plot=lambda *a: None, finish_plot=lambda fig, *a, **kw: fig)
        try:
            exec(update.source(by_id[original_ids[10]]), space)
            fig = space['plot_fig_s7']()
            self.assertEqual(len(fig.axes), 2)
            self.assertEqual(sum(len(ax.patches) for ax in fig.axes), 24)
            # A negative lower interval must survive rendering without clipping.
            extreme = copy.deepcopy(cells)
            extreme[0]['goodput_rps_ci95_low'] = '-2'
            fig2 = plot.figure(extreme, manifest['rates_rps']['cpu'])
            self.assertLess(fig2.axes[0].get_ylim()[0], -2)
        finally: plt.close('all')

    def test_cli_check_write_copy_and_failure_does_not_write(self):
        original_path = update.HERE.parent / 'slimmed_experiment_figures.ipynb'
        original = original_path.read_bytes()
        with tempfile.TemporaryDirectory(prefix='figS7-synthetic-test-') as tmp:
            root = Path(tmp)
            for name, data in fixture().items(): (root / name).write_bytes(data)
            notebook = root / 'copy.ipynb'; notebook.write_bytes(original)
            command = [sys.executable, str(update.HERE / 'update_fig7_cluster_mixed_notebook.py'),
                       '--publication', tmp, '--manifest', str(root / 'manifest.json'),
                       '--notebook', str(notebook)]
            dry = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertFalse(json.loads(dry.stdout)['notebook_written'])
            self.assertEqual(notebook.read_bytes(), original)
            stale = subprocess.run(command + ['--check'], capture_output=True, text=True)
            self.assertNotEqual(stale.returncode, 0)
            write = subprocess.run(command + ['--write'], capture_output=True, text=True)
            self.assertEqual(write.returncode, 0, write.stderr)
            check = subprocess.run(command + ['--check'], capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stderr)
            previous = notebook.read_bytes()
            (root / 'cells.csv').write_bytes(b'incomplete\n')
            failed = subprocess.run(command + ['--write'], capture_output=True, text=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(notebook.read_bytes(), previous)
            self.assertEqual(original_path.read_bytes(), original)


if __name__ == '__main__': unittest.main()
