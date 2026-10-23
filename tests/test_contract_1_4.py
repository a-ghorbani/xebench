"""Contract 1.4: ExecuTorch in build (data/benchmarks.schema.json, scripts/lib/publication.mjs).

Next to build.runtimes, a row may name the runtimes ExecuTorch and react-native-executorch, with
engineVersion "ExecuTorch <version>" and bindingVersion "react-native-executorch <version>". A file
declares 1.4 only when a row carries one of them. Every test runs against a temporary copy of
scripts/ + data/ (test_contract_publication.Workspace), so data/benchmarks.json is never modified.
"""
import copy
import json
from pathlib import Path
import subprocess

from test_contract_publication import Workspace, envelope_with, load

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/contract-1.4'
REASONS = {
    'executorch-runtimes-in-1.3.json': 'build.runtimes ExecuTorch needs schemaVersion 1.4',
    'binding-only-in-1.3.json': 'build.runtimes react-native-executorch needs schemaVersion 1.4',
    'unknown-runtime-name.json': 'is not one of',
    'engine-version-form.json': 'engineVersion',
}
NO_VERSIONS = {'llamaCpp': None, 'llamaRn': None, 'litertLm': None, 'litertNpuRuntimeQualcomm': None, 'qairt': None,
               'genie': None}
EXECUTORCH_VERSIONS = {**NO_VERSIONS, 'executorch': '1.4.1', 'reactNativeExecutorch': '0.10.7'}


def executorch_row():
    return copy.deepcopy(load(FIXTURES / 'executorch-runtimes.json')['rows'][0])


