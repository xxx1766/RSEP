"""Exercise real process limits in isolated children, leaving the test runner intact."""
import json
from pathlib import Path
import subprocess
import sys
import unittest


class FileDescriptorCapacityTests(unittest.TestCase):
    module = 'fig6_cluster_driver'
    def child(self, code):
        return subprocess.run([sys.executable, "-c", code.replace("import fig6_cluster_driver as driver", "import " + self.module + " as driver")], cwd=Path(__file__).parent,
                              text=True, capture_output=True)

    def test_raises_low_soft_limit_and_preserves_hard_limit(self):
        result = self.child('''
import json, resource
import fig6_cluster_driver as driver
hard = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
resource.setrlimit(resource.RLIMIT_NOFILE, (1024, hard))
record = driver.ensure_nofile()
assert resource.getrlimit(resource.RLIMIT_NOFILE) == (65536, hard)
assert record == {'soft_before': 1024, 'soft': 65536, 'hard': hard, 'minimum': 65536}
print(json.dumps(record))
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_insufficient_hard_limit_fails_before_collecting(self):
        result = self.child('''
import resource
import fig6_cluster_driver as driver
resource.setrlimit(resource.RLIMIT_NOFILE, (1024, 4096))
try:
    driver.ensure_nofile()
except RuntimeError as error:
    assert 'RLIMIT_NOFILE' in str(error)
else:
    raise AssertionError('insufficient hard limit accepted')
assert resource.getrlimit(resource.RLIMIT_NOFILE) == (1024, 4096)
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_existing_larger_soft_limit_is_not_lowered(self):
        result = self.child('''
import resource
import fig6_cluster_driver as driver
hard = resource.getrlimit(resource.RLIMIT_NOFILE)[1]
resource.setrlimit(resource.RLIMIT_NOFILE, (131072, hard))
record = driver.ensure_nofile()
assert resource.getrlimit(resource.RLIMIT_NOFILE) == (131072, hard)
assert record['soft'] == 131072
''')
        self.assertEqual(result.returncode, 0, result.stderr)


class RayFileDescriptorCapacityTests(FileDescriptorCapacityTests):
    module = 'fig6_cluster_ray'


if __name__ == '__main__':
    unittest.main()
