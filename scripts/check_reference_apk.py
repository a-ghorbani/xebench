#!/usr/bin/env python3
"""Check an arm64 xebench APK carries exactly its build flavor's native runtimes.

The default ``reference`` flavor must contain the JS bundle and every llama.rn
CPU variant, and no native library outside React Native core and llama.rn CPU:
in particular nothing from the accelerator or vendor runtimes (Hexagon/HTP,
OpenCL, QNN/QAIRT, LiteRT-LM, GENIE-X, ExecuTorch and the react-native-worklets
runtime it needs). ``experimental`` carries ExecuTorch with its XNNPACK backend
only. No flavor may contain any file of the Qualcomm GENIE-X QAIRT bundle
(scripts/stage_geniex_bundle.py), by name or by content. Packaging is only one
check: device validation is still required.

For the flavors that carry QAIRT, --qairt-version requires every packaged QNN
library that names a QAIRT build to name that one, and --qairt-libs requires
every library of a staging manifest (stage_qairt_libs.py's qairt-libs.json) to
be packaged byte for byte.
"""
import argparse
from fnmatch import fnmatchcase
import hashlib
import json
from pathlib import Path
import re
import sys
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import stage_geniex_bundle  # noqa: E402

LIB = 'lib/arm64-v8a/'

# llama.rn picks the best CPU variant at runtime; a missing optimized variant would
# silently fall back to slower kernels.
LLAMA_CPU_VARIANTS = ['', '_v8', '_v8_2', '_v8_2_dotprod', '_v8_2_i8mm', '_v8_2_dotprod_i8mm']
COMMON_REQUIRED = {'assets/index.android.bundle'} | {
    LIB + f'librnllama{kind}{variant}.so'
    for kind in ('', '_jni') for variant in LLAMA_CPU_VARIANTS
}
# Every flavor registers the run-condition probe.
COMMON_REQUIRED_DEX = [b'Lcom/xebenchapp/BenchConditionsPackage;']
# Reference allowlist for native libraries: React Native core plus llama.rn CPU.
REFERENCE_LIBS = [LIB + name for name in (
    'libappmodules.so', 'libc++_shared.so', 'libfbjni.so', 'libhermestooling.so',
    'libhermesvm.so', 'libimagepipeline.so', 'libjsi.so', 'libnative-filters.so',
    'libnative-imagetranscoder.so', 'libreact_codegen_*.so', 'libreactnative.so',
)] + sorted(name for name in COMMON_REQUIRED if name.startswith(LIB))

# Shell-style patterns matched against APK entry names.
# llama.rn's accelerator variants and the HTP libraries it syncs into assets.
LLAMA_ACCELERATORS = [
    'lib/*/librnllama*hexagon*.so',
    'lib/*/librnllama*opencl*.so',
    'assets/ggml-hexagon/*',
]
# Any ggml Hexagon/OpenCL backend, including the copy geniex-android bundles.
GGML_ACCELERATORS = ['*libggml-htp-*.so', '*libggml-hexagon*.so', '*libggml-opencl*.so']
VENDOR_QNN = ['*libQnn*.so', '*libQairt*.so', '*libSNPE*.so', '*libcdsprpc.so']
LITERT = ['*liblitertlm*.so', '*libLiteRt*.so']
GENIEX = ['*libGenie*.so', '*libgeniex*.so']
# The Qualcomm QAIRT bundle is staged on the phone only, never packaged: its distinctive file names
# here, and every one of its files by content (QAIRT_BUNDLE_SHA256).
QAIRT_BUNDLE = ['*part[0-9]*_of_[0-9]*.bin', '*genie_config.json', '*htp_backend_ext_config.json',
                '*' + stage_geniex_bundle.BUNDLE['directory'] + '*']
