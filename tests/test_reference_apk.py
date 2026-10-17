import importlib.util
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

SPEC = importlib.util.spec_from_file_location('apk', Path(__file__).resolve().parents[1] / 'scripts/check_reference_apk.py')
apk_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(apk_check)

# One concrete entry per forbidden pattern family, keyed by what it represents.
SAMPLES = {
    'llama-hexagon': 'lib/arm64-v8a/librnllama_jni_v8_2_dotprod_i8mm_hexagon_opencl.so',
    'llama-opencl': 'lib/arm64-v8a/librnllama_v8_2_opencl.so',
    'htp-asset': 'assets/ggml-hexagon/libggml-htp-v73.so',
    'qnn': 'lib/arm64-v8a/libQnnHtp.so',
    'qnn-skel': 'lib/arm64-v8a/libQnnHtpV73Skel.so',
    'qairt': 'lib/arm64-v8a/libQairtCore.so',
    'cdsprpc': 'lib/arm64-v8a/libcdsprpc.so',
    'litert': 'lib/arm64-v8a/liblitertlm_jni.so',
    'genie': 'lib/arm64-v8a/libGenie.so',
    'executorch': 'lib/arm64-v8a/libexecutorch.so',
    'executorch-helper': 'lib/arm64-v8a/libpthreadpool.so',
    'ggml-htp': 'lib/arm64-v8a/libggml-htp-v81.so',
    'ggml-opencl': 'lib/arm64-v8a/libggml-opencl.so',
}
FORBIDDEN = {
    'reference': set(SAMPLES),
    'experimental': {'genie'},
    # geniex-android bundles its own QNN and ggml Hexagon/OpenCL libraries.
    'geniex': {'llama-hexagon', 'llama-opencl', 'htp-asset', 'litert', 'executorch',
               'executorch-helper'},
}
DEX_SAMPLES = {
    'litert': b'Lcom/google/ai/edge/litertlm/Engine;',
    'litert-bridge': b'Lcom/xebenchapp/LiteRtBenchModule;',
    'geniex': b'Lcom/geniex/sdk/GenieXSdk;',
    'geniex-bridge': b'Lcom/xebenchapp/GenieXBenchModule;',
}
FORBIDDEN_DEX = {
    'reference': set(DEX_SAMPLES),
    'experimental': {'geniex', 'geniex-bridge'},
    'geniex': {'litert', 'litert-bridge'},
}


def write_apk(path, flavor, extra=(), dex=b'', drop=None, empty=None):
    spec = apk_check.FLAVORS[flavor]
    with ZipFile(path, 'w') as apk:
        for name in spec['required']:
            if name == drop:
                continue
            apk.writestr(name, b'' if name == empty else b'fixture')
        for name in set(extra) - spec['required']:
            apk.writestr(name, b'fixture')
        apk.writestr('classes.dex', b'dex\n' + b''.join(spec.get('required_dex', [])) + dex)


class ReferenceApkTests(unittest.TestCase):
    def check(self, flavor, **kwargs):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'fixture.apk'
            write_apk(path, flavor, **kwargs)
            apk_check.check_apk(path, flavor)

    def test_missing_and_empty_runtime_are_rejected(self):
        for flavor, spec in apk_check.FLAVORS.items():
            self.check(flavor)
            for name in spec['required']:
                for mode in ('drop', 'empty'):
                    with self.subTest(flavor=flavor, name=name, mode=mode):
                        with self.assertRaisesRegex(ValueError, 'missing'):
                            self.check(flavor, **{mode: name})

    def test_default_flavor_is_reference(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'fixture.apk'
            write_apk(path, 'reference', extra=[SAMPLES['litert']])
            with self.assertRaisesRegex(ValueError, 'reference APK: unexpected'):
                apk_check.check_apk(path)

    def test_flavor_specific_native_libraries(self):
        for flavor, forbidden in FORBIDDEN.items():
            for key, name in SAMPLES.items():
                with self.subTest(flavor=flavor, sample=key):
                    if key in forbidden:
                        with self.assertRaisesRegex(ValueError, 'unexpected'):
                            self.check(flavor, extra=[name])
                    else:
                        self.check(flavor, extra=[name])

    def test_flavor_specific_engine_classes(self):
        for flavor, forbidden in FORBIDDEN_DEX.items():
            for key, descriptor in DEX_SAMPLES.items():
                with self.subTest(flavor=flavor, sample=key):
                    if key in forbidden:
                        with self.assertRaisesRegex(ValueError, 'unexpected classes'):
                            self.check(flavor, dex=descriptor)
                    else:
                        self.check(flavor, dex=descriptor)

    def test_accelerator_flavors_require_their_bridges(self):
        for flavor in ('experimental', 'geniex'):
            with self.subTest(flavor=flavor), tempfile.TemporaryDirectory() as root:
                path = Path(root) / 'fixture.apk'
                with ZipFile(path, 'w') as apk:
                    for name in apk_check.FLAVORS[flavor]['required']:
                        apk.writestr(name, b'fixture')
                    apk.writestr('classes.dex', b'dex')
                with self.assertRaisesRegex(ValueError, 'missing classes'):
                    apk_check.check_apk(path, flavor)


if __name__ == '__main__':
    unittest.main()
