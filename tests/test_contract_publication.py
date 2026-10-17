"""Contract 1.1 tests: migration, row invariants, generatedAt forms and consumer fixtures.

Like test_publication.py, every test that writes runs against a temporary copy of
scripts/ + data/, so the checked-in data/benchmarks.json is never modified.
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

from test_publication import legacy_record, reference_record

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/contract-1.1'
NEW_FIELDS = ('validation', 'guardsPassed', 'conditionPolicy', 'protocolVersion', 'timingMethod', 'dispersion',
              'evidence', 'build')
ISO = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def verified_row():
    return copy.deepcopy(load(FIXTURES / 'verified.json')['rows'][0])


def envelope_with(*rows, **fields):
    envelope = {**load(FIXTURES / 'verified.json'), **fields}
    envelope['rows'] = list(rows)
    envelope['rowCount'] = len(rows)
    return envelope


def as_1_0(envelope):
    """The pre-migration shape of a 1.1 file: what main held before this contract."""
    old = copy.deepcopy(envelope)
    old['schemaVersion'] = '1.0'
    old['generatedAt'] = '2026-07-22'
    for row in old['rows']:
        if row['provenance'] == 'measured':
            assert row.pop('validation') == 'legacy'
    return old


def dump(envelope):
    return json.dumps(envelope, indent=2, ensure_ascii=False) + '\n'


class Workspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copytree(ROOT / 'scripts', self.root / 'scripts')
        shutil.copytree(ROOT / 'data', self.root / 'data')
        self.canonical = self.root / 'data/benchmarks.json'
        self.counter = 0

    def tearDown(self):
        self.tmp.cleanup()

    def node(self, script, *args):
        return subprocess.run(['node', str(self.root / 'scripts' / script), *map(str, args)],
                              capture_output=True, text=True)

    def digest(self):
        return hashlib.sha256(self.canonical.read_bytes()).hexdigest()

    def no_leftovers(self):
        self.assertEqual(sorted(p.name for p in self.canonical.parent.iterdir()),
                         sorted(p.name for p in (ROOT / 'data').iterdir()), 'temporary files left behind')

    def write_json(self, value, text=None):
        self.counter += 1
        path = self.root / f'envelope-{self.counter:03d}.json'
        path.write_text(text if text is not None else dump(value), encoding='utf-8')
        return path

    def validate(self, envelope):
        return self.node('validate_data.mjs', self.write_json(envelope))

    def assert_valid(self, envelope):
        result = self.validate(envelope)
        self.assertEqual(result.returncode, 0, result.stderr)

    def assert_invalid(self, envelope, reason):
        result = self.validate(envelope)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(reason, result.stderr)

    def schema_errors(self, row):
        """Errors from the JSON Schema alone (what a consumer's stock validator would see)."""
        script = ("import {validateSchema} from './scripts/lib/publication.mjs';"
                  "import {readFileSync} from 'node:fs';"
                  "const s = JSON.parse(readFileSync('./data/benchmarks.schema.json', 'utf8'));"
                  f"console.log(JSON.stringify(validateSchema({json.dumps(row)}, s.$defs.row, s)));")
        result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True,
                                cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)


