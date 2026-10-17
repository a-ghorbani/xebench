"""Fail-closed publication tests for scripts/aggregate.mjs and data validation.

Each test runs the aggregator from a temporary copy of the repository layout
(scripts/ + data/), so the checked-in data/benchmarks.json is never written.
"""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EXISTING_KEY = 'llama.cpp|cpu|android|Google Pixel 9|Llama 3.2 1B|Q4_0|measured'


def aggregate(values):
    ordered = sorted(values)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    return {'median': median, 'iqr': 0, 'min': ordered[0], 'max': ordered[-1], 'n': len(ordered)}


def legacy_record(model='Test Model 1B', quant='Q4_0', runs=3, n_cold_runs=None, **overrides):
    """A pre-schemaVersion app record: coldRuns holds only successful repetitions."""
    cold = [{'runIndex': i, 'loadMs': 900 + i, 'ttftMs': 1000 + 10 * i, 'promptTokens': 512,
             'prefillTps': 200 + i, 'prefillMethod': 'engine-timings', 'decodeTokens': 128,
             'decodeTps': 25 + i, 'decodeMethod': 'engine-timings', 'peakPssMb': 900 + i,
             'deltaPssMb': 700 + i} for i in range(runs)]
    metric = lambda name: aggregate([run[name] for run in cold]) if cold else None
    record = {
        'schema': 'xebench-raw', 'harnessVersion': 'xebench-0.1', 'runId': 'run-' + model,
        'timestampIso': '2026-07-01T10:00:00Z', 'engine': 'llama.cpp', 'engineVersion': 'llama.rn 0.6',
        'backend': 'cpu', 'platform': 'android',
        'deviceInfo': {'manufacturer': 'Google', 'model': 'Pixel 9', 'soc': 'Tensor G4'},
        'model': model, 'quant': quant, 'modelFile': 'model.gguf', 'modelFileSizeMb': 700,
        'protocol': {'nColdRuns': runs if n_cold_runs is None else n_cold_runs, 'promptLabel': 'std-pp512-v1',
                     'promptChars': 2000, 'maxDecodeTokens': 128, 'cooldownSec': 0, 'sustainedTargetSec': 0,
                     'nThreads': 4, 'nCtx': 2048, 'coldDefinition': 'fresh engine instance per run'},
        'coldRuns': cold,
        'summary': {name: metric(name) for name in
                    ('ttftMs', 'prefillTps', 'decodeTps', 'peakPssMb', 'deltaPssMb', 'loadMs')} if cold else None,
        'sustained': None, 'processReused': False, 'guardsPassed': True, 'guardNotes': [],
    }
    record.update(overrides)
    return record


def legacy_app_record(**overrides):
    """The real shape emitted by the pre-schemaVersion app (harness xebench-0.4-app):
    no timestamp, a stub coldRuns entry, and summary n fixed to the run count."""
    record = {
        'schema': 'xebench-raw', 'harnessVersion': 'xebench-0.4-app', 'engine': 'llama.cpp',
        'engineVersion': 'llama.rn', 'backend': 'cpu', 'platform': 'android',
        'deviceInfo': {'model': 'Pixel 9', 'soc': '', 'androidRelease': '15'},
        'model': 'Legacy App Model', 'quant': 'Q4_0', 'modelFile': 'm.gguf',
        'protocol': {'nColdRuns': 3, 'promptLabel': 'std-pp512-v1', 'maxDecodeTokens': 128,
                     'coldDefinition': 'fresh engine instance, warm foreground app process (top-app cpuset)'},
        'coldRuns': [{'runIndex': 0, 'prefillMethod': 'engine-timings', 'decodeMethod': 'engine-timings'}],
        'summary': {'prefillTps': {'median': 101.23, 'n': 3, 'min': 100, 'max': 102},
                    'decodeTps': {'median': 20.55, 'n': 3, 'min': 20, 'max': 21},
                    'ttftMs': {'median': 900, 'n': 3}, 'loadMs': {'median': 500, 'n': 3}},
        'guardsPassed': True, 'guardNotes': [],
    }
    record.update(overrides)
    return record