QAIRT_BUNDLE_SHA256 = {digest for _, digest in stage_geniex_bundle.BUNDLE['files'].values()}
BUNDLE_SIZES = {size for size, _ in stage_geniex_bundle.BUNDLE['files'].values()}
# react-native-executorch: its JSI library, the ExecuTorch runtime, one library per
# backend, and react-native-worklets, whose runtimes it runs inference on.
EXECUTORCH = ['lib/*/libexecutorch.so', 'lib/*/libRnExecutorch.so',
              'lib/*/lib*_executorch_backend.so', 'lib/*/libworklets.so']
# The one ExecuTorch backend experimental packages (CPU); any other backend library is unexpected.
EXECUTORCH_BACKENDS = 'lib/*/lib*_executorch_backend.so'
XNNPACK_BACKEND = 'libxnnpack_executorch_backend.so'

# Dex type descriptors of engine bridges and SDKs that a flavor must not contain.
DEX_LITERT = [b'Lcom/google/ai/edge/litertlm/', b'Lcom/google/ai/edge/litert/', b'Lcom/xebenchapp/LiteRtBench']
DEX_GENIEX = [b'Lcom/geniex/sdk/', b'Lcom/xebenchapp/GenieXBench']
DEX_EXECUTORCH = [b'Lcom/xebenchapp/ExecutorchBench']

FLAVORS = {
    'reference': {
        'required': COMMON_REQUIRED,
        'forbidden': (LLAMA_ACCELERATORS + GGML_ACCELERATORS + VENDOR_QNN + LITERT
                      + GENIEX + EXECUTORCH),
        # Qualcomm Java SDKs (QNN/QAIRT/GENIE-X packaging) have no place in reference.
        'forbidden_dex': DEX_LITERT + DEX_GENIEX + DEX_EXECUTORCH + [b'Lcom/qualcomm/'],
        'allowed_libs': REFERENCE_LIBS,
    },
    'experimental': {
        'required': COMMON_REQUIRED | {
            LIB + 'liblitertlm_jni.so',
            # Google's Qualcomm dispatch (litert-npu-runtime-qualcomm, Maven).
            LIB + 'libLiteRtDispatch_Qualcomm.so',
            LIB + 'libexecutorch.so',
            LIB + 'libRnExecutorch.so',
            LIB + XNNPACK_BACKEND,
            LIB + 'libworklets.so',
        },
        'forbidden': GENIEX,
        'only_executorch_backend': XNNPACK_BACKEND,
        'forbidden_dex': DEX_GENIEX,
        'required_dex': [b'Lcom/xebenchapp/LiteRtBench', b'Lcom/xebenchapp/ExecutorchBench'],
    },
    'geniex': {
        # geniex-android's JNI, core and both plugins, and the QNN HTP runtime its qairt plugin loads.
        'required': COMMON_REQUIRED | {LIB + name for name in (
            'libgeniex.so', 'libgeniex_core.so', 'libgeniex_plugin_qairt.so', 'libgeniex_plugin_llama_cpp.so',
            'libnpu_jni.so', 'libQnnHtp.so', 'libQnnSystem.so', 'libQnnHtpV81Stub.so', 'libQnnHtpV81Skel.so',
        )},
        'forbidden': LLAMA_ACCELERATORS + LITERT + EXECUTORCH,
        'forbidden_dex': DEX_LITERT + DEX_EXECUTORCH,
        'required_dex': [b'Lcom/geniex/sdk/', b'Lcom/xebenchapp/GenieXBench'],
    },
}


# QAIRT runtime libraries LiteRT-LM's Qualcomm NPU path loads (scripts/stage_qairt_libs.py).
QNN_NPU_LIBS = {LIB + name for name in (
    'libQnnHtp.so', 'libQnnSystem.so', 'libQnnHtpV73Stub.so', 'libQnnHtpV81Stub.so',
    'libQnnHtpV73Skel.so', 'libQnnHtpV81Skel.so',
)}


