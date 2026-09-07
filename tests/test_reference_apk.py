import importlib.util
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

SPEC = importlib.util.spec_from_file_location('apk', Path(__file__).resolve().parents[1] / 'scripts/check_reference_apk.py')
apk_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(apk_check)


class ReferenceApkTests(unittest.TestCase):
    def test_missing_and_empty_runtime_are_rejected(self):
        for mode in ('complete', 'missing', 'empty'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as root:
                path = Path(root) / 'fixture.apk'
                with ZipFile(path, 'w') as apk:
                    for name in apk_check.REQUIRED:
                        if name.endswith('librnllama_jni_v8.so'):
                            if mode == 'missing':
                                continue
                            if mode == 'empty':
                                apk.writestr(name, b'')
                                continue
                        apk.writestr(name, b'fixture')
                if mode == 'complete':
                    apk_check.check_apk(path)
                else:
                    with self.assertRaises(ValueError):
                        apk_check.check_apk(path)