def llama_bench_record(**overrides):
    """The shape written by scripts/bench_llamacpp.py (harness xebench-0.2-native)."""
    agg = lambda v: {'median': v, 'mean': v, 'stddev': None, 'iqr': None, 'min': v, 'max': v, 'n': 3}
    record = legacy_record(model='Bench Model', harnessVersion='xebench-0.2-native',
                           coldRuns=[{'runIndex': 0, 'prefillMethod': 'engine-timings',
                                      'decodeMethod': 'engine-timings'}])
    record['protocol']['coldDefinition'] = 'llama-bench internal warmup + reps (not app cold-start)'
    record['summary'] = {'ttftMs': agg(None), 'prefillTps': agg(871.42), 'decodeTps': agg(43.18),
                         'peakPssMb': agg(None), 'deltaPssMb': agg(None), 'loadMs': agg(None)}
    record.update(overrides)
    return record


def reference_record(runs=3, **overrides):
    """A schemaVersion 1 CPU reference session (xebenchapp/src/harness/session.ts)."""
    cold = [{'runIndex': i, 'status': 'complete', 'loadMs': 800 + i, 'ttftMs': 900 + i, 'promptTokens': 512,
             'prefillTps': 300 + i, 'prefillMethod': 'engine-timings', 'decodeTokens': 64, 'decodeTps': 30 + i,
             'decodeMethod': 'engine-timings'} for i in range(runs)]
    record = {
        'schema': 'xebench-raw', 'schemaVersion': 1, 'harnessVersion': 'xebench-0.5-reference',
        'runId': 'ref-1', 'timestampIso': '2026-10-01T10:00:00Z',
        'deviceInfo': {'model': 'Pixel 9', 'androidRelease': '15'},
        'artifact': {'sha256': '0' * 64, 'sizeBytes': 1}, 'engine': 'llama.cpp', 'engineVersion': 'llama.rn 0.6',
        'backend': 'cpu', 'observedBackend': None, 'platform': 'android', 'model': 'Reference Model',
        'quant': 'Q4_0', 'modelFile': 'model.gguf', 'configuration': {}, 'configurationRunId': 'a',
        'protocol': {'version': 'cpu-reference-v1', 'nColdRuns': runs, 'cooldownSec': 0, 'promptLabel': 'std-pp512-v1',
                     'prompt': 'x', 'maxDecodeTokens': 64, 'nThreads': 4, 'nCtx': 2048,
                     'coldDefinition': 'fresh engine instance in warm app process'},
        'status': 'complete', 'coldRuns': cold,
        'summary': {name: aggregate([run[name] for run in cold])
                    for name in ('loadMs', 'ttftMs', 'prefillTps', 'decodeTps')},
        'guardsPassed': None, 'guardNotes': ['Run conditions and actual backend execution have not been verified'],
    }
    record.update(overrides)
    return record


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copytree(ROOT / 'scripts', self.root / 'scripts')
        shutil.copytree(ROOT / 'data', self.root / 'data')
        self.canonical = self.root / 'data/benchmarks.json'
        self.inputs = self.root / 'inputs'
        self.inputs.mkdir()
        self.counter = 0

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, *records, raw=None):
        self.counter += 1
        path = self.inputs / f'raw-{self.counter:03d}.jsonl'
        text = raw if raw is not None else ''.join(json.dumps(r) + '\n' for r in records)
        path.write_text(text, encoding='utf-8')
        return path

    def run_aggregate(self, *args):
        return subprocess.run(['node', str(self.root / 'scripts/aggregate.mjs'), *map(str, args)],
                              capture_output=True, text=True)

    def digest(self):
        return hashlib.sha256(self.canonical.read_bytes()).hexdigest()

    def published(self):
        return json.loads(self.canonical.read_text(encoding='utf-8'))

    def assert_rejected(self, *args, code=1, reason=None):
        before = self.digest()
        result = self.run_aggregate(*args)
        self.assertEqual(result.returncode, code, result.stderr)
        if reason is not None:
            self.assertIn(reason, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertEqual(before, self.digest(), 'canonical data changed on a rejected run')
        self.assertEqual(sorted(p.name for p in self.canonical.parent.iterdir()),
                         sorted(p.name for p in (ROOT / 'data').iterdir()), 'temporary files left behind')
        return result

    def preview(self, *paths):
        result = self.run_aggregate(*paths)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout), result.stderr

    # 1. canonical data fails closed
    def test_malformed_canonical_is_left_byte_identical(self):
        path = self.write(legacy_record())
        good = self.published()
        dup = copy.deepcopy(good)
        dup['rows'].append(copy.deepcopy(dup['rows'][0]))
        dup['rowCount'] += 1
        bad_row = copy.deepcopy(good)
        bad_row['rows'][0]['provenance'] = 'verified'
        extra = copy.deepcopy(good)
        extra['rows'][0]['unknownField'] = 1
        miscount = copy.deepcopy(good)
        miscount['rowCount'] = 3
        major = copy.deepcopy(good)
        major['schemaVersion'] = '2.0'
        cases = {
            'truncated': self.canonical.read_text()[:500],
            'empty': '',
            'not-json': 'garbage',
            'non-finite': json.dumps(good).replace('"ppTps": 1916', '"ppTps": 1e999'),
            'duplicate-key': json.dumps(dup),
            'schema-invalid': json.dumps(bad_row),
            'additional-property': json.dumps(extra),
            'row-count': json.dumps(miscount),
            'unsupported-major': json.dumps(major),
        }
        for name, text in cases.items():
            with self.subTest(case=name):
                self.canonical.write_text(text, encoding='utf-8')
                result = self.assert_rejected(path, '--update', '--allow-legacy')
                self.assertIn('not modified', result.stderr)
        with self.subTest(case='missing'):
            self.canonical.unlink()
            result = self.run_aggregate(path, '--update', '--allow-legacy')
            self.assertEqual(result.returncode, 1)
            self.assertFalse(self.canonical.exists())

    # 2. malformed input fails closed, in preview and update
    def test_malformed_input_is_rejected(self):
        good = json.dumps(legacy_record())
        cases = {
            'truncated': good + '\n' + good[: len(good) // 2],
            'garbage': 'not json\n',
            'not-object': '[1, 2]\n',
            'overflow': good.replace('"decodeTps": 26', '"decodeTps": 1e999') + '\n',
            'nan-literal': good.replace('"decodeTps": 26', '"decodeTps": NaN') + '\n',
            'wrong-schema': json.dumps({**legacy_record(), 'schema': 'something-else'}) + '\n',
            'no-records': '\n\n',
            'missing-engine': json.dumps({k: v for k, v in legacy_record().items() if k != 'engine'}) + '\n',
            'unknown-engine': json.dumps(legacy_record(engine='mystery')) + '\n',
            'zero-decode': json.dumps(legacy_record(summary={**legacy_record()['summary'],
                                                             'decodeTps': aggregate([0, 0, 0])})) + '\n',
            'no-summary': json.dumps(legacy_record(runs=0, n_cold_runs=3)) + '\n',
        }
        reasons = {
            'truncated': 'not valid JSON', 'garbage': 'not valid JSON', 'not-object': 'not a JSON object',
            'overflow': 'non-finite', 'nan-literal': 'not valid JSON', 'wrong-schema': 'unexpected schema',
            'no-records': 'no records', 'missing-engine': 'missing engine', 'unknown-engine': 'is not one of',
            'zero-decode': 'not a valid measurement', 'no-summary': 'summary is missing',
        }
        for name, text in cases.items():
            path = self.write(raw=text)
            with self.subTest(case=name, mode='preview'):
                self.assert_rejected(path, reason=reasons[name])
            with self.subTest(case=name, mode='update'):
                self.assert_rejected(path, '--update', '--allow-legacy', reason=reasons[name])
        with self.subTest(case='empty-file-among-others'):
            self.assert_rejected(self.write(raw=''), self.write(legacy_record()), reason='no records')
        with self.subTest(case='byte-order-mark-is-accepted'):
            rows, _ = self.preview(self.write(raw='\ufeff' + good + '\n'))
            self.assertEqual(len(rows), 1)
        with self.subTest(case='unreadable-file'):
            self.assert_rejected(self.inputs / 'missing.jsonl', '--update', '--allow-legacy')
        with self.subTest(case='unknown-flag'):
            self.assert_rejected(self.write(legacy_record()), '--update', '--allow-legacy', '--force', code=2)
        with self.subTest(case='allow-legacy-without-update'):
            self.assert_rejected(self.write(legacy_record()), '--allow-legacy', code=2)

    # 3. legacy records need an explicit flag
    def test_legacy_record_requires_explicit_flag(self):
        path = self.write(legacy_record())
        result = self.assert_rejected(path, '--update')
        self.assertIn('--allow-legacy', result.stderr)
        result = self.run_aggregate(path, '--update', '--allow-legacy')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('LEGACY', result.stderr)
        data = self.published()
        self.assertEqual(data['rowCount'], len(data['rows']))
        self.assertEqual(data['rowCount'], 42)
        row = next(r for r in data['rows'] if r['model'] == 'Test Model 1B')
        self.assertEqual(row['provenance'], 'measured')
        self.assertEqual((row['ppTps'], row['tgTps'], row['ttftMs'], row['memoryMb']), (201, 26, 1010, 701))
        self.assertEqual(sorted(p.name for p in self.canonical.parent.iterdir()),
                         sorted(p.name for p in (ROOT / 'data').iterdir()), 'temporary files left behind')
        check = subprocess.run(['node', str(self.root / 'scripts/validate_data.mjs')], capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stderr)

    def test_update_replaces_the_file_atomically(self):
        self.canonical.chmod(0o664)
        inode = self.canonical.stat().st_ino
        result = self.run_aggregate(self.write(legacy_record()), '--update', '--allow-legacy')
        self.assertEqual(result.returncode, 0, result.stderr)
        stat = self.canonical.stat()
        self.assertNotEqual(stat.st_ino, inode, 'canonical file was rewritten in place, not renamed over')
        self.assertEqual(stat.st_mode & 0o777, 0o664)
        self.assertEqual(sorted(p.name for p in self.canonical.parent.iterdir()),
                         sorted(p.name for p in (ROOT / 'data').iterdir()))

    def test_short_write_leaves_canonical_unchanged(self):
        before = self.digest()
        path = self.write(legacy_record())
        # A 20 KiB file-size limit makes the temp-file write fail part-way (EFBIG).
        result = subprocess.run(['bash', '-c', 'ulimit -f 20; exec node "$0" "$1" --update --allow-legacy',
                                 str(self.root / 'scripts/aggregate.mjs'), str(path)],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, self.digest())
        self.assertEqual(sorted(p.name for p in self.canonical.parent.iterdir()),
                         sorted(p.name for p in (ROOT / 'data').iterdir()), 'temporary files left behind')

    # 4. sampleCount is the verified number of successful repetitions
    def test_sample_count_comes_from_successful_repetitions(self):
        rows, stderr = self.preview(self.write(legacy_record(runs=2, n_cold_runs=5)))
        self.assertEqual(rows[0]['sampleCount'], 2)
        self.assertTrue(rows[0]['asStated'].startswith('n=2 '))
        self.assertIn('incomplete session (2/5', stderr)
        self.assert_rejected(self.write(legacy_record(runs=2, n_cold_runs=5)), '--update', '--allow-legacy')

        rows, _ = self.preview(self.write(reference_record(runs=4)))
        self.assertEqual(rows[0]['sampleCount'], 4)

        mismatched = legacy_record()
        mismatched['summary']['decodeTps']['n'] = 4
        self.assert_rejected(self.write(mismatched), reason='disagree on n')
        mismatched['summary'] = {k: {**v, 'n': 4} for k, v in mismatched['summary'].items()}
        mismatched['protocol']['nColdRuns'] = 4
        self.assert_rejected(self.write(mismatched), reason='successful repetitions')
        missing_run = legacy_record()
        missing_run['coldRuns'].pop()
        self.assert_rejected(self.write(missing_run), reason='successful repetitions')
        missing_metric = legacy_record()
        del missing_metric['coldRuns'][1]['decodeTps']
        self.assert_rejected(self.write(missing_metric), reason='2 of 3 successful repetitions with decodeTps')
        disagree = legacy_record()
        disagree['summary']['prefillTps']['n'] = 2
        self.assert_rejected(self.write(disagree), reason='disagree on n')
        overcount = legacy_record(runs=3, n_cold_runs=2)
        self.assert_rejected(self.write(overcount), reason='exceeds protocol.nColdRuns')
        failed_rep = reference_record()
        failed_rep['coldRuns'][1] = {'runIndex': 1, 'status': 'failed', 'metrics': None,
                                     'error': {'phase': 'inference', 'message': 'x'}}
        self.assert_rejected(self.write(failed_rep), reason='successful repetitions')

    def test_legacy_producers_without_repetitions(self):
        rows, stderr = self.preview(self.write(legacy_app_record(), llama_bench_record()))
        by_model = {row['model']: row for row in rows}
        app = by_model['Legacy App Model']
        self.assertEqual((app['sampleCount'], app['ppTps'], app['tgTps'], app['ttftMs'], app['memoryMb']),
                         (3, 101.2, 20.6, 900, None))
        self.assertNotIn('sourceDate', app)
        self.assertNotIn('soc', app)
        self.assertEqual(app['asStated'], 'n=3 cold-run median; prompt std-pp512-v1; decode 128 tok')
        self.assertEqual(by_model['Bench Model']['asStated'],
                         'n=3 reps (llama-bench mean); prompt std-pp512-v1; decode 128 tok')
        self.assertIn('legacy app record: n is the configured run count', stderr)
        self.assertIn('llama-bench record: n is self-reported', stderr)
        result = self.run_aggregate(self.write(legacy_app_record(), llama_bench_record()), '--update', '--allow-legacy')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.published()['rowCount'], 43)
        # Only known producers may omit repetitions; a self-declared llama-bench label is not enough.
        for name, record in {
            'unknown-harness': legacy_app_record(harnessVersion='xebench-9.9'),
            'claimed-llama-bench': llama_bench_record(harnessVersion='xebench-0.1', coldRuns=[]),
        }.items():
            with self.subTest(case=name):
                self.assert_rejected(self.write(record), reason='cannot be verified')
        self.assert_rejected(self.write(legacy_app_record(timestampIso='yesterday')), reason='timestampIso')

    # 5. output order does not depend on input order or locale
    def test_order_is_deterministic(self):
        records = [legacy_record(model=m, quant=q) for m in ('B Model', 'a model', 'A Model') for q in ('Q8_0', 'Q4_0')]
        forward = self.write(*records)
        backward = self.write(*reversed(records))
        rows_forward, _ = self.preview(forward)
        rows_backward, _ = self.preview(backward)
        self.assertEqual(rows_forward, rows_backward)
        self.assertEqual([(r['model'], r['quant']) for r in rows_forward],
                         [('A Model', 'Q4_0'), ('A Model', 'Q8_0'), ('B Model', 'Q4_0'), ('B Model', 'Q8_0'),
                          ('a model', 'Q4_0'), ('a model', 'Q8_0')])

        results = []
        for path in (forward, backward):
            shutil.copyfile(ROOT / 'data/benchmarks.json', self.canonical)
            result = self.run_aggregate(path, '--update', '--allow-legacy')
            self.assertEqual(result.returncode, 0, result.stderr)
            results.append(self.published()['rows'])
        self.assertEqual(results[0], results[1])
        key = lambda r: tuple(r.get(f, '') for f in
                              ('platform', 'device', 'model', 'engine', 'backend', 'quant', 'provenance'))
        self.assertEqual(results[0], sorted(results[0], key=key))

    # 6. row-key collisions are explicit
    def test_row_key_collisions(self):
        twice = self.write(legacy_record(), legacy_record(runId='other'))
        self.assert_rejected(twice, '--update', '--allow-legacy', reason='2 input records map to row key')
        rows, stderr = self.preview(twice)
        self.assertEqual(len(rows), 2)
        self.assertIn('map to row key', stderr)

        before = self.published()
        old = next(r for r in before['rows'] if '|'.join(
            str(r.get(f, '')) for f in ('engine', 'backend', 'platform', 'device', 'model', 'quant',
                                        'provenance')) == EXISTING_KEY)
        result = self.run_aggregate(self.write(legacy_record(model='Llama 3.2 1B', quant='Q4_0')),
                                    '--update', '--allow-legacy')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f'replaced existing row {EXISTING_KEY}', result.stderr)
        after = self.published()
        self.assertEqual(after['rowCount'], before['rowCount'])
        new = next(r for r in after['rows'] if r['model'] == 'Llama 3.2 1B' and r['quant'] == 'Q4_0'
                   and r['device'] == 'Google Pixel 9' and r['engine'] == 'llama.cpp' and r['backend'] == 'cpu')
        self.assertNotEqual(old['tgTps'], new['tgTps'])
        self.assertEqual(new['tgTps'], 26)
        untouched = lambda data: [r for r in data['rows'] if r is not None and not (
            r['model'] == 'Llama 3.2 1B' and r.get('quant') == 'Q4_0' and r['device'] == 'Google Pixel 9'
            and r['backend'] == 'cpu')]
        self.assertCountEqual(untouched(before), untouched(after))

    # 7. failed guards and incomplete sessions are never published
    def test_failed_guard_and_incomplete_records_are_not_published(self):
        failed = legacy_record(guardsPassed=False, guardNotes=['battery 20% < 50%'])
        result = self.assert_rejected(self.write(failed), '--update', '--allow-legacy')
        self.assertIn('guards did not pass', result.stderr)
        rows, _ = self.preview(self.write(failed))
        self.assertIn('GUARDS FAILED', rows[0]['asStated'])
        self.assert_rejected(self.write(legacy_record(guardsPassed=None)), '--update', '--allow-legacy',
                             reason='guardsPassed must be a boolean')
        self.assert_rejected(self.write(legacy_record(runs=0, n_cold_runs=3)), '--update', '--allow-legacy',
                             reason='summary is missing')
        for status in ('running', 'failed'):
            with self.subTest(status=status):
                self.assert_rejected(self.write(reference_record(status=status)), reason='Only complete')
        # a whole batch is rejected when one record is unpublishable
        self.assert_rejected(self.write(legacy_record(model='Fine'), failed), '--update', '--allow-legacy',
                             reason='guards did not pass')

    def test_flagged_reference_session_previews_but_never_publishes(self):
        # observed-conditions-v1 (session.ts) sets guardsPassed=false on a flagged
        # endpoint and keeps the measurements; it never sets true.
        flagged = reference_record(
            guardsPassed=False,
            guardNotes=['Run conditions and actual backend execution have not been verified', 'not-foreground'],
            conditionPolicy={'version': 'observed-conditions-v1', 'mode': 'record-and-flag'})
        rows, _ = self.preview(self.write(flagged))
        self.assertEqual(rows[0]['sampleCount'], 3)
        self.assertIn('GUARDS FAILED', rows[0]['asStated'])
        self.assertIn('not-foreground', rows[0]['asStated'])
        self.assert_rejected(self.write(flagged), '--update', '--allow-legacy', reason='preview-only')
        self.assert_rejected(self.write(reference_record(guardsPassed=True)), reason='guardsPassed must be null or false')

    # 8. unsupported schema/protocol versions are rejected
    def test_incompatible_versions_are_rejected(self):
        ref = reference_record()
        cases = {
            'schema-v2': {**ref, 'schemaVersion': 2},
            'schema-string': {**ref, 'schemaVersion': '1'},
            'schema-null': {**ref, 'schemaVersion': None},
            'protocol-v2': {**ref, 'protocol': {**ref['protocol'], 'version': 'cpu-reference-v2'}},
            'protocol-missing': {**ref, 'protocol': {k: v for k, v in ref['protocol'].items() if k != 'version'}},
            'legacy-with-protocol-version': legacy_record(protocol={**legacy_record()['protocol'],
                                                                   'version': 'cpu-reference-v1'}),
        }
        reasons = {'schema-v2': 'Only complete', 'schema-string': 'Only complete', 'schema-null': 'Only complete',
                   'protocol-v2': 'unsupported protocol version', 'protocol-missing': 'unsupported protocol version',
                   'legacy-with-protocol-version': 'declares protocol.version'}
        for name, record in cases.items():
            with self.subTest(case=name):
                self.assert_rejected(self.write(record), reason=reasons[name])
                self.assert_rejected(self.write(record), '--update', '--allow-legacy', reason=reasons[name])
        # schemaVersion 1 sessions stay preview-only until consumer migration
        self.preview(self.write(ref))
        result = self.assert_rejected(self.write(ref), '--update', '--allow-legacy')
        self.assertIn('preview-only', result.stderr)