# llama.cpp's Hexagon NPU backend: llama.rn's Hexagon/OpenCL variant (core and JNI) and the DSP
# libraries it syncs into assets/ggml-hexagon, for Hexagon v73 (SM8550) to v81 (SM8850). The build
# set xebench.excludeLlamaHexagon drops all of them. File names alone do not prove the backend:
# llama.rn 0.13.0-rc.8 always builds the hexagon_opencl variant and always syncs the DSP libraries,
# but compiles the Hexagon backend into it (ggml-hexagon.cpp, -DGGML_USE_HEXAGON) only when it finds
# a Hexagon SDK. So the variant must also contain HEXAGON_MARKER, the DSP-library URI format string
# of ggml-hexagon.cpp, which exists in no other source llama.rn compiles.
HEXAGON_VARIANT = LIB + 'librnllama_v8_2_dotprod_i8mm_hexagon_opencl.so'
HEXAGON_LIBS = {LIB + f'librnllama{kind}_v8_2_dotprod_i8mm_hexagon_opencl.so' for kind in ('', '_jni')} | {
    f'assets/ggml-hexagon/libggml-htp-{arch}.so' for arch in ('v73', 'v75', 'v79', 'v81')
}
HEXAGON_MARKER = b'file:///libggml-htp-v%u.so?htp_iface_skel_handle_invoke'



# A QAIRT build string as QNN libraries embed it (e.g. v2.50.0.260828221209).
QAIRT_MARKER = re.compile(rb'v([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{6,14})(?![0-9])')
QAIRT_FLAVORS = ('experimental', 'geniex')


def _sha256(handle):
    digest = hashlib.sha256()
    for block in iter(lambda: handle.read(1 << 20), b''):
        digest.update(block)
    return digest.hexdigest()


def qairt_problems(apk, entries, version=None, manifest=None):
    """--qairt-version and --qairt-libs: the packaged QNN runtime is the expected QAIRT build."""
    problems = []
    libraries = {entry.filename: entry for entry in entries if entry.filename.startswith(LIB)}
    if version is not None:
        if LIB + 'libQnnHtp.so' not in libraries:
            problems.append('missing ' + LIB + 'libQnnHtp.so for --qairt-version')
        for name, entry in sorted(libraries.items()):
            found = {match.decode() for match in QAIRT_MARKER.findall(apk.read(entry))}
            if not fnmatchcase(name, '*libQnn*.so') and not found:
                continue
            if found - {version} or (name == LIB + 'libQnnHtp.so' and version not in found):
                problems.append(f'{name} names QAIRT {", ".join(sorted(found)) or "nothing"}, not {version}')
    if manifest is not None:
        for library in manifest['libraries']:
            name = LIB + library['name']
            if name not in libraries:
                problems.append(f'missing staged QAIRT library {name}')
                continue
            with apk.open(libraries[name]) as handle:
                if _sha256(handle) != library['sha256']:
                    problems.append(f'{name} is not the staged QAIRT {manifest["qairtVersion"]} file')
    return problems


