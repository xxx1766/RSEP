"""Synthetic publication fixtures; never modify the real notebook or PDFs.

Run with a Python environment containing matplotlib/numpy:
  python -m unittest discover -s TMC_slimmed/plotting -p test_update_fig6_cluster_notebook.py -v
"""
import ast
import copy
import csv
import io
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import update_fig6_cluster_notebook as m


def csvbytes(rows):
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode()


def assignment(text, name):
    return ast.literal_eval(next(n.value for n in ast.parse(text).body
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)))


class NotebookUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.notebook = m.HERE.parent / 'slimmed_experiment_figures.ipynb'
        cls.original = cls.notebook.read_bytes()
        cls.nb = json.loads(cls.original)
        jobs = [dict(zip(m.FIELDS, cell), id=f'fixture-{i:03d}-{repeat}', repeat=repeat)
                for i, cell in enumerate(sorted(m.EXPECTED)) for repeat in range(1, 6)]
        cls.manifest = dict(campaign='independent-fixture', jobs=jobs, repetitions=5,
            rates_rps={k: {'low': 10, 'high': 20} for k in ('cpu', 'memory', 'io')},
            duration_s=10, deadline_ms=500)
        cls.runs = [dict(zip(m.FIELDS, m.key(j)), job_id=j['id'], repeat=j['repeat'],
            rate_rps=cls.manifest['rates_rps'][j['kind']][j['level']], duration_s=10,
            goodput_rps=j['repeat'], on_time_in_window=10 * j['repeat']) for j in jobs]
        # Hand-known observations [1,2,3,4,5]: mean3, variance2.5, SE=sqrt(.5).
        ci = m.T4 / math.sqrt(2)
        cls.cells = [dict(zip(m.FIELDS, k), metric='goodput_rps', n=5, mean=3.,
            sd=math.sqrt(2.5), se=math.sqrt(.5), ci95_halfwidth=ci,
            ci95_low=3-ci, ci95_high=3+ci) for k in sorted(m.EXPECTED)]
        cls.blobs = {'manifest.json': json.dumps(cls.manifest).encode(),
                     'runs.csv': csvbytes(cls.runs), 'cells.csv': csvbytes(cls.cells)}
        cls.audit = dict(status='PASS', treatments=360, origin_runs=720, cells=72,
            repetitions=5, campaign=cls.manifest['campaign'],
            manifest_sha256=m.sha(cls.blobs['manifest.json']),
            export_sha256={n: m.sha(cls.blobs[n]) for n in ('runs.csv', 'cells.csv')},
            job_details={j['id']: {} for j in jobs})
        cls.blobs['audit.json'] = json.dumps(cls.audit).encode()
        cls.selected, cls.man, cls.hashes = m.validate_publication(cls.blobs)
        cls.helper = (m.HERE / 'fig6_cluster.py').read_text()

    def tearDown(self):
        self.assertEqual(self.notebook.read_bytes(), self.original)

    def prepared(self):
        return m.prepare(self.nb, self.selected, self.man, self.hashes, self.helper)

    def test_preserves_other_cells_paths_archives_and_is_idempotent(self):
        updated, changed = self.prepared()
        # A subsequent campaign may already have the desired intro/style/heading.
        self.assertIn(8, changed)
        self.assertTrue(set(changed) <= {0, 1, 7, 8})
        for i, cell in enumerate(self.nb['cells']):
            if i not in changed:
                self.assertEqual(updated['cells'][i], cell)
        before = assignment(m.source(self.nb['cells'][1]), 'FIGURE_FILES')
        after = assignment(m.source(updated['cells'][1]), 'FIGURE_FILES')
        self.assertEqual(after, {**before, '6': m.FIG6_OUTPUT})
        self.assertIn(m.FIG6_OUTPUT, m.source(updated['cells'][7]))
        self.assertNotIn('figD1_heterogeneous_compact.pdf', m.source(updated['cells'][7]))
        self.assertIn('正文 **Fig. 5–11**', m.source(updated['cells'][0]))
        self.assertIn('分别对应当前附件图S5/S6', m.source(updated['cells'][0]))
        self.assertEqual(updated['cells'][8]['outputs'], [])
        self.assertIsNone(updated['cells'][8]['execution_count'])
        self.assertEqual(m.prepare(updated, self.selected, self.man, self.hashes, self.helper)[1], [])

    def test_rejects_seven_corrupt_or_incomplete_fixtures(self):
        for kind in ('hash', 'missing_run', 'duplicate_repeat', 'n4', 'ci', 'count', 'audit_incomplete'):
            with self.subTest(kind=kind):
                blobs, audit = copy.deepcopy(self.blobs), copy.deepcopy(self.audit)
                runs, cells = copy.deepcopy(self.runs), copy.deepcopy(self.cells)
                if kind == 'hash':
                    blobs['cells.csv'] += b'\n'
                elif kind == 'missing_run':
                    blobs['runs.csv'] = csvbytes(runs[:-1])
                elif kind == 'duplicate_repeat':
                    runs[0]['repeat'] = 2
                    blobs['runs.csv'] = csvbytes(runs)
                elif kind == 'n4':
                    cells[0]['n'] = 4
                    blobs['cells.csv'] = csvbytes(cells)
                elif kind == 'ci':
                    cells[0]['ci95_high'] += .1
                    blobs['cells.csv'] = csvbytes(cells)
                elif kind == 'count':
                    runs[0]['on_time_in_window'] += 1
                    blobs['runs.csv'] = csvbytes(runs)
                else:
                    audit['status'] = 'INCOMPLETE'
                if kind != 'hash':
                    audit['export_sha256'] = {n: m.sha(blobs[n]) for n in ('runs.csv', 'cells.csv')}
                blobs['audit.json'] = json.dumps(audit).encode()
                with self.assertRaises(ValueError):
                    m.validate_publication(blobs)

    def test_embedded_plot_renders_six_axes_and_72_bars_in_memory(self):
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np
        updated, _ = self.prepared()
        space = dict(json=json, np=np, plt=plt, DATA={}, PROVENANCE={},
            FIGURE_STYLE={'6': {'figsize': [3.5, 4.0]}},
            PALETTES={'fourway': ['blue', 'orange', 'green', 'red']}, PLOTTED_VALUES={},
            start_plot=lambda *a: None, finish_plot=lambda fig, *a, **kw: fig)
        try:
            exec(m.source(updated['cells'][8]), space)
            fig = space['plot_fig_6']()
            self.assertEqual(len(fig.axes), 6)
            self.assertEqual(sum(len(ax.patches) for ax in fig.axes), 72)
        finally:
            plt.close('all')

    def test_cli_dry_run_and_stale_check_without_writing(self):
        with tempfile.TemporaryDirectory(prefix='fig6-notebook-fixture-') as tmp:
            directory = Path(tmp)
            for name, data in self.blobs.items():
                (directory / name).write_bytes(data)
            command = [sys.executable, str(m.HERE / 'update_fig6_cluster_notebook.py'),
                '--publication', tmp, '--manifest', str(directory / 'manifest.json')]
            result = subprocess.run(command + ['--dry-run'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads(result.stdout)['notebook_written'])
            result = subprocess.run(command + ['--check'], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
