import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('stage_model', ROOT / 'scripts/stage_model.py')
stage_model = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stage_model)

SERIAL = 'FAKE123'
FILE = 'Llama-3.2-1B-Instruct-Q4_K_M.gguf'
PAYLOAD = b'GGUF fixture bytes, not a real model\n' * 64

# A stand-in for adb: logs every invocation and maps device paths into a temp dir.
# FAKE_ADB_CORRUPT alters the pushed bytes, FAKE_ADB_PUSH_FAIL fails the push after
# writing a partial copy, FAKE_ADB_NO_SHA makes sha256sum unavailable,
# FAKE_ADB_NO_APP reports the app as not installed and FAKE_ADB_OFFLINE the device
# as missing.
FAKE_ADB = r'''#!{python}
import hashlib, json, os, shlex, shutil, sys
from pathlib import Path
root = Path(os.environ['FAKE_DEVICE_ROOT'])
with open(os.environ['FAKE_ADB_LOG'], 'a') as log:
    log.write(json.dumps(sys.argv[1:]) + '\n')
if sys.argv[1:3] != ['-s', os.environ['FAKE_ADB_SERIAL']]:
    sys.exit('fake adb: missing or wrong -s serial')
args = sys.argv[3:]
device = lambda path: root / path.lstrip('/')
if args[0] == 'get-state':
    if os.environ.get('FAKE_ADB_OFFLINE'):
        sys.exit("error: device '" + sys.argv[2] + "' not found")
    print('device')
    sys.exit(0)
if args[0] == 'push':
    target = device(args[2])
    if not target.parent.is_dir():
        sys.exit('adb: error: remote directory does not exist')
    shutil.copyfile(args[1], target)
    if os.environ.get('FAKE_ADB_PUSH_FAIL'):
        sys.exit('adb: error: failed to copy: connection reset')
    if os.environ.get('FAKE_ADB_CORRUPT'):
        with open(target, 'ab') as handle:
            handle.write(b'!')
    print('1 file pushed')
    sys.exit(0)
if args[0] != 'shell' or len(args) != 2:
    sys.exit('fake adb: unsupported invocation')
cmd = shlex.split(args[1])
if cmd[:2] == ['pm', 'path']:
    if os.environ.get('FAKE_ADB_NO_APP'):
        sys.exit(1)
    print('package:/data/app/~~x/' + cmd[2] + '/base.apk')
elif cmd[:2] == ['mkdir', '-p']:
    device(cmd[2]).mkdir(parents=True, exist_ok=True)
elif cmd[0] == 'sha256sum':
    if os.environ.get('FAKE_ADB_NO_SHA'):
        print('/system/bin/sh: sha256sum: inaccessible or not found', file=sys.stderr)
        sys.exit(127)
    path = device(cmd[1])
    if not path.is_file():
        print('sha256sum: ' + cmd[1] + ': No such file or directory', file=sys.stderr)
        sys.exit(1)
    print(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + cmd[1])
elif cmd[:2] == ['mv', '-f']:
    os.replace(device(cmd[2]), device(cmd[3]))
    if os.environ.get('FAKE_ADB_CORRUPT_AFTER_MV'):
        with open(device(cmd[3]), 'ab') as handle:
            handle.write(b'!')
elif cmd[:2] == ['rm', '-f']:
    device(cmd[2]).unlink(missing_ok=True)
else:
    sys.exit('fake adb: unsupported shell command ' + args[1])
'''


class StageModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.device = base / 'device'
        self.device.mkdir()
        self.log = base / 'adb.log'
        self.adb = base / 'adb'
        self.adb.write_text(FAKE_ADB.replace('{python}', sys.executable))
        self.adb.chmod(self.adb.stat().st_mode | stat.S_IXUSR)
        self.model_file = base / 'models' / FILE
        self.model_file.parent.mkdir()
        self.model_file.write_bytes(PAYLOAD)
        self.manifest = self.write_manifest()
        env = mock.patch.dict(os.environ, {'FAKE_DEVICE_ROOT': str(self.device), 'FAKE_ADB_LOG': str(self.log),
                                           'FAKE_ADB_SERIAL': SERIAL})
        env.start()
        self.addCleanup(env.stop)

    def write_manifest(self, **overrides):
        entry = {
            'id': 'fixture', 'repo': 'example/Fixture-GGUF', 'revision': 'a' * 40, 'file': FILE,
            'size': len(PAYLOAD), 'sha256': hashlib.sha256(PAYLOAD).hexdigest(),
            'license': 'Llama 3.2 Community License', 'gated': False, **overrides,
        }
        path = Path(self.tmp.name) / 'manifest.json'
        path.write_text(json.dumps({'schemaVersion': 1, 'models': [entry]}))
        return path

    def run_main(self, *extra, file=None):
        argv = [str(file or self.model_file), '--serial', SERIAL, '--manifest', str(self.manifest),
                '--adb', str(self.adb), *extra]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = stage_model.main(argv)
        return code, out.getvalue(), err.getvalue()

    def adb_calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def pushes(self):
        return [call for call in self.adb_calls() if call[2] == 'push']

    def staged(self, suffix=''):
        return self.device / stage_model.DEVICE_DIR.lstrip('/') / (FILE + suffix)

    def test_shell_words_are_quoted_for_the_device_shell(self):
        adb = stage_model.Adb(SERIAL, str(self.adb))
        adb.shell('sha256sum', "/sdcard/a b;rm -rf $HOME'x", check=False)
        [call] = self.adb_calls()
        self.assertEqual(call[2:], ['shell', "sha256sum '/sdcard/a b;rm -rf $HOME'\"'\"'x'"])

    def test_mismatch_after_rename_does_not_leave_the_file_under_its_final_name(self):
        with mock.patch.dict(os.environ, {'FAKE_ADB_CORRUPT_AFTER_MV': '1'}):
            code, _, err = self.run_main()
        self.assertEqual(code, 1, err)
        self.assertIn('after the rename', err)
        self.assertFalse(self.staged().exists())
        self.assertFalse(self.staged('.partial').exists())

    def test_stages_verified_file_in_app_external_files_dir(self):
        code, out, err = self.run_main()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.staged().read_bytes(), PAYLOAD)
        self.assertFalse(self.staged('.partial').exists())
        self.assertEqual(stage_model.DEVICE_DIR, '/sdcard/Android/data/com.xebenchapp/files')
        self.assertTrue(all(call[:2] == ['-s', SERIAL] for call in self.adb_calls()))
        [push] = self.pushes()
        self.assertEqual(push[4], f'{stage_model.DEVICE_DIR}/{FILE}.partial')
        self.assertIn('matches the manifest', out)

    def test_local_sha256_mismatch_is_refused_before_any_adb_call(self):
        self.manifest = self.write_manifest(sha256='0' * 64)
        code, _, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn('does not match the manifest', err)
        self.assertEqual(self.adb_calls(), [])
        self.assertFalse(self.staged().exists())

    def test_local_size_mismatch_is_refused(self):
        self.manifest = self.write_manifest(size=len(PAYLOAD) + 1)
        code, _, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn('size', err)
        self.assertEqual(self.adb_calls(), [])

    def test_wrong_basename_is_refused(self):
        renamed = self.model_file.with_name('Llama-3.2-1B-Instruct-Q4_K_M-copy.gguf')
        renamed.write_bytes(PAYLOAD)
        code, _, err = self.run_main(file=renamed)
        self.assertEqual(code, 1)
        self.assertIn('expected the file name', err)
        self.assertEqual(self.adb_calls(), [])

    def test_missing_file_prints_pinned_download_command_and_never_calls_adb(self):
        self.model_file.unlink()
        code, _, err = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn('not found', err)
        self.assertIn(f'hf download example/Fixture-GGUF {FILE} --revision {"a" * 40}', err)
        self.assertIn(f'https://huggingface.co/example/Fixture-GGUF/resolve/{"a" * 40}/{FILE}', err)
        self.assertEqual(self.adb_calls(), [])

    def test_device_side_mismatch_is_refused_and_partial_removed(self):
        with mock.patch.dict(os.environ, {'FAKE_ADB_CORRUPT': '1'}):
            code, _, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn('device-side sha256', err)
        self.assertFalse(self.staged().exists())
        self.assertFalse(self.staged('.partial').exists())
        self.assertFalse(any(call[3].startswith('mv') for call in self.adb_calls() if call[2] == 'shell'))

    def test_device_side_hash_unavailable_is_refused(self):
        with mock.patch.dict(os.environ, {'FAKE_ADB_NO_SHA': '1'}):
            code, _, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn('unavailable', err)
        self.assertFalse(self.staged().exists())
        self.assertFalse(self.staged('.partial').exists())

    def test_existing_verified_copy_is_not_pushed_again(self):
        self.assertEqual(self.run_main()[0], 0)
        self.log.unlink()
        code, out, err = self.run_main()
        self.assertEqual(code, 0, err)
        self.assertIn('already present and verified', out)
        self.assertEqual(self.pushes(), [])

    def test_stale_device_copy_is_replaced(self):
        self.staged().parent.mkdir(parents=True)
        self.staged().write_bytes(b'stale')
        code, _, err = self.run_main()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.staged().read_bytes(), PAYLOAD)

    def test_device_mismatch_leaves_stale_copy_untouched_and_says_so(self):
        self.staged().parent.mkdir(parents=True)
        self.staged().write_bytes(b'stale')
        with mock.patch.dict(os.environ, {'FAKE_ADB_CORRUPT': '1'}):
            code, _, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertEqual(self.staged().read_bytes(), b'stale')
        self.assertFalse(self.staged('.partial').exists())
        self.assertIn('left in place', err)

    def test_failed_push_is_refused_and_partial_removed(self):
        with mock.patch.dict(os.environ, {'FAKE_ADB_PUSH_FAIL': '1'}):
            code, _, err = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn('connection reset', err)
        self.assertFalse(self.staged().exists())
        self.assertFalse(self.staged('.partial').exists())

    def test_missing_adb_is_reported(self):
        self.adb = Path(self.tmp.name) / 'no-such-adb'
        code, _, err = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn('cannot run', err)

    def test_unavailable_device_is_reported_as_such(self):
        with mock.patch.dict(os.environ, {'FAKE_ADB_OFFLINE': '1'}):
            code, _, err = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn('not available', err)
        self.assertIn('not found', err)
        self.assertNotIn('not installed', err)
        self.assertEqual(self.pushes(), [])

    def test_app_not_installed_is_refused(self):
        with mock.patch.dict(os.environ, {'FAKE_ADB_NO_APP': '1'}):
            code, _, err = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn('not installed', err)
        self.assertEqual(self.pushes(), [])

    def test_invalid_manifest_is_refused(self):
        self.manifest = self.write_manifest(revision='main')
        code, _, err = self.run_main()
        self.assertEqual(code, 2)
        self.assertIn('invalid', err)
        self.assertEqual(self.adb_calls(), [])

    def test_serial_is_required(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = stage_model.main([str(self.model_file), '--manifest', str(self.manifest), '--adb', str(self.adb)])
        self.assertEqual(code, 2)
        self.assertEqual(self.adb_calls(), [])

    def test_print_download_needs_no_device(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = stage_model.main(['--print-download', '--manifest', str(self.manifest)])
        self.assertEqual(code, 0)
        self.assertIn('--revision ' + 'a' * 40, out.getvalue())
        self.assertIn('accept', out.getvalue())


class CommittedManifestTests(unittest.TestCase):
    def test_reference_model_is_pinned_and_matches_the_bundled_config(self):
        [model] = stage_model.load_manifest(stage_model.MANIFEST)
        self.assertEqual(model['repo'], 'bartowski/Llama-3.2-1B-Instruct-GGUF')
        self.assertEqual(model['revision'], '067b946cf014b7c697f3654f621d577a3e3afd1c')
        self.assertEqual(model['sha256'], '6f85a640a97cf2bf5b8e764087b1e83da0fdb51d7c9fab7d0fece9385611df83')
        self.assertEqual(model['size'], 807694464)
        self.assertEqual(model['license'], 'Llama 3.2 Community License')
        config = json.loads((ROOT / 'xebenchapp/configs/cpu-reference-v1.json').read_text())
        self.assertEqual({run['modelFile'] for run in config['runs']}, {model['file']})
        for run in config['runs']:
            if 'modelSha256' in run:
                self.assertEqual(run['modelSha256'], model['sha256'])

    def test_experimental_gemma_gguf_is_pinned_and_matches_the_accelerator_configs(self):
        manifest = ROOT / 'configs/models/experimental-models.json'
        models = {model['file']: model for model in stage_model.load_manifest(manifest)}
        model = models['gemma-3-1b-it-q4_0.gguf']
        q40 = models['Llama-3.2-1B-Instruct-Q4_0.gguf']
        self.assertEqual((q40['repo'], q40['revision'], q40['size'], q40['sha256']),
                         ('bartowski/Llama-3.2-1B-Instruct-GGUF', '067b946cf014b7c697f3654f621d577a3e3afd1c',
                          773025920, 'fa0390e7c043f89ae1847bd6682d748041a99d4ef3de0e0b27d33b6af97a8be8'))
        self.assertEqual((model['repo'], model['revision']),
                         ('google/gemma-3-1b-it-qat-q4_0-gguf', 'd1be121d36172a4b0b964657e2ee859d61138593'))
        self.assertEqual((model['size'], model['sha256']),
                         (1003541152, '95e5b8d891cd6a794f66c2a6fb59a41e9562b4660560b854274eceffb628b22a'))
        self.assertTrue(model['gated'])
        for name in ('f8-gemma3-accelerators-v1.json', 's23-gemma3-accelerators-v1.json'):
            config = json.loads((ROOT / 'xebenchapp/configs' / name).read_text())
            self.assertEqual({run['modelFile'] for run in config['runs'] if run['engine'] == 'llama.cpp'},
                             {model['file']})
        text = (ROOT / 'MODELS.md').read_text()
        for value in (model['repo'], model['revision'], model['file'], model['sha256'], str(model['size'])):
            self.assertIn(value, text)

    def test_models_md_documents_the_manifest_values(self):
        [model] = stage_model.load_manifest(stage_model.MANIFEST)
        text = (ROOT / 'MODELS.md').read_text()
        for value in (model['repo'], model['revision'], model['file'], model['sha256'], str(model['size'])):
            self.assertIn(value, text)
        self.assertIn(stage_model.DEVICE_DIR, text)
        self.assertNotIn('/data/local/tmp', text)


if __name__ == '__main__':
    unittest.main()