class MigrationTests(Workspace):
    def migrate(self, *args):
        return self.node('migrate_contract_1_1.mjs', *args)

    def test_checked_in_data_is_migrated(self):
        result = subprocess.run(['node', str(ROOT / 'scripts/migrate_contract_1_1.mjs'), '--check'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = load(ROOT / 'data/benchmarks.json')
        self.assertEqual(data['schemaVersion'], '1.1')
        self.assertRegex(data['generatedAt'], ISO)
        for row in data['rows']:
            if row['provenance'] == 'measured':
                self.assertEqual(row['validation'], 'legacy')
                self.assertEqual(list(row)[-1], 'validation', 'validation must be appended, not interleaved')
                self.assertEqual([f for f in NEW_FIELDS if f in row], ['validation'])
            else:
                self.assertEqual([f for f in NEW_FIELDS if f in row], [])

    def test_migration_from_1_0_and_idempotence(self):
        current = load(ROOT / 'data/benchmarks.json')
        old = as_1_0(current)
        old_text = dump(old)
        self.canonical.write_text(old_text, encoding='utf-8')

        check = self.migrate('--check')
        self.assertEqual(check.returncode, 1, check.stderr)
        self.assertIn('needs the 1.1 migration', check.stderr)
        self.assertEqual(self.canonical.read_text(encoding='utf-8'), old_text, '--check must not write')

        first = self.migrate()
        self.assertEqual(first.returncode, 0, first.stderr)
        measured = sum(r['provenance'] == 'measured' for r in old['rows'])
        self.assertIn(f'{measured} measured row(s) labelled validation=legacy', first.stderr)
        migrated_text = self.canonical.read_text(encoding='utf-8')
        migrated = json.loads(migrated_text)
        self.assertEqual(migrated['schemaVersion'], '1.1')
        self.assertRegex(migrated['generatedAt'], ISO)
        # Rows are exactly the checked-in migrated rows, key order included.
        self.assertEqual(json.dumps(migrated['rows']), json.dumps(current['rows']))
        self.assertEqual({k: v for k, v in migrated.items() if k not in ('schemaVersion', 'generatedAt')},
                         {k: v for k, v in current.items() if k not in ('schemaVersion', 'generatedAt')})
        self.no_leftovers()

        inode = self.canonical.stat().st_ino
        before = self.digest()
        for args in ((), ('--check',)):
            with self.subTest(second_run=args):
                again = self.migrate(*args)
                self.assertEqual(again.returncode, 0, again.stderr)
                self.assertEqual(self.digest(), before)
                self.assertEqual(self.canonical.stat().st_ino, inode, 'an idempotent run must not rewrite the file')
        self.assertIn('nothing to do', self.migrate().stderr)
        self.assertEqual(self.node('validate_data.mjs').returncode, 0)

    def test_vendor_rows_are_byte_identical(self):
        current = load(ROOT / 'data/benchmarks.json')
        old_text = dump(as_1_0(current))
        self.canonical.write_text(old_text, encoding='utf-8')
        self.assertEqual(self.migrate().returncode, 0)
        new_text = self.canonical.read_text(encoding='utf-8')

        old_rows, new_rows = json.loads(old_text)['rows'], json.loads(new_text)['rows']
        for old, new in zip(old_rows, new_rows, strict=True):
            if old['provenance'] != 'measured':
                self.assertEqual(json.dumps(old), json.dumps(new))
                self.assertIn(json.dumps(old, indent=2).replace('\n', '\n    '), new_text)
            else:
                self.assertEqual(json.dumps({**old, 'validation': 'legacy'}), json.dumps(new))

        # Textually, the only changes are the two envelope fields and one appended line per measured row.
        removed, added = [], []
        for line in difflib.unified_diff(old_text.splitlines(), new_text.splitlines(), lineterm='', n=0):
            if line.startswith(('---', '+++', '@@')):
                continue
            (removed if line.startswith('-') else added).append(line[1:])
        envelope = lambda lines: [l for l in lines if l.startswith(('  "schemaVersion"', '  "generatedAt"'))]
        self.assertEqual(len(envelope(removed)), 2)
        self.assertEqual(len(envelope(added)), 2)
        body_removed = [l for l in removed if l not in envelope(removed)]
        body_added = [l for l in added if l not in envelope(added)]
        measured = sum(r['provenance'] == 'measured' for r in old_rows)
        self.assertEqual(body_added.count('      "validation": "legacy"'), measured)
        self.assertEqual(sorted(l + ',' for l in body_removed),
                         sorted(l for l in body_added if l != '      "validation": "legacy"'))

    def test_migration_fails_closed(self):
        current = load(ROOT / 'data/benchmarks.json')
        old = as_1_0(current)
        smuggled = copy.deepcopy(old)
        measured = next(r for r in smuggled['rows'] if r['provenance'] == 'measured')
        measured['evidence'] = verified_row()['evidence']  # would become a legacy row with evidence
        cases = {
            'unsupported-major': dump({**old, 'schemaVersion': '2.0'}),
            'unknown-minor': dump({**current, 'schemaVersion': '1.2'}),
            'truncated': dump(old)[:300],
            'invalid-row': dump({**old, 'rows': [*old['rows'][:-1], {**old['rows'][-1], 'eligibility': 'x'}]}),
            'legacy-with-evidence': dump(smuggled),
        }
        reasons = {'unsupported-major': 'unsupported schemaVersion', 'unknown-minor': 'only 1.0 -> 1.1',
                   'truncated': 'not valid JSON', 'invalid-row': 'eligibility', 'legacy-with-evidence':
                   'legacy rows carry no evidence'}
        for name, text in cases.items():
            with self.subTest(case=name):
                self.canonical.write_text(text, encoding='utf-8')
                before = self.digest()
                result = self.migrate()
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(reasons[name], result.stderr)
                self.assertIn('not modified', result.stderr)
                self.assertEqual(self.digest(), before)
                self.no_leftovers()
        self.assertEqual(self.migrate('--force').returncode, 2)


class InvariantTests(Workspace):
    def test_invariants_are_rejected(self):
        row = verified_row()
        without = lambda r, *keys: {k: v for k, v in r.items() if k not in keys}
        legacy = {**without(row, 'guardsPassed', 'dispersion', 'evidence'), 'validation': 'legacy'}
        cases = {
            # verified requires guardsPassed === true and evidence
            'verified-guards-null': ({**row, 'guardsPassed': None}, 'requires guardsPassed === true', True),
            'verified-guards-missing': (without(row, 'guardsPassed'), 'requires guardsPassed === true', True),
            'verified-without-evidence': (without(row, 'evidence'), 'requires evidence', True),
            # guardsPassed=false requires validation failed
            'guards-false-unverified': ({**row, 'guardsPassed': False, 'validation': 'unverified'},
                                        'guardsPassed=false requires validation=failed', True),
            'guards-false-legacy': ({**legacy, 'guardsPassed': False},
                                    'guardsPassed=false requires validation=failed', True),
            'guards-false-no-validation': ({**without(row, 'validation'), 'guardsPassed': False},
                                           'guardsPassed=false requires validation=failed', True),
            # legacy rows carry no dispersion or evidence
            'legacy-with-dispersion': ({**legacy, 'dispersion': row['dispersion']}, 'legacy rows carry no dispersion',
                                       True),
            'legacy-with-evidence': ({**legacy, 'evidence': row['evidence']}, 'legacy rows carry no evidence', True),
            # dispersion: finite non-negative, min <= max (min <= max is beyond the schema)
            'dispersion-min-above-max': ({**row, 'dispersion': {'tgTps': {'iqr': 1, 'min': 30, 'max': 20}}},
                                         'min 30 > max 20', False),
            'dispersion-negative': ({**row, 'dispersion': {'ppTps': {'iqr': -1, 'min': 1, 'max': 2}}},
                                    '< minimum 0', True),
            'dispersion-missing-iqr': ({**row, 'dispersion': {'ppTps': {'min': 1, 'max': 2}}},
                                       'missing required iqr', True),
            'dispersion-unknown-metric': ({**row, 'dispersion': {'ttftMs': {'iqr': 0, 'min': 1, 'max': 2}}},
                                          'dispersion.ttftMs: not allowed', True),
            'dispersion-string': ({**row, 'dispersion': {'ppTps': {'iqr': '1', 'min': 1, 'max': 2}}},
                                  'expected number', True),
            # evidence shape
            'evidence-no-sessions': ({**row, 'evidence': {**row['evidence'], 'sessionIds': []}}, 'fewer than 1', True),
            'evidence-empty-session': ({**row, 'evidence': {**row['evidence'], 'sessionIds': ['']}},
                                       'shorter than 1', True),
            'evidence-duplicate-session': ({**row, 'evidence': {**row['evidence'], 'sessionIds': ['a', 'a']}},
                                           'duplicate session id', False),
            'evidence-uppercase-sha': ({**row, 'evidence': {**row['evidence'],
                                                           'bundleSha256': row['evidence']['bundleSha256'].upper()}},
                                       'does not match', True),
            'evidence-short-sha': ({**row, 'evidence': {**row['evidence'], 'bundleSha256': 'ab' * 31}},
                                   'does not match', True),
            'evidence-missing-url': ({**row, 'evidence': without(row['evidence'], 'url')}, 'missing required url',
                                     True),
            'evidence-extra-key': ({**row, 'evidence': {**row['evidence'], 'path': '/sdcard/x'}},
                                   'evidence.path: not allowed', True),
            # build: strings only, known keys only
            'build-extra-key': ({**row, 'build': {'serial': 'abc'}}, 'build.serial: not allowed', True),
            'build-non-string': ({**row, 'build': {'engineVersion': 1}}, 'expected string', True),
            # other field shapes
            'unknown-validation': ({**row, 'validation': 'pending'}, 'is not one of', True),
            'guards-string': ({**row, 'guardsPassed': 'true'}, 'expected boolean|null', True),
            # eligibility is deferred to 1.2
            'eligibility-measured': ({**row, 'eligibility': 'eligible'}, 'eligibility: not allowed', True),
        }
        for name, (bad, reason, schema_alone) in cases.items():
            with self.subTest(case=name):
                self.assert_invalid(envelope_with(bad), reason)
                errors = self.schema_errors(bad)
                if schema_alone:
                    self.assertTrue(errors, f'the schema alone should reject {name}')
                else:
                    self.assertEqual(errors, [], f'{name} is a validator-only invariant')

    def test_new_fields_are_measured_only(self):
        vendor = load(FIXTURES / 'vendor.json')['rows'][0]
        row = verified_row()
        for provenance in ('vendor', 'community'):
            for field in NEW_FIELDS:
                with self.subTest(provenance=provenance, field=field):
                    bad = {**vendor, 'provenance': provenance, field: row[field]}
                    self.assert_invalid(envelope_with(bad), f'{field} is only valid on provenance=measured rows')
                    self.assertTrue(self.schema_errors(bad))
        with self.subTest(case='vendor-eligibility'):
            self.assert_invalid(envelope_with({**vendor, 'eligibility': 'eligible'}), 'eligibility: not allowed')

    def test_valid_measured_variants(self):
        row = verified_row()
        without = lambda r, *keys: {k: v for k, v in r.items() if k not in keys}
        variants = {
            'verified-minimal-dispersion': {**row, 'dispersion': {'tgTps': {'iqr': 0, 'min': 5, 'max': 5}}},
            'verified-no-optional': without(row, 'dispersion', 'build', 'conditionPolicy', 'protocolVersion',
                                            'timingMethod'),
            'failed-guards-true': {**row, 'validation': 'failed'},
            'failed-with-evidence': {**row, 'validation': 'failed', 'guardsPassed': False},
            'unverified-no-guards': without({**row, 'validation': 'unverified'}, 'guardsPassed', 'evidence'),
            'measured-1.0-shape': without(row, *NEW_FIELDS),
            'legacy-with-build': {**without(row, 'guardsPassed', 'dispersion', 'evidence'), 'validation': 'legacy'},
            'empty-build': {**row, 'build': {}},
        }
        for name, good in variants.items():
            with self.subTest(case=name):
                self.assert_valid(envelope_with(good))
                self.assertEqual(self.schema_errors(good), [])

    def test_evidence_url_must_be_https(self):
        row = verified_row()
        for url in ('https://example.com/e.tar.gz', 'https://example.com:8443/a/b?c=d'):
            with self.subTest(url=url, expect='valid'):
                self.assert_valid(envelope_with({**row, 'evidence': {**row['evidence'], 'url': url}}))
        for url in ('http://example.com/e.tar.gz', 'ftp://example.com/e', 'HTTPS://example.com/e', 'https://',
                    'https:example.com', '//example.com/e', 'example.com/e', 'javascript:alert(1)',
                    'file:///tmp/e', ''):
            with self.subTest(url=url, expect='invalid'):
                result = self.validate(envelope_with({**row, 'evidence': {**row['evidence'], 'url': url}}))
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn('evidence.url', result.stderr)

    def test_generated_at_forms(self):
        vendor = load(FIXTURES / 'vendor.json')['rows'][0]
        for value in ('2026-10-17', '2026-10-17T13:40:03Z', '2024-02-29', '2026-12-31T23:59:59Z'):
            with self.subTest(generatedAt=value, expect='valid'):
                self.assert_valid(envelope_with(vendor, generatedAt=value))
        for value in ('2026-10-17T13:40:03.123Z', '2026-10-17T13:40:03+00:00', '2026-10-17T13:40:03',
                      '2026-10-17 13:40:03Z', '2026-10-17T13:40Z', '20261017', '17/10/2026', '2026-10',
                      '2026-02-30', '2025-02-29', '2026-13-01', '2026-10-17T24:00:00Z', '2026-10-17T12:60:00Z', ''):
            with self.subTest(generatedAt=value, expect='invalid'):
                self.assert_invalid(envelope_with(vendor, generatedAt=value), 'generatedAt')


class FixtureTests(Workspace):
    def test_consumer_fixtures(self):
        index = load(FIXTURES / 'index.json')
        listed = {entry['file'] for entry in index['fixtures']}
        self.assertEqual(listed, {p.name for p in FIXTURES.glob('*.json')} - {'index.json'})
        for entry in index['fixtures']:
            with self.subTest(fixture=entry['file']):
                envelope = load(FIXTURES / entry['file'])
                self.assertEqual(envelope['rows'][0].get('validation'), entry['validation'])
                result = self.node('validate_data.mjs', FIXTURES / entry['file'])
                self.assertEqual(result.returncode, 0 if entry['expect'] == 'accept' else 1, result.stderr)
        self.assertIn('unsupported schemaVersion 2.0',
                      self.node('validate_data.mjs', FIXTURES / 'unsupported-major.json').stderr)
        # The verified fixture carries every 1.1 field, so the website sees the full shape.
        self.assertEqual([f for f in NEW_FIELDS if f not in load(FIXTURES / 'verified.json')['rows'][0]], [])

    def test_legacy_and_vendor_fixtures_match_canonical_rows(self):
        canonical = load(ROOT / 'data/benchmarks.json')['rows']
        for name in ('legacy.json', 'vendor.json'):
            with self.subTest(fixture=name):
                self.assertIn(load(FIXTURES / name)['rows'][0], canonical)


class ProducerTests(Workspace):
    def write_raw(self, *records):
        self.counter += 1
        path = self.root / f'raw-{self.counter:03d}.jsonl'
        path.write_text(''.join(json.dumps(r) + '\n' for r in records), encoding='utf-8')
        return path

    def aggregate(self, *args):
        return self.node('aggregate.mjs', *args)

    def assert_rejected(self, reason, *args):
        before = self.digest()
        result = self.aggregate(*args)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(reason, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertEqual(self.digest(), before)
        self.no_leftovers()

    def test_legacy_rows_are_published_as_legacy(self):
        result = self.aggregate(self.write_raw(legacy_record()), '--update', '--allow-legacy')
        self.assertEqual(result.returncode, 0, result.stderr)
        data = load(self.canonical)
        self.assertEqual(data['schemaVersion'], '1.1')
        self.assertRegex(data['generatedAt'], ISO)
        row = next(r for r in data['rows'] if r['model'] == 'Test Model 1B')
        self.assertEqual([f for f in NEW_FIELDS if f in row], ['validation'])
        self.assertEqual(row['validation'], 'legacy')
        self.assertEqual(self.node('validate_data.mjs').returncode, 0)
        self.assertEqual(self.node('migrate_contract_1_1.mjs', '--check').returncode, 0)

    def test_preview_labels(self):
        result = self.aggregate(self.write_raw(legacy_record()))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)[0]['validation'], 'legacy')

        unverified = json.loads(self.aggregate(self.write_raw(reference_record())).stdout)[0]
        self.assertEqual((unverified['validation'], unverified['guardsPassed'], unverified['protocolVersion']),
                         ('unverified', None, 'cpu-reference-v1'))
        self.assertNotIn('conditionPolicy', unverified)
        flagged = reference_record(guardsPassed=False, guardNotes=['not-foreground'],
                                   conditionPolicy={'version': 'observed-conditions-v1', 'mode': 'record-and-flag'})
        failed = json.loads(self.aggregate(self.write_raw(flagged)).stdout)[0]
        self.assertEqual((failed['validation'], failed['guardsPassed'], failed['conditionPolicy']),
                         ('failed', False, 'observed-conditions-v1'))
        for row in (unverified, failed):
            self.assertNotIn('evidence', row)
            self.assertNotIn('dispersion', row)

    def test_reference_sessions_stay_preview_only(self):
        self.assert_rejected('preview-only', self.write_raw(reference_record()), '--update', '--allow-legacy')

    def test_update_requires_a_migrated_canonical_file(self):
        current = load(self.canonical)
        unlabelled = copy.deepcopy(current)
        del next(r for r in unlabelled['rows'] if r['provenance'] == 'measured')['validation']
        for name, envelope in {'1.0': as_1_0(current), 'measured-without-validation': unlabelled}.items():
            with self.subTest(case=name):
                self.canonical.write_text(dump(envelope), encoding='utf-8')
                self.assert_rejected('migrate_contract_1_1.mjs', self.write_raw(legacy_record()), '--update',
                                     '--allow-legacy')

    def test_refuses_to_emit_verified(self):
        data = load(self.canonical)
        data['rows'].append(verified_row())
        data['rowCount'] += 1
        self.canonical.write_text(dump(data), encoding='utf-8')
        self.assertEqual(self.node('validate_data.mjs').returncode, 0, 'the verified fixture row is contract-valid')
        self.assert_rejected('refusing to emit validation=verified', self.write_raw(legacy_record()), '--update',
                             '--allow-legacy')


class ValidatorKeywordTests(unittest.TestCase):
    def run_cases(self, schema, values):
        script = ("import {validateSchema} from './scripts/lib/publication.mjs';"
                  f"const s = {schema};"
                  f"console.log(JSON.stringify({json.dumps(values)}.map((v) => validateSchema(v, s).length)));")
        result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True,
                                cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_new_keywords(self):
        self.assertEqual(self.run_cases("{const: 'a'}", ['a', 'b', None]), [0, 1, 1])
        self.assertEqual(self.run_cases("{const: {x: [1]}}", [{'x': [1]}, {'x': [1, 2]}, {'x': [1], 'y': 0}]),
                         [0, 1, 1])
        self.assertEqual(self.run_cases("{type: 'string', pattern: '^[a-f]{2}$'}", ['ab', 'abc', 'AB', 5]),
                         [0, 1, 1, 1])
        self.assertEqual(self.run_cases("{minLength: 2}", ['ab', 'a', '\U0001f600', 3]), [0, 1, 1, 0])
        self.assertEqual(self.run_cases("{minItems: 1}", [[1], [], 'x']), [0, 1, 0])
        self.assertEqual(self.run_cases("{allOf: [{type: 'integer'}, {minimum: 2}]}", [2, 1, 2.5]), [0, 1, 1])
        self.assertEqual(self.run_cases(
            "{if: {required: ['a']}, then: {required: ['b']}, else: {required: ['c']}}",
            [{'a': 1, 'b': 1}, {'a': 1}, {'c': 1}, {}]), [0, 1, 0, 1])
        self.assertEqual(self.run_cases("{type: 'object', properties: {x: false}}", [{}, {'x': None}]), [0, 1])

    def test_malformed_new_keywords_fail_closed(self):
        for schema, message in [
            ("{then: {}}", 'then/else without if'),
            ("{pattern: '('}", 'invalid pattern'),
            ("{pattern: 1}", 'pattern at # must be a string'),
            ("{minItems: -1}", 'minItems at # must be a non-negative integer'),
            ("{allOf: []}", 'allOf at # must be a non-empty array'),
            ("{if: {oneOf: []}}", 'unsupported schema keyword oneOf at #/if'),
        ]:
            with self.subTest(schema=schema):
                result = subprocess.run(['node', '--input-type=module', '-e',
                                         "import {validateSchema} from './scripts/lib/publication.mjs';"
                                         f"validateSchema(1, {schema});"], capture_output=True, text=True, cwd=ROOT)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)


if __name__ == '__main__':
    unittest.main()
