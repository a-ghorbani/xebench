import hashlib
import importlib.util
import json
import re
from pathlib import Path
import tempfile
import unittest
from unittest import mock
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
    'executorch-jsi': 'lib/arm64-v8a/libreact-native-executorch.so',
    'executorch-xnnpack': 'lib/arm64-v8a/libxnnpack_executorch_backend.so',
    'executorch-vulkan': 'lib/arm64-v8a/libvulkan_executorch_backend.so',
    'executorch-qnn': 'lib/arm64-v8a/libqnn_executorch_backend.so',
    'worklets': 'lib/arm64-v8a/libworklets.so',
    'ggml-htp': 'lib/arm64-v8a/libggml-htp-v81.so',
    'ggml-opencl': 'lib/arm64-v8a/libggml-opencl.so',
}
EXECUTORCH_SAMPLES = {'executorch', 'executorch-jsi', 'executorch-xnnpack', 'executorch-vulkan', 'executorch-qnn',
                      'worklets'}
FORBIDDEN = {
    'reference': set(SAMPLES),
    # ExecuTorch ships with its XNNPACK (CPU) backend only.
    'experimental': {'genie', 'executorch-vulkan', 'executorch-qnn'},
    # geniex-android bundles its own QNN and ggml Hexagon/OpenCL libraries.
    'geniex': {'llama-hexagon', 'llama-opencl', 'htp-asset', 'litert'} | EXECUTORCH_SAMPLES,
}
DEX_SAMPLES = {
    'litert': b'Lcom/google/ai/edge/litertlm/Engine;',
    'litert-bridge': b'Lcom/xebenchapp/LiteRtBenchModule;',
    'geniex': b'Lcom/geniex/sdk/GenieXSdk;',
    'geniex-bridge': b'Lcom/xebenchapp/GenieXBenchModule;',
    'executorch-bridge': b'Lcom/xebenchapp/ExecutorchBenchModule;',
}
FORBIDDEN_DEX = {
    'reference': set(DEX_SAMPLES),
    'experimental': {'geniex', 'geniex-bridge'},
    'geniex': {'litert', 'litert-bridge', 'executorch-bridge'},
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

    def test_experimental_npu_build_requires_dispatch_and_staged_qnn_libs(self):
        self.assertIn('lib/arm64-v8a/libLiteRtDispatch_Qualcomm.so', apk_check.FLAVORS['experimental']['required'])
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'fixture.apk'
            write_apk(path, 'experimental')
            apk_check.check_apk(path, 'experimental')
            with self.assertRaisesRegex(ValueError, 'missing .*libQnnHtpV81Skel.so'):
                apk_check.check_apk(path, 'experimental', require_qnn=True)
            write_apk(path, 'experimental', extra=sorted(apk_check.QNN_NPU_LIBS))
            apk_check.check_apk(path, 'experimental', require_qnn=True)
            with self.assertRaisesRegex(ValueError, 'only to the experimental'):
                apk_check.check_apk(path, 'reference', require_qnn=True)

    def test_experimental_hexagon_build_requires_the_hexagon_variant_and_htp_libraries(self):
        self.assertIn('assets/ggml-hexagon/libggml-htp-v81.so', apk_check.HEXAGON_LIBS)
        self.assertIn('lib/arm64-v8a/librnllama_jni_v8_2_dotprod_i8mm_hexagon_opencl.so', apk_check.HEXAGON_LIBS)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'fixture.apk'
            write_apk(path, 'experimental')
            with self.assertRaisesRegex(ValueError, 'missing .*libggml-htp-v81.so'):
                apk_check.check_apk(path, 'experimental', require_hexagon=True)
            for dropped in sorted(apk_check.HEXAGON_LIBS):
                with self.subTest(dropped=dropped):
                    write_apk(path, 'experimental', extra=sorted(apk_check.HEXAGON_LIBS - {dropped}))
                    with self.assertRaisesRegex(ValueError, 'missing .*' + re.escape(dropped)):
                        apk_check.check_apk(path, 'experimental', require_hexagon=True)
            write_apk(path, 'experimental', extra=sorted(apk_check.HEXAGON_LIBS | apk_check.QNN_NPU_LIBS))
            apk_check.check_apk(path, 'experimental', require_qnn=True, require_hexagon=True)
            with self.assertRaisesRegex(ValueError, 'only to the experimental'):
                apk_check.check_apk(path, 'reference', require_hexagon=True)



def write_contents(path, flavor, contents):
    """A flavor's minimal APK with some entries' bytes replaced or added (name -> bytes)."""
    spec = apk_check.FLAVORS[flavor]
    files = {name: b'fixture' for name in spec['required']}
    files.update(contents)
    with ZipFile(path, 'w') as apk:
        for name, data in files.items():
            apk.writestr(name, data)
        apk.writestr('classes.dex', b'dex\n' + b''.join(apk_check.COMMON_REQUIRED_DEX + spec.get('required_dex', [])))


V250, V245 = b'2.50.0.260828221209', b'2.45.0.260326154327'


class QualcommBundleTests(unittest.TestCase):
    """No flavor packages the GENIE-X QAIRT bundle; the geniex flavor packages the expected QAIRT runtime."""

    def test_no_flavor_packages_a_bundle_file_by_name(self):
        directory = apk_check.stage_geniex_bundle.BUNDLE['directory']
        for flavor in apk_check.FLAVORS:
            for name in ('assets/part1_of_4.bin', 'assets/models/genie_config.json',
                         'assets/htp_backend_ext_config.json', f'assets/{directory}/tokenizer.json'):
                with self.subTest(flavor=flavor, name=name), tempfile.TemporaryDirectory() as root:
                    path = Path(root) / 'fixture.apk'
                    write_apk(path, flavor, extra=[name])
                    with self.assertRaisesRegex(ValueError, 'unexpected ' + re.escape(name)):
                        apk_check.check_apk(path, flavor)

    def test_no_flavor_packages_a_bundle_file_under_another_name(self):
        data = b'a QAIRT context binary'
        digest = hashlib.sha256(data).hexdigest()
        with mock.patch.object(apk_check, 'QAIRT_BUNDLE_SHA256', {digest}), \
                mock.patch.object(apk_check, 'BUNDLE_SIZES', {len(data)}), tempfile.TemporaryDirectory() as root:
            for flavor in apk_check.FLAVORS:
                with self.subTest(flavor=flavor):
                    path = Path(root) / 'fixture.apk'
                    write_contents(path, flavor, {'assets/weights.dat': data})
                    with self.assertRaisesRegex(ValueError, 'unexpected assets/weights.dat'):
                        apk_check.check_apk(path, flavor)
                    write_contents(path, flavor, {'assets/weights.dat': data + b'!'})
                    apk_check.check_apk(path, flavor)

    def test_the_pins_cover_every_bundle_file(self):
        self.assertEqual(len(apk_check.QAIRT_BUNDLE_SHA256), len(apk_check.stage_geniex_bundle.BUNDLE['files']))

    def test_qairt_version_names_one_build_in_every_marked_qnn_library(self):
        htp, skel = apk_check.LIB + 'libQnnHtp.so', apk_check.LIB + 'libQnnHtpV81Skel.so'
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'fixture.apk'
            write_contents(path, 'geniex', {htp: b'QnnHtp v' + V250 + b'\0', skel: b'skel v' + V250})
            apk_check.check_apk(path, 'geniex', qairt_version=V250.decode())
            for name, data in ((htp, b'QnnHtp v' + V245), (skel, b'skel v' + V245), (htp, b'no marker'),
                               (skel, b'v' + V250 + b' and v' + V245)):
                with self.subTest(name=name, data=data):
                    write_contents(path, 'geniex', {htp: b'QnnHtp v' + V250, skel: b'skel v' + V250, name: data})
                    with self.assertRaisesRegex(ValueError, 'not 2.50.0.260828221209'):
                        apk_check.check_apk(path, 'geniex', qairt_version=V250.decode())
            with self.assertRaisesRegex(ValueError, 'experimental and geniex'):
                apk_check.check_apk(path, 'reference', qairt_version=V250.decode())

    def test_qairt_libs_requires_every_staged_library_byte_for_byte(self):
        staged = {'libQnnHtp.so': b'QnnHtp v' + V250, 'libQnnSystem.so': b'system'}
        manifest = {'qairtVersion': '2.50.0', 'libraries': [
            {'name': name, 'sha256': hashlib.sha256(data).hexdigest()} for name, data in staged.items()]}
        with tempfile.TemporaryDirectory() as root:
            listing = Path(root) / 'qairt-libs.json'
            listing.write_text(json.dumps(manifest))
            path = Path(root) / 'fixture.apk'
            write_contents(path, 'geniex', {apk_check.LIB + name: data for name, data in staged.items()})
            apk_check.check_apk(path, 'geniex', qairt_libs=listing)
            write_contents(path, 'geniex', {apk_check.LIB + 'libQnnHtp.so': staged['libQnnHtp.so'],
                                            apk_check.LIB + 'libQnnSystem.so': b'the AAR copy'})
            with self.assertRaisesRegex(ValueError, 'libQnnSystem.so is not the staged QAIRT 2.50.0 file'):
                apk_check.check_apk(path, 'geniex', qairt_libs=listing)


class BundledConfigurationTests(unittest.TestCase):
    def test_the_hashed_and_the_imported_bundled_configuration_are_one_file(self):
        """build.gradle hashes the file reference.ts bundles; if they differ, every bundled
        session names a file it did not run and its run is no longer a reviewed one."""
        root = Path(__file__).resolve().parents[1]
        gradle = (root / 'xebenchapp/android/app/build.gradle').read_text()
        runner = (root / 'xebenchapp/src/harness/reference.ts').read_text()
        hashed = re.findall(r'def bundledConfig = file\("\$rootDir/\.\./configs/([^"]+)"\)', gradle)
        imported = re.findall(r"import bundledConfig from '\.\./\.\./configs/([^']+)';", runner)
        self.assertEqual(hashed, ['cpu-reference-v2.json'])
        self.assertEqual(imported, hashed)
        self.assertTrue((root / 'xebenchapp/configs' / hashed[0]).is_file())


if __name__ == '__main__':
    unittest.main()