class Contract14Tests(Workspace):
    def module(self, expression):
        """Evaluate a JS expression against scripts/lib/publication.mjs; returns its JSON value."""
        script = ("import * as p from './scripts/lib/publication.mjs';"
                  f"let out; try {{ out = {{ok: ({expression}) ?? null}}; }} catch (e) {{ out = {{error: String(e.message)}}; }}"
                  "console.log(JSON.stringify(out));")
        result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True,
                                cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def build_for(self, engine, versions, backend='cpu'):
        session = {'engine': engine, 'backend': backend, 'runtimeVersions': versions}
        return self.module(f'p.buildFromSession({json.dumps(session)})')

    def test_consumer_fixtures(self):
        index = load(FIXTURES / 'index.json')
        self.assertEqual({entry['file'] for entry in index['fixtures']},
                         {path.name for path in FIXTURES.glob('*.json')} - {'index.json'})
        for entry in index['fixtures']:
            with self.subTest(fixture=entry['file']):
                self.assertTrue(entry['consumerExpectation'])
                result = self.node('validate_data.mjs', FIXTURES / entry['file'])
                self.assertEqual(result.returncode, 1 if entry['producerMustNotEmit'] else 0, result.stderr)
                if entry['producerMustNotEmit']:
                    self.assertIn(REASONS[entry['file']], result.stderr)
        accepted = [load(FIXTURES / e['file']) for e in index['fixtures'] if not e['producerMustNotEmit']]
        self.assertEqual({e['schemaVersion'] for e in accepted}, {'1.4'})

    def test_executorch_content_needs_1_4(self):
        row = executorch_row()
        build = row['build']
        for version in ('1.0', '1.1', '1.2', '1.3'):
            with self.subTest(schemaVersion=version):
                if version in ('1.0', '1.1'):
                    self.assert_invalid(envelope_with(row, schemaVersion=version), 'build.runtimes needs schemaVersion 1.2')
                else:
                    self.assert_invalid(envelope_with(row, schemaVersion=version),
                                        'build.runtimes ExecuTorch needs schemaVersion 1.4')
        # Each piece of 1.4 content alone is enough to need 1.4.
        pieces = {
            'engine-version': ({'engineVersion': 'ExecuTorch 1.4.1', 'runtimes': [{'name': 'QAIRT', 'version': '2.50.0'}]},
                               'build.engineVersion ExecuTorch 1.4.1 needs schemaVersion 1.4'),
            'binding-version': ({'bindingVersion': 'react-native-executorch 0.10.7',
                                 'runtimes': [{'name': 'QAIRT', 'version': '2.50.0'}]},
                                'build.bindingVersion react-native-executorch 0.10.7 needs schemaVersion 1.4'),
            'runtime-name': ({'runtimes': [{'name': 'ExecuTorch', 'version': '1.4.1'}]},
                             'build.runtimes ExecuTorch needs schemaVersion 1.4'),
        }
        for name, (piece, reason) in pieces.items():
            with self.subTest(piece=name):
                self.assert_invalid(envelope_with({**row, 'build': piece}, schemaVersion='1.3'), reason)
                self.assert_valid(envelope_with({**row, 'build': piece}, schemaVersion='1.4'))
        # Without runtimes, engineVersion and bindingVersion stay free text in every minor.
        loose = {**row, 'build': {'engineVersion': build['engineVersion'], 'bindingVersion': build['bindingVersion']}}
        self.assert_valid(envelope_with(loose, schemaVersion='1.3'))

    def test_the_schema_alone_refuses_1_4_content_in_a_1_3_file(self):
        # A consumer validating with the schema only (no publication.mjs) also refuses it.
        script = ("import {validateSchema} from './scripts/lib/publication.mjs';"
                  "import {readFileSync} from 'node:fs';"
                  "const s = JSON.parse(readFileSync('./data/benchmarks.schema.json', 'utf8'));"
                  f"const e = {json.dumps(envelope_with(executorch_row(), schemaVersion='1.3'))};"
                  "console.log(JSON.stringify([validateSchema(e, s), validateSchema({...e, schemaVersion: '1.4'}, s)]));")
        result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True,
                                cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        in_13, in_14 = json.loads(result.stdout)
        self.assertTrue(any('is not one of' in error for error in in_13), in_13)
        self.assertEqual(in_14, [])

    def test_runtime_rejections(self):
        row = executorch_row()
        build = lambda **changes: {**row, 'build': {**row['build'], **changes}}
        cases = {
            'lowercase-name': (build(runtimes=[{'name': 'executorch', 'version': '1.4.1'}]), 'is not one of'),
            'backend-as-runtime': (build(runtimes=[{'name': 'XNNPACK', 'version': '1.4.1'}]), 'is not one of'),
            'v-prefix': (build(runtimes=[{'name': 'ExecuTorch', 'version': 'v1.4.1'}]), 'does not match'),
            'engine-free-text': (build(engineVersion='ExecuTorch 1.4.1 (XNNPACK)'), 'engineVersion'),
            'engine-binding-name': (build(engineVersion='react-native-executorch 0.10.7'), 'engineVersion'),
            'binding-free-text': (build(bindingVersion='react-native-executorch latest'), 'bindingVersion'),
            'binding-engine-name': (build(bindingVersion='ExecuTorch 1.4.1'), 'bindingVersion'),
            'two-versions-one-name': (build(runtimes=[{'name': 'ExecuTorch', 'version': '1.4.1'},
                                                      {'name': 'ExecuTorch', 'version': '1.4.0'}]),
                                      'more than one entry for ExecuTorch'),
        }
        for name, (bad, reason) in cases.items():
            with self.subTest(case=name):
                self.assert_invalid(envelope_with(bad, schemaVersion='1.4'), reason)

    def test_build_from_session_executorch(self):
        self.assertEqual(self.build_for('executorch', EXECUTORCH_VERSIONS), {'ok': {
            'engineVersion': 'ExecuTorch 1.4.1', 'bindingVersion': 'react-native-executorch 0.10.7',
            'runtimes': [{'name': 'ExecuTorch', 'version': '1.4.1'},
                         {'name': 'react-native-executorch', 'version': '0.10.7'}]}})
        # Only what is reported appears; a session without the new keys (older apps) still maps.
        binding_only = {**EXECUTORCH_VERSIONS, 'executorch': None}
        self.assertEqual(self.build_for('executorch', binding_only), {'ok': {
            'bindingVersion': 'react-native-executorch 0.10.7',
            'runtimes': [{'name': 'react-native-executorch', 'version': '0.10.7'}]}})
        self.assertEqual(self.build_for('executorch', NO_VERSIONS), {'ok': None})
        llama = {**NO_VERSIONS, 'llamaCpp': {'buildNumber': 11385, 'commit': 'bf9a0cc'}, 'llamaRn': '0.13.0-rc.7'}
        self.assertEqual(self.build_for('llama.cpp', llama)['ok']['runtimes'],
                         [{'name': 'llama.cpp', 'version': 'b11385'}, {'name': 'llama.rn', 'version': '0.13.0-rc.7'}])

    def test_build_from_session_publishes_only_the_engines_runtimes(self):
        llama = {**NO_VERSIONS, 'llamaCpp': {'buildNumber': 11385, 'commit': 'bf9a0cc'}, 'llamaRn': '0.13.0-rc.7'}
        cases = {
            'executorch-on-llama': ('llama.cpp', 'cpu', {**llama, 'executorch': '1.4.1'}),
            'binding-on-litert': ('litert-lm', 'cpu', {**NO_VERSIONS, 'litertLm': '0.18.0',
                                                       'reactNativeExecutorch': '0.10.7'}),
            'llama-rn-on-executorch': ('executorch', 'cpu', {**EXECUTORCH_VERSIONS, 'llamaRn': '0.13.0-rc.7'}),
        }
        for name, (engine, backend, versions) in cases.items():
            with self.subTest(case=name):
                self.assertIn('does not run on', self.build_for(engine, versions, backend).get('error', ''))
        self.assertIn('is not a version',
                      self.build_for('executorch', {**EXECUTORCH_VERSIONS, 'executorch': '1.4.1 (XNNPACK)'})['error'])
        self.assertIn('unknown key', self.build_for('executorch', {**EXECUTORCH_VERSIONS, 'xnnpack': '1.4.1'})['error'])

    def test_contract_version_for(self):
        cases = [
            ("[{build: {runtimes: [{name: 'ExecuTorch', version: '1.4.1'}]}}]", None, '1.4'),
            ("[{build: {engineVersion: 'ExecuTorch 1.4.1', runtimes: [{name: 'QAIRT', version: '2.50.0'}]}}]", None, '1.4'),
            ("[{build: {runtimes: [{name: 'react-native-executorch', version: '0.10.7'}]}}, {dateBasis: 'measured'}]",
             None, '1.4'),
            ("[{dateBasis: 'measured'}]", None, '1.3'),
            ("[{build: {runtimes: [{name: 'llama.rn', version: '0.13.0'}]}}]", None, '1.2'),
            # Free-text engineVersion without runtimes is not 1.4 content.
            ("[{build: {engineVersion: 'ExecuTorch 1.4.1'}}]", None, '1.1'),
            ("[{}]", '1.4', '1.4'),
            ("[{build: {runtimes: [{name: 'ExecuTorch', version: '1.4.1'}]}}]", '1.3', '1.4'),
        ]
        for rows, floor, expected in cases:
            with self.subTest(rows=rows, floor=floor):
                floor_arg = f", '{floor}'" if floor else ''
                self.assertEqual(self.module(f'p.contractVersionFor({rows}{floor_arg})'), {'ok': expected})

    def test_a_1_4_canonical_file_needs_no_1_1_migration(self):
        envelope = load(FIXTURES / 'executorch-runtimes.json')
        self.assertEqual(self.module(f'p.migrationNeeds({json.dumps(envelope)})'), {'ok': []})

    def test_the_canonical_file_stays_below_1_4(self):
        # No ExecuTorch row with runtimes is published yet, so the canonical file keeps its version.
        data = load(ROOT / 'data/benchmarks.json')
        self.assertNotEqual(data['schemaVersion'], '1.4')
        self.assertEqual(self.module(f"p.contractVersionFor({json.dumps(data['rows'])})"), {'ok': data['schemaVersion']})


if __name__ == '__main__':
    import unittest
    unittest.main()
