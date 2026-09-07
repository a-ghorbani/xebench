import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ReferencePublicationTests(unittest.TestCase):
    def test_new_sessions_cannot_modify_canonical_data(self):
        canonical = ROOT / 'data/benchmarks.json'
        before = hashlib.sha256(canonical.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'record.jsonl'
            for status in ('running', 'failed', 'complete'):
                with self.subTest(status=status):
                    path.write_text(json.dumps({'schema': 'xebench-raw', 'schemaVersion': 1, 'status': status}))
                    result = subprocess.run(['node', str(ROOT / 'scripts/aggregate.mjs'), str(path), '--update'],
                                            capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(before, hashlib.sha256(canonical.read_bytes()).hexdigest())


if __name__ == '__main__':
    unittest.main()
