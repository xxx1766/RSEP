import unittest
from unittest.mock import patch

import issue12_nj_matrix as matrix


class MatrixRecoveryTests(unittest.TestCase):
    def test_transfer_direction_must_be_explicit(self):
        with patch.object(matrix, "command", return_value=""), self.assertRaises(TypeError):
            matrix.copy("NJ-k1", "/tmp/remote", "/local")

    def test_pull_reads_remote_source(self):
        with patch.object(matrix, "command", return_value="") as call:
            matrix.copy("NJ-k1", "/tmp/remote", "/local", pull=True)
        self.assertEqual(call.call_args.args[0][-2:], ["NJ-k1:/tmp/remote", "/local"])

    def test_finished_success_is_collected_without_launch(self):
        self.assertTrue(hasattr(matrix, "recovery_action"), "completion-aware resume decision missing")
        self.assertEqual(matrix.recovery_action(dict(phase="finished", exit_code=0)), "collect")
        self.assertEqual(matrix.recovery_action(dict(phase="running")), "observe")
        self.assertEqual(matrix.recovery_action(dict(phase="starting")), "observe")
        self.assertEqual(matrix.recovery_action(dict(phase="absent")), "launch")
        for state in (dict(phase="finished", exit_code=1),dict(phase="missing_process")):
            with self.assertRaises(RuntimeError):
                matrix.recovery_action(state)


if __name__ == "__main__":
    unittest.main()
