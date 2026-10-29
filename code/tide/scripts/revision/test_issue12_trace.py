import unittest

from issue12_trace import generate


class ConferenceShapeTraceTests(unittest.TestCase):
    def test_gradual_boundaries_and_offered_window(self):
        rows, window_ms = generate("gradual", 1, 1)
        offsets = [row["scheduled_offset_ns"] for row in rows]
        self.assertEqual((len(rows), window_ms), (1000, 270000))
        self.assertEqual(offsets[0], 0)
        self.assertEqual(offsets[300], 150000000000)
        self.assertEqual(offsets[600], 240000000000)
        self.assertEqual(offsets[900], 270000000000)
        self.assertEqual(offsets[-1], 270000000000)

    def test_burst_same_offsets_for_parallel_sources_and_unique_ids(self):
        rows, window_ms = generate("burst", 2, 3)
        self.assertEqual((len(rows), window_ms), (2000, 240000))
        self.assertEqual(len({row["id"] for row in rows}), 2000)
        self.assertEqual([row["scheduled_offset_ns"] for row in rows[:2]], [0, 0])
        self.assertEqual(rows[800]["scheduled_offset_ns"], 120000000000)
        self.assertEqual(rows[-1]["scheduled_offset_ns"], 239700000000)


if __name__ == "__main__":
    unittest.main()
