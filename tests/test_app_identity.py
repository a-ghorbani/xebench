import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    'capture_identity', Path(__file__).resolve().parents[1] / 'scripts/capture_app_run.py')
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)

BASE = '/data/app/private-install/com.xebenchapp-private/base.apk'
SPLIT = '/data/app/private-install/com.xebenchapp-private/split_config.arm64_v8a.apk'


class AppIdentityTests(unittest.TestCase):
    def test_base_and_splits_have_sorted_fingerprints_without_install_paths(self):
        def command(*args):
            if args == ('shell', 'pm', 'path', capture.APP):
                return f'package:{SPLIT}\npackage:{BASE}'
            self.assertEqual(args, ('shell', 'sha256sum', BASE, SPLIT))
            return f'{"a" * 64}  {BASE}\n{"b" * 64}  {SPLIT}'

        result = capture.installed_apks(command)
        self.assertEqual(result, {'status': 'recorded', 'apks': [
            {'name': 'base.apk', 'sha256': 'a' * 64},
            {'name': 'split_config.arm64_v8a.apk', 'sha256': 'b' * 64},
        ]})
        self.assertNotIn('private', json.dumps(result))

    def test_unsafe_missing_or_duplicate_paths_are_unavailable_without_hashing(self):
        for paths in ['', 'package:/data/app/a/base.apk;id', 'package:/data/app/../base.apk',
                      f'package:{BASE}\npackage:{BASE}', f'package:{SPLIT}',
                      f'package:{BASE}\npackage:/data/app/other/base.apk']:
            with self.subTest(paths=paths):
                calls = []
                def command(*args):
                    calls.append(args)
                    return paths
                self.assertEqual(capture.installed_apks(command)['status'], 'unavailable')
                self.assertEqual(len(calls), 1)

    def test_invalid_or_incomplete_checksum_output_is_unavailable(self):
        for hashes in ['', f'bad  {BASE}', f'{"a" * 64}  /wrong/base.apk',
                       f'{"a" * 64}  {BASE}\n{"b" * 64}  {BASE}']:
            with self.subTest(hashes=hashes):
                result = capture.installed_apks(lambda *args:
                    f'package:{BASE}' if args[1] == 'pm' else hashes)
                self.assertEqual(result['status'], 'unavailable')

    def test_adb_error_is_unknown_and_does_not_leak_command(self):
        def command(*args):
            raise subprocess.CalledProcessError(1, ['adb', 'private-serial'])
        result = capture.installed_apks(command)
        self.assertEqual(result['status'], 'unavailable')
        self.assertNotIn('private', json.dumps(result))

    def test_deadline_is_not_swallowed_as_missing_identity(self):
        def command(*args):
            raise TimeoutError('deadline')
        with self.assertRaises(TimeoutError):
            capture.installed_apks(command)

    def test_hash_timeout_and_cancellation_propagate(self):
        for failure in [subprocess.TimeoutExpired(['adb'], 15), capture.CaptureInterrupted(15)]:
            with self.subTest(failure=type(failure).__name__):
                def command(*args):
                    if args[1] == 'pm':
                        return f'package:{BASE}'
                    raise failure
                with self.assertRaises(type(failure)):
                    capture.installed_apks(command)

    def capture_fixture(self, root, after_digest='a', unavailable=False):
        hashes, calls = [], []
        def adb(*args):
            calls.append(args)
            if args[:3] == ('shell', 'pm', 'path'):
                return 'unsupported' if unavailable else f'package:{BASE}'
            if args[:2] == ('shell', 'sha256sum'):
                if hashes and after_digest == 'timeout':
                    raise TimeoutError('deadline')
                digest = 'a' if not hashes else after_digest
                hashes.append(digest)
                return f'{digest * 64}  {BASE}'
            if args[:2] == ('shell', 'pidof'):
                return '123'
            if args[:3] == ('logcat', '-b', 'events'):
                return ''
            if args[0] == 'logcat':
                record = {'schema': 'xebench-raw', 'engine': 'llama.cpp', 'backend': 'cpu',
                          'model': 'fixture', 'quant': 'fixture'}
                return '100 I ReactNativeJS: XEBENCH_RESULT ' + json.dumps(record) + \
                    '\n101 I ReactNativeJS: XEBENCH_DONE'
            return 'fixture'
        code = capture.capture_device('private-serial', 30, root, adb=adb)
        manifest = json.loads(next(Path(root).glob('*/manifest.json')).read_text())
        self.assertNotIn('private', json.dumps(manifest))
        self.assertEqual(calls[-1], ('shell', 'am', 'force-stop', capture.APP))
        return code, manifest

    def test_complete_capture_records_matching_before_and_after_identity(self):
        with tempfile.TemporaryDirectory() as root:
            code, manifest = self.capture_fixture(root)
            self.assertEqual(code, 0)
            self.assertEqual(manifest['appIdentity']['consistency'], 'matched')
            self.assertEqual(manifest['appIdentity']['before'], manifest['appIdentity']['after'])
            self.assertFalse(manifest['publicationReady'])

    def test_changed_binary_cannot_be_a_complete_capture(self):
        with tempfile.TemporaryDirectory() as root:
            code, manifest = self.capture_fixture(root, after_digest='b')
            self.assertEqual(code, 4)
            self.assertEqual(manifest['status'], 'app-changed')
            self.assertEqual(manifest['appIdentity']['consistency'], 'changed')
            self.assertEqual(len(manifest['records']), 1)

    def test_unavailable_identity_keeps_results_but_does_not_claim_a_match(self):
        with tempfile.TemporaryDirectory() as root:
            code, manifest = self.capture_fixture(root, unavailable=True)
            self.assertEqual(code, 0)
            self.assertEqual(manifest['appIdentity']['consistency'], 'unverified')
            self.assertEqual(manifest['appIdentity']['before']['status'], 'unavailable')
            self.assertFalse(manifest['publicationReady'])

    def test_post_run_hash_deadline_preserves_results_and_cleanup(self):
        with tempfile.TemporaryDirectory() as root:
            code, manifest = self.capture_fixture(root, after_digest='timeout')
            self.assertEqual(code, 3)
            self.assertEqual(manifest['status'], 'timeout')
            self.assertEqual(len(manifest['records']), 1)
            self.assertEqual(manifest['appIdentity']['consistency'], 'unverified')
            self.assertIsNone(manifest['appIdentity']['after'])


if __name__ == '__main__':
    unittest.main()
