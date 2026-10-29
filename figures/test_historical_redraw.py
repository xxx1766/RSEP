import copy
import csv
import io
from pathlib import Path
import unittest
import numpy as np
from historical_redraw import validate_historical, render_placement, render_allocation, render_goodput


class HistoricalRedrawTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).parent/'historical_sources'
        cls.data = {key: list(csv.DictReader(io.StringIO((root/name).read_text()))) for key, name in {
            'placement': 'zheng-table_5_9_placement.csv',
            'allocation': 'allocation_trace_vector_vertices.csv',
            'goodput': 'tmc_goodput_vector_values.csv'}.items()}

    def test_incomplete_source_is_rejected(self):
        data = copy.deepcopy(self.data); data['goodput'].pop()
        with self.assertRaises(ValueError): validate_historical(data)

    def test_nonmonotone_trace_is_rejected(self):
        data = copy.deepcopy(self.data); data['allocation'][1]['time_s_from_pdf_vector'] = '-1'
        with self.assertRaises(ValueError): validate_historical(data)

    def test_all_200_table_values_are_drawn(self):
        fig = render_placement(self.data)
        for ax, metric in zip(fig.axes, ['edge_percent', 'cloud_percent']):
            self.assertEqual(len(ax.lines), 10)
            for line in ax.lines:
                rows = sorted([r for r in self.data['placement'] if r['platform'] == line._source_platform], key=lambda r: int(r['rate_rps_per_client']))
                np.testing.assert_array_equal(line.get_ydata(), [float(r[metric]) for r in rows])

    def test_all_77_vector_vertices_are_drawn_without_steps_or_smoothing(self):
        fig = render_allocation(self.data)
        self.assertEqual(sum(len(line.get_xdata()) for line in fig.axes[0].lines), 77)
        for line in fig.axes[0].lines:
            rows = [r for r in self.data['allocation'] if r['platform'] == line.get_label()]
            np.testing.assert_array_equal(line.get_xdata(), [float(r['time_s_from_pdf_vector']) for r in rows])
            np.testing.assert_array_equal(line.get_ydata(), [float(r['allocated_cpus_from_pdf_vector']) for r in rows])
            self.assertEqual(line.get_drawstyle(), 'default')

    def test_all_50_bar_pairs_and_pattern_presence_are_preserved(self):
        fig = render_goodput(self.data)
        self.assertEqual(len(fig.axes), 5)
        for ax, rate in zip(fig.axes, [10,20,30,40,50]):
            rows = [r for r in self.data['goodput'] if int(r['rate_rps_per_client']) == rate]
            full = {p._source_platform:p for p in ax.patches if p._metric == 'throughput'}
            timely = {p._source_platform:p for p in ax.patches if p._metric == 'goodput'}
            self.assertEqual(len(full), 10)
            for row in rows:
                self.assertEqual(full[row['platform']].get_height(), float(row['throughput_rps_from_pdf_vector']))
                if row['pattern_patch_present'] == 'True':
                    self.assertEqual(timely[row['platform']].get_height(), float(row['goodput_rps_from_pdf_vector']))
                else:self.assertNotIn(row['platform'], timely)
        self.assertEqual(len({round(ax.get_position().y0, 8) for ax in fig.axes}), 1)


if __name__ == '__main__': unittest.main()