def check_apk(path, flavor='reference', require_qnn=False, require_hexagon=False, qairt_version=None,
              qairt_libs=None):
    if require_qnn and flavor != 'experimental':
        raise ValueError('--require-qnn applies only to the experimental flavor')
    if require_hexagon and flavor != 'experimental':
        raise ValueError('--require-hexagon applies only to the experimental flavor')
    if (qairt_version or qairt_libs) and flavor not in QAIRT_FLAVORS:
        raise ValueError('--qairt-version and --qairt-libs apply only to the experimental and geniex flavors')
    spec = dict(FLAVORS[flavor])
    if require_qnn:
        spec['required'] = spec['required'] | QNN_NPU_LIBS
    if require_hexagon:
        spec['required'] = spec['required'] | HEXAGON_LIBS
    manifest = json.loads(Path(qairt_libs).read_text()) if qairt_libs else None
    with ZipFile(path) as apk:
        entries = apk.infolist()
        present = {entry.filename for entry in entries if entry.file_size > 0}
        # Native code is recognised by content, not name: an ELF staged under assets/
        # or renamed (e.g. a DSP skel) must still meet the allowlist.
        elf = set()
        # So is the Qualcomm bundle: a renamed copy of any of its files is still refused.
        bundle_copies = []
        for entry in entries:
            with apk.open(entry) as handle:
                if handle.read(4) == b'\x7fELF':
                    elf.add(entry.filename)
            if entry.file_size in BUNDLE_SIZES and not entry.is_dir():
                with apk.open(entry) as handle:
                    if _sha256(handle) in QAIRT_BUNDLE_SHA256:
                        bundle_copies.append(entry.filename)
        dex = b''.join(apk.read(entry.filename) for entry in entries
                       if fnmatchcase(entry.filename, 'classes*.dex'))
        qairt = qairt_problems(apk, entries, qairt_version, manifest)
        hexagon_built = (require_hexagon and HEXAGON_VARIANT in present
                         and HEXAGON_MARKER in apk.read(HEXAGON_VARIANT))
    problems = []
    required_dex = COMMON_REQUIRED_DEX + spec.get('required_dex', [])
    missing = spec['required'] - present
    if missing:
        problems.append('missing ' + ', '.join(sorted(missing)))
    if require_hexagon and HEXAGON_VARIANT in present and not hexagon_built:
        problems.append(f'{HEXAGON_VARIANT} has no Hexagon backend (built without a Hexagon SDK)')
    names = {entry.filename for entry in entries}
    forbidden = spec['forbidden'] + QAIRT_BUNDLE
    unexpected = sorted(set(bundle_copies) | {name for name in names
                                               if any(fnmatchcase(name, pattern) for pattern in forbidden)})
    if 'only_executorch_backend' in spec:
        unexpected += sorted(name for name in names - set(unexpected)
                             if fnmatchcase(name, EXECUTORCH_BACKENDS)
                             and name.rsplit('/', 1)[-1] != spec['only_executorch_backend'])
    if 'allowed_libs' in spec:
        unexpected += sorted(
            name for name in names - set(unexpected)
            if (name in elf or name.endswith('.so'))
            and not any(fnmatchcase(name, pattern) for pattern in spec['allowed_libs']))
    if unexpected:
        problems.append('unexpected ' + ', '.join(unexpected))
    unexpected_dex = [d.decode() for d in spec.get('forbidden_dex', []) if d in dex]
    if unexpected_dex:
        problems.append('unexpected classes ' + ', '.join(unexpected_dex))
    missing_dex = [d.decode() for d in required_dex if d not in dex]
    if missing_dex:
        problems.append('missing classes ' + ', '.join(missing_dex))
    problems += qairt
    if problems:
        raise ValueError(f'{flavor} APK: ' + '; '.join(problems))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('apk')
    parser.add_argument('--flavor', choices=sorted(FLAVORS), default='reference')
    parser.add_argument('--require-qnn', action='store_true',
                        help='experimental only: also require the staged QAIRT libs for LiteRT NPU')
    parser.add_argument('--require-hexagon', action='store_true',
                        help="experimental only: also require llama.rn's Hexagon variant and the "
                             'libggml-htp-v73 to v81 DSP libraries (llama.cpp NPU)')
    parser.add_argument('--qairt-version', help='every packaged QNN library naming a QAIRT build names this one '
                        '(e.g. 2.50.0.260828221209)')
    parser.add_argument('--qairt-libs', help="a staging manifest (stage_qairt_libs.py's qairt-libs.json): "
                        'every library in it is packaged byte for byte')
    args = parser.parse_args()
    check_apk(args.apk, args.flavor, args.require_qnn, args.require_hexagon, args.qairt_version,
              args.qairt_libs)
    print(f'{args.flavor} APK packages its expected runtimes; device validation is still required.')
