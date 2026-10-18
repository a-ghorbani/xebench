"""The 2026-10-17 llama.cpp Hexagon row correction (scripts/correct_hexagon_row.mjs).

Pins the exact change: one row (POCO F8 Ultra, llama.cpp NPU, Llama 3.2 1B Q4_0)
goes from validation "legacy" to "failed" with a note appended to asStated; its
numbers and every other row stay byte-identical. Tests that write run against a
temporary copy of scripts/ + data/, so the checked-in file is never modified.
"""
import copy
import difflib
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ISO = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')

IDENTITY = {'engine': 'llama.cpp', 'backend': 'npu', 'platform': 'android', 'device': 'POCO F8 Ultra',
            'soc': 'SM8850', 'model': 'Llama 3.2 1B', 'quant': 'Q4_0', 'provenance': 'measured'}
PUBLISHED = {'ppTps': 197.6, 'tgTps': 38.6, 'ttftMs': 2396, 'sampleCount': 3}
ORIGINAL_AS_STATED = 'n=3 cold-run median; prompt std-pp512-v1; decode 128 tok'
NOTE = ('Marked failed 2026-10-17. With llama.rn 0.12.4 (locked in our harness when this row was measured), '
        'llama.cpp Hexagon output on this phone is wrong for every tested prompt longer than ~128 tokens; this '
        'row used a ~512-token prompt and has no output check, so its speed does not represent correct '
        'inference. With llama.rn 0.13.0-rc.7 the Llama 3.2 1B output is correct (Q4_K_M passes; Q4_0 passes '
        'the same probes as its CPU reference, gate unverified). Successor: a separate Q4_K_M row.')
CORRECTED_AS_STATED = f'{ORIGINAL_AS_STATED}; {NOTE}'


def is_target(row):
    return all(row.get(k) == v for k, v in IDENTITY.items())


def revert_hexagon_correction(envelope):
    """The envelope as it was before this correction (the target row back to legacy)."""
    old = copy.deepcopy(envelope)
    for row in old['rows']:
        if is_target(row) and row.get('validation') == 'failed':
            assert row['asStated'] == CORRECTED_AS_STATED
            row['asStated'] = ORIGINAL_AS_STATED
            row['validation'] = 'legacy'
    return old


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def dump(envelope):
    return json.dumps(envelope, indent=2, ensure_ascii=False) + '\n'


