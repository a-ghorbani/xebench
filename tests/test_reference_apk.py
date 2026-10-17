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
        required_dex = apk_check.COMMON_REQUIRED_DEX + spec.get('required_dex', [])
        apk.writestr('classes.dex', b'dex\n' + b''.join(required_dex) + dex)


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

    def test_flavors_require_their_bridges(self):
        for flavor, spec in apk_check.FLAVORS.items():
            required = apk_check.COMMON_REQUIRED_DEX + spec.get('required_dex', [])
            for descriptor in required:
                with self.subTest(flavor=flavor, descriptor=descriptor), \
                        tempfile.TemporaryDirectory() as root:
                    path = Path(root) / 'fixture.apk'
                    with ZipFile(path, 'w') as apk:
                        for name in spec['required']:
                            apk.writestr(name, b'fixture')
                        kept = [d for d in required if d != descriptor]
                        apk.writestr('classes.dex', b'dex\n' + b''.join(kept))
                    with self.assertRaisesRegex(ValueError, 'missing classes'):
                        apk_check.check_apk(path, flavor)

    def test_reference_allows_only_known_native_libraries(self):
        core = 'lib/arm64-v8a/libreact_codegen_safeareacontext.so'
        unknown = 'lib/arm64-v8a/libomp.so'
        self.check('reference', extra=[core])
        with self.assertRaisesRegex(ValueError, 'unexpected lib/arm64-v8a/libomp.so'):
            self.check('reference', extra=[unknown])
        for flavor in ('experimental', 'geniex'):
            with self.subTest(flavor=flavor):
                self.check(flavor, extra=[core, unknown])

    def test_reference_rejects_native_code_outside_the_allowlist_by_content(self):
        with tempfile.TemporaryDirectory() as root:
            for name in ('assets/libggml-cpu.so', 'assets/models/htp-skel', 'lib/arm64-v8a/libomp.so'):
                with self.subTest(name=name):
                    path = Path(root) / 'fixture.apk'
                    write_apk(path, 'reference')
                    with ZipFile(path, 'a') as apk:
                        apk.writestr(name, b'\x7fELF' + b'\0' * 60)
                    with self.assertRaisesRegex(ValueError, 'unexpected ' + name):
                        apk_check.check_apk(path, 'reference')
            # A non-ELF asset that is not named .so is fine.
            path = Path(root) / 'fixture.apk'
            write_apk(path, 'reference', extra=['assets/configs/cpu-reference-v1.json'])
            apk_check.check_apk(path, 'reference')

    def test_reference_rejects_qualcomm_and_litert_core_classes(self):
        for descriptor in (b'Lcom/qualcomm/qti/QnnDelegate;', b'Lcom/google/ai/edge/litert/CompiledModel;'):
            with self.subTest(descriptor=descriptor):
                with self.assertRaisesRegex(ValueError, 'unexpected classes'):
                    self.check('reference', dex=descriptor)


if __name__ == '__main__':
    unittest.main()