class DataValidationTests(unittest.TestCase):
    def run_node(self, script):
        return subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True, cwd=ROOT)

    def test_checked_in_data_conforms(self):
        result = subprocess.run(['node', str(ROOT / 'scripts/validate_data.mjs')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unsupported_schema_keywords_fail_closed(self):
        result = self.run_node(
            "import {validateSchema} from './scripts/lib/publication.mjs';"
            "validateSchema('x', {type: 'string', pattern: '^y$'});")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unsupported schema keyword pattern', result.stderr)

    def test_schema_is_checked_eagerly(self):
        for name, schema, message in [
            ('unreached-def', "{type: 'string', $defs: {x: {type: 'integer', exclusiveMinimum: 1}}}",
             'unsupported schema keyword exclusiveMinimum at #/$defs/x'),
            ('unreached-property', "{type: 'object', properties: {x: {pattern: 'a'}}}",
             'unsupported schema keyword pattern at #/properties/x'),
            ('bogus-type', "{type: ['number', 'bogus']}", 'unsupported schema type \"bogus\"'),
            ('enum-not-array', "{enum: 'x'}", 'enum at # must be an array'),
        ]:
            with self.subTest(case=name):
                result = self.run_node("import {validateSchema} from './scripts/lib/publication.mjs';"
                                       f"validateSchema(1, {schema});")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)

    def test_validator_reports_errors(self):
        result = self.run_node(
            "import {validateSchema} from './scripts/lib/publication.mjs';"
            "const s = {type: 'object', required: ['a'], additionalProperties: false,"
            " properties: {a: {type: 'integer', minimum: 1}, u: {type: 'string', format: 'uri'}}};"
            "console.log(JSON.stringify(["
            " validateSchema({a: 1, u: 'https://x.test/'}, s).length,"
            " validateSchema({a: 0}, s).length, validateSchema({}, s).length,"
            " validateSchema({a: 1, constructor: 1}, s).length, validateSchema({a: 1, u: 'nope'}, s).length,"
            " validateSchema({a: 1.5}, s).length]));")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [0, 1, 1, 1, 1, 1])


if __name__ == '__main__':
    unittest.main()
