"""Contract 1.2 tests: build.runtimes, the fixed engineVersion/bindingVersion forms, consumer
fixtures, and the producer's mapping from a session's runtimeVersions to the row build.

Like test_contract_publication.py, every test runs against a temporary copy of scripts/ +
data/, so the checked-in data/benchmarks.json is never modified.
"""
import copy
import json
from pathlib import Path
import subprocess
import unittest

from test_contract_publication import Workspace, envelope_with, load
from test_publication import reference_record

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/contract-1.2'
LLAMA_VERSIONS = {'llamaCpp': {'buildNumber': 11385, 'commit': 'bf9a0cc'}, 'llamaRn': '0.13.0-rc.7',
                  'litertLm': None, 'litertNpuRuntimeQualcomm': None, 'qairt': None, 'genie': None}
LITERT_NPU_VERSIONS = {'llamaCpp': None, 'llamaRn': None, 'litertLm': '0.18.0',
                       'litertNpuRuntimeQualcomm': '2.3.0', 'qairt': '2.50.0.260828221209', 'genie': None}


def fixture_row(name):
    return copy.deepcopy(load(FIXTURES / name)['rows'][0])


class Contract12Tests(Workspace):
    def module(self, expression):
        """Evaluate a JS expression against scripts/lib/publication.mjs; returns its JSON value."""
        script = ("import * as p from './scripts/lib/publication.mjs';"
                  f"let out; try {{ out = {{ok: ({expression}) ?? null}}; }} catch (e) {{ out = {{error: String(e.message)}}; }}"
                  "console.log(JSON.stringify(out));")
        result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True,
                                cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def build_for(self, engine, versions):
        return self.module(f'p.buildFromSession({json.dumps({"engine": engine, "runtimeVersions": versions})})')

    def test_consumer_fixtures(self):
        index = load(FIXTURES / 'index.json')
        listed = {entry['file'] for entry in index['fixtures']}
        self.assertEqual(listed, {p.name for p in FIXTURES.glob('*.json')} - {'index.json'})
        for entry in index['fixtures']:
            with self.subTest(fixture=entry['file']):
                self.assertIsInstance(entry['producerMustNotEmit'], bool)
                self.assertTrue(entry['consumerExpectation'])
                result = self.node('validate_data.mjs', FIXTURES / entry['file'])
                self.assertEqual(result.returncode, 1 if entry['producerMustNotEmit'] else 0, result.stderr)
        accepted = [load(FIXTURES / e['file']) for e in index['fixtures'] if not e['producerMustNotEmit']]
        self.assertEqual({e['schemaVersion'] for e in accepted}, {'1.2'})

    def test_runtime_rejections(self):
        llama = fixture_row('llama-cpp-runtimes.json')
        litert = fixture_row('litert-npu-runtimes.json')
        build = lambda row, **changes: {**row, 'build': {**row['build'], **changes}}
        cases = {
            # name, version and shape of each runtime
            'unknown-name': (build(llama, runtimes=[{'name': 'ggml', 'version': '0.9.4'}]), 'is not one of', True),
            'name-case': (build(llama, runtimes=[{'name': 'Llama.cpp', 'version': 'b11385'}]), 'is not one of', True),
            'free-text-version': (build(litert, runtimes=[{'name': 'LiteRT-LM', 'version': '0.18.0 (Maven)'}]),
                                  'does not match', True),
            'v-prefix': (build(litert, runtimes=[{'name': 'LiteRT-LM', 'version': 'v0.18.0'}]), 'does not match', True),
            'path-version': (build(litert, runtimes=[{'name': 'QAIRT', 'version': '/opt/qairt/2.50'}]),
                             'does not match', True),
            'numeric-version': (build(litert, runtimes=[{'name': 'LiteRT-LM', 'version': 18}]), 'expected string',
                                True),
            'missing-version': (build(litert, runtimes=[{'name': 'LiteRT-LM'}]), 'missing required version', True),
            'extra-key': (build(litert, runtimes=[{'name': 'LiteRT-LM', 'version': '0.18.0', 'path': 'x'}]),
                          'path: not allowed', True),
            'empty-list': (build(litert, runtimes=[]), 'fewer than 1', True),
            # one entry per name: identical entries fail the schema, differing ones only the validator
            'same-entry-twice': (build(llama, runtimes=[{'name': 'llama.cpp', 'version': 'b11385'}] * 2),
                                 'items are not unique', True),
            'two-versions-one-name': (build(llama, runtimes=[{'name': 'llama.cpp', 'version': 'b11385'},
                                                             {'name': 'llama.cpp', 'version': 'b11386'}]),
                                      'more than one entry for llama.cpp', False),
            # with runtimes, engineVersion/bindingVersion take their fixed forms
            'engine-no-b': (build(llama, engineVersion='llama.cpp 11385 (bf9a0cc)'), 'engineVersion', True),
            'engine-long-commit': (build(llama, engineVersion='llama.cpp b11385 (bf9a0cc0123)'), 'engineVersion',
                                   True),
            'engine-free-text': (build(litert, engineVersion='LiteRT-LM 0.18.0 from Maven'), 'engineVersion', True),
            'binding-free-text': (build(llama, bindingVersion='llama.rn latest'), 'bindingVersion', True),
        }
        for name, (bad, reason, schema_alone) in cases.items():
            with self.subTest(case=name):
                self.assert_invalid(envelope_with(bad, schemaVersion='1.2'), reason)
                errors = self.schema_errors(bad)
                if schema_alone:
                    self.assertTrue(errors, f'the schema alone should reject {name}')
                else:
                    self.assertEqual(errors, [], f'{name} is a validator-only invariant')

    def test_runtimes_need_1_2_and_stay_measured_only(self):
        llama = fixture_row('llama-cpp-runtimes.json')
        for version in ('1.0', '1.1'):
            with self.subTest(schemaVersion=version):
                self.assert_invalid(envelope_with(llama, schemaVersion=version), 'build.runtimes needs schemaVersion 1.2')
        vendor = load(ROOT / 'tests/fixtures/contract-1.1/vendor.json')['rows'][0]
        self.assert_invalid(envelope_with({**vendor, 'build': {'runtimes': llama['build']['runtimes']}},
                                          schemaVersion='1.2'), 'build is only valid on provenance=measured rows')
        # Without runtimes, engineVersion stays free-form, so 1.1 rows keep validating unchanged in 1.2.
        loose = {**llama, 'build': {'engineVersion': 'llama.cpp b0000-fixture'}}
        self.assert_valid(envelope_with(loose, schemaVersion='1.2'))
        self.assert_valid(envelope_with(loose, schemaVersion='1.1'))

    def test_accepted_runtime_versions(self):
        litert = fixture_row('litert-npu-runtimes.json')
        for version in ('b11385', 'b1', '0.13.0-rc.7', '0.18.0', '2.3.0', '2.50.0.260828221209', '1.19.0', '1.0'):
            with self.subTest(version=version):
                good = {**litert, 'build': {**litert['build'], 'runtimes': [{'name': 'QAIRT', 'version': version}]}}
                self.assert_valid(envelope_with(good, schemaVersion='1.2'))

    def test_producer_versions_pass_the_consumer_token_check(self):
        # The website drops a runtime entry whose version fails this check; xebench must never emit one.
        import re
        token = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$')
        schema = load(ROOT / 'data/benchmarks.schema.json')
        producer = re.compile(schema['$defs']['runtime']['properties']['version']['pattern'])
        samples = ['b11385', 'b1', '0.13.0-rc.7', '0.18.0', '2.3.0', '2.50.0.260828221209', '1.0', '10.2.3-beta.1',
                   '1.2.3-rc.1.2.3', 'b9999999', '99999.999999999999.1.2-ABCDEFGHIJKLMNOP.abc']
        for version in samples:
            if producer.fullmatch(version):
                self.assertRegex(version, token)
        for entry in load(FIXTURES / 'index.json')['fixtures']:
            if entry['producerMustNotEmit']:
                continue
            for row in [v for v in load(FIXTURES / entry['file']).values() if isinstance(v, list)][0]:
                for runtime in row.get('build', {}).get('runtimes', []):
                    with self.subTest(fixture=entry['file'], version=runtime['version']):
                        self.assertRegex(runtime['version'], token)

    def test_build_from_session_llama_cpp(self):
        self.assertEqual(self.build_for('llama.cpp', LLAMA_VERSIONS), {'ok': {
            'engineVersion': 'llama.cpp b11385 (bf9a0cc)', 'bindingVersion': 'llama.rn 0.13.0-rc.7',
            'runtimes': [{'name': 'llama.cpp', 'version': 'b11385'}, {'name': 'llama.rn', 'version': '0.13.0-rc.7'}]}})
        # A full 40-hex commit is shortened to 7 for engineVersion.
        full = {**LLAMA_VERSIONS, 'llamaCpp': {'buildNumber': 11385, 'commit': 'bf9a0cc' + 'a' * 33}}
        self.assertEqual(self.build_for('llama.cpp', full)['ok']['engineVersion'], 'llama.cpp b11385 (bf9a0cc)')

    def test_build_from_session_litert_and_genie(self):
        self.assertEqual(self.build_for('litert-lm', LITERT_NPU_VERSIONS), {'ok': {
            'engineVersion': 'LiteRT-LM 0.18.0',
            'runtimes': [{'name': 'LiteRT-LM', 'version': '0.18.0'},
                         {'name': 'litert-npu-runtime-qualcomm', 'version': '2.3.0'},
                         {'name': 'QAIRT', 'version': '2.50.0.260828221209'}]}})
        cpu = {**LITERT_NPU_VERSIONS, 'litertNpuRuntimeQualcomm': None, 'qairt': None}
        self.assertEqual(self.build_for('litert-lm', cpu)['ok']['runtimes'], [{'name': 'LiteRT-LM', 'version': '0.18.0'}])
        genie = {key: None for key in LLAMA_VERSIONS} | {'genie': '1.19.0'}
        self.assertEqual(self.build_for('genie-x', genie),
                         {'ok': {'engineVersion': 'Genie 1.19.0', 'runtimes': [{'name': 'Genie', 'version': '1.19.0'}]}})

    def test_build_from_session_never_invents(self):
        # No runtimeVersions (older apps) or nothing reported: no build at all.
        self.assertEqual(self.module("p.buildFromSession({engine: 'llama.cpp'})"), {'ok': None})
        self.assertEqual(self.build_for('llama.cpp', None), {'ok': None})
        self.assertEqual(self.build_for('llama.cpp', {key: None for key in LLAMA_VERSIONS}), {'ok': None})
        # The engine's own version unknown: runtimes only, no engineVersion; no binding off llama.cpp.
        partial = {**LLAMA_VERSIONS, 'llamaCpp': None}
        self.assertEqual(self.build_for('llama.cpp', partial), {'ok': {
            'bindingVersion': 'llama.rn 0.13.0-rc.7', 'runtimes': [{'name': 'llama.rn', 'version': '0.13.0-rc.7'}]}})
        self.assertNotIn('bindingVersion', self.build_for('litert-lm', {**LITERT_NPU_VERSIONS, 'llamaRn': '0.13.0'})['ok'])

    def test_build_from_session_fails_closed(self):
        bad = {
            'not-object': [],
            'unknown-key': {**LLAMA_VERSIONS, 'ggml': '0.9.4'},
            'string-build-number': {**LLAMA_VERSIONS, 'llamaCpp': {'buildNumber': '11385', 'commit': 'bf9a0cc'}},
            'negative-build-number': {**LLAMA_VERSIONS, 'llamaCpp': {'buildNumber': -1, 'commit': 'bf9a0cc'}},
            'short-commit': {**LLAMA_VERSIONS, 'llamaCpp': {'buildNumber': 11385, 'commit': 'bf9a0c'}},
            'uppercase-commit': {**LLAMA_VERSIONS, 'llamaCpp': {'buildNumber': 11385, 'commit': 'BF9A0CC'}},
            'free-text-version': {**LITERT_NPU_VERSIONS, 'litertLm': 'latest'},
            'b-version-off-llama': {**LITERT_NPU_VERSIONS, 'qairt': 'b11385'},
        }
        for name, versions in bad.items():
            with self.subTest(case=name):
                self.assertIn('runtimeVersions', self.build_for('llama.cpp', versions).get('error', ''))

    def test_contract_version_for(self):
        self.assertEqual(self.module("p.contractVersionFor([{build: {runtimes: [{name: 'QAIRT', version: '2.50.0'}]}}])"),
                         {'ok': '1.2'})
        self.assertEqual(self.module("p.contractVersionFor([{build: {engineVersion: 'x'}}, {}])"), {'ok': '1.1'})
        # Never below what the canonical file already declares.
        self.assertEqual(self.module("p.contractVersionFor([{}], '1.2')"), {'ok': '1.2'})

    def test_a_1_2_canonical_file_needs_no_1_1_migration(self):
        envelope = load(FIXTURES / 'v1.1-rows-in-1.2.json')
        self.assertEqual(self.module(f'p.migrationNeeds({json.dumps(envelope)})'), {'ok': []})
        migrated = self.module(f'p.migrateTo11({json.dumps(envelope)})')['ok']
        self.assertEqual((migrated['touched'], migrated['changed']), (0, False))

    def test_aggregate_preview_fills_build_from_runtime_versions(self):
        path = self.root / 'session.jsonl'
        path.write_text(json.dumps(reference_record(runtimeVersions=LLAMA_VERSIONS)) + '\n', encoding='utf-8')
        result = self.node('aggregate.mjs', path)
        self.assertEqual(result.returncode, 0, result.stderr)
        [row] = json.loads(result.stdout)
        self.assertEqual(row['build'], {
            'engineVersion': 'llama.cpp b11385 (bf9a0cc)', 'bindingVersion': 'llama.rn 0.13.0-rc.7',
            'runtimes': [{'name': 'llama.cpp', 'version': 'b11385'}, {'name': 'llama.rn', 'version': '0.13.0-rc.7'}]})
        # Sessions without runtimeVersions keep producing rows without build.
        path.write_text(json.dumps(reference_record()) + '\n', encoding='utf-8')
        [row] = json.loads(self.node('aggregate.mjs', path).stdout)
        self.assertNotIn('build', row)
        # A malformed runtimeVersions rejects the run.
        path.write_text(json.dumps(reference_record(runtimeVersions={'llamaRn': 'latest'})) + '\n', encoding='utf-8')
        result = self.node('aggregate.mjs', path)
        self.assertEqual(result.returncode, 1)
        self.assertIn('runtimeVersions.llamaRn is not a version', result.stderr)


if __name__ == '__main__':
    unittest.main()
