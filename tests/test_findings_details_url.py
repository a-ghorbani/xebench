"""findings.schema.json `detailsUrl`: optional, https only, and present on every checked-in finding."""
import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


def schema_error_counts(urls):
    """Schema errors for the first checked-in finding with each detailsUrl (None = field absent)."""
    script = (
        "import {validateSchema} from './scripts/lib/publication.mjs';"
        "import {readFileSync} from 'node:fs';"
        "const s = JSON.parse(readFileSync('./data/findings.schema.json', 'utf8'));"
        "const f = JSON.parse(readFileSync('./data/findings.json', 'utf8')).findings[0];"
        f"const urls = {json.dumps(urls)};"
        "console.log(JSON.stringify(urls.map((u) => {"
        "  const g = {...f}; delete g.detailsUrl; if (u !== null) g.detailsUrl = u;"
        "  return validateSchema(g, s.$defs.finding, s).length; })));"
    )
    result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True, cwd=ROOT)
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


class DetailsUrlTests(unittest.TestCase):
    def test_accepted_and_rejected_values(self):
        ok = [None, 'https://github.com/a-ghorbani/xebench/blob/0123abc/docs/x.md#section']
        bad = ['http://example.com/x', 'javascript:alert(1)', 'https://example.com', 'https://user@example.com/x',
               'https://example.com/a b', 42]
        counts = schema_error_counts(ok + bad)
        self.assertEqual(counts[:len(ok)], [0] * len(ok))
        for url, count in zip(bad, counts[len(ok):]):
            self.assertGreater(count, 0, f'{url!r} must be rejected')

    def test_every_finding_links_its_details(self):
        findings = json.loads((ROOT / 'data/findings.json').read_text(encoding='utf-8'))['findings']
        for finding in findings:
            with self.subTest(finding=finding['id']):
                url = finding.get('detailsUrl', '')
                self.assertRegex(url, r'^https://github\.com/a-ghorbani/xebench/blob/[0-9a-f]{40}/docs/.+#.+$',
                                 'detailsUrl must be a commit-pinned docs anchor')


if __name__ == '__main__':
    unittest.main()