class CheckedInDataTests(unittest.TestCase):
    def test_checked_in_row_is_corrected(self):
        data = load(ROOT / 'data/benchmarks.json')
        targets = [row for row in data['rows'] if is_target(row)]
        self.assertEqual(len(targets), 1)
        row = targets[0]
        self.assertEqual({k: row[k] for k in PUBLISHED}, PUBLISHED, 'numbers must stay as published')
        self.assertEqual(row['validation'], 'failed')
        self.assertEqual(row['asStated'], CORRECTED_AS_STATED)
        self.assertNotIn('guardsPassed', row)
        self.assertEqual(list(row)[-2:], ['asStated', 'validation'], 'key order must be unchanged')
        result = subprocess.run(['node', str(ROOT / 'scripts/correct_hexagon_row.mjs'), '--check'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_it_is_the_only_non_legacy_measured_row(self):
        data = load(ROOT / 'data/benchmarks.json')
        others = [r for r in data['rows'] if r['provenance'] == 'measured' and not is_target(r)]
        self.assertTrue(others)
        self.assertEqual({r['validation'] for r in others}, {'legacy'})


class CorrectionScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copytree(ROOT / 'scripts', self.root / 'scripts')
        shutil.copytree(ROOT / 'data', self.root / 'data')
        self.canonical = self.root / 'data/benchmarks.json'
        self.current = load(ROOT / 'data/benchmarks.json')
        self.before_text = dump(revert_hexagon_correction(self.current))
        self.canonical.write_text(self.before_text, encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, *args):
        return subprocess.run(['node', str(self.root / 'scripts/correct_hexagon_row.mjs'), *args],
                              capture_output=True, text=True)

    def digest(self):
        return hashlib.sha256(self.canonical.read_bytes()).hexdigest()

    def test_check_reports_pending_without_writing(self):
        result = self.run_script('--check')
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('needs the 2026-10-17', result.stderr)
        self.assertEqual(self.canonical.read_text(encoding='utf-8'), self.before_text)

    def test_exactly_one_row_changes(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('1 row changed, 40 unchanged', result.stderr)
        after_text = self.canonical.read_text(encoding='utf-8')
        before, after = json.loads(self.before_text), json.loads(after_text)
        self.assertRegex(after['generatedAt'], ISO)
        self.assertEqual({k: v for k, v in after.items() if k not in ('generatedAt', 'rows')},
                         {k: v for k, v in before.items() if k not in ('generatedAt', 'rows')})
        self.assertEqual(len(after['rows']), 41)
        changed = 0
        for old, new in zip(before['rows'], after['rows'], strict=True):
            if is_target(old):
                changed += 1
                self.assertEqual(json.dumps({**old, 'asStated': CORRECTED_AS_STATED, 'validation': 'failed'}),
                                 json.dumps(new))
            else:
                self.assertEqual(json.dumps(old), json.dumps(new))
        self.assertEqual(changed, 1)
        # The result is exactly the checked-in rows.
        self.assertEqual(json.dumps(after['rows']), json.dumps(self.current['rows']))
        # Textually: generatedAt plus the row's asStated and validation lines, nothing else.
        removed, added = [], []
        for line in difflib.unified_diff(self.before_text.splitlines(), after_text.splitlines(), lineterm='', n=0):
            if not line.startswith(('---', '+++', '@@')):
                (removed if line.startswith('-') else added).append(line[1:])
        self.assertEqual([l.split(':')[0].strip() for l in removed], ['"generatedAt"', '"asStated"', '"validation"'])
        self.assertEqual(added[1:], [f'      "asStated": {json.dumps(CORRECTED_AS_STATED)},',
                                     '      "validation": "failed"'])
        validate = subprocess.run(['node', str(self.root / 'scripts/validate_data.mjs')], capture_output=True,
                                  text=True)
        self.assertEqual(validate.returncode, 0, validate.stderr)

    def test_idempotent(self):
        self.assertEqual(self.run_script().returncode, 0)
        inode, before = self.canonical.stat().st_ino, self.digest()
        for args in ((), ('--check',)):
            with self.subTest(args=args):
                again = self.run_script(*args)
                self.assertEqual(again.returncode, 0, again.stderr)
                self.assertEqual(self.digest(), before)
                self.assertEqual(self.canonical.stat().st_ino, inode, 'an idempotent run must not rewrite')
        self.assertIn('nothing to do', self.run_script().stderr)

    def test_fails_closed(self):
        base = json.loads(self.before_text)
        index = next(i for i, r in enumerate(base['rows']) if is_target(r))

        def with_row(**fields):
            env = copy.deepcopy(base)
            env['rows'][index].update(fields)
            return dump(env)

        missing = copy.deepcopy(base)
        del missing['rows'][index]
        missing['rowCount'] -= 1
        cases = {
            'changed-number': (with_row(ppTps=200.0), 'values differ'),
            'missing-row': (dump(missing), 'found 0'),
            'unexpected-state': (with_row(validation='unverified'), 'unexpected state'),
            'edited-note': (with_row(asStated=ORIGINAL_AS_STATED + '; other'), 'unexpected state'),
            'invalid-input': (with_row(eligibility='x'), 'eligibility'),
        }
        for name, (text, reason) in cases.items():
            with self.subTest(case=name):
                self.canonical.write_text(text, encoding='utf-8')
                before = self.digest()
                result = self.run_script()
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(reason, result.stderr)
                self.assertIn('not modified', result.stderr)
                self.assertEqual(self.digest(), before)
        self.assertEqual(sorted(p.name for p in self.canonical.parent.iterdir()),
                         sorted(p.name for p in (ROOT / 'data').iterdir()), 'temporary files left behind')
        self.assertEqual(self.run_script('--force').returncode, 2)


if __name__ == '__main__':
    unittest.main()
