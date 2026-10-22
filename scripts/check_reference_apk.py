#!/usr/bin/env python3
"""Check an arm64 xebench APK carries exactly its build flavor's native runtimes.

The default ``reference`` flavor must contain the JS bundle and every llama.rn
CPU variant, and no native library outside React Native core and llama.rn CPU:
in particular nothing from the accelerator or vendor runtimes (Hexagon/HTP,
OpenCL, QNN/QAIRT, LiteRT-LM, GENIE-X, ExecuTorch and the react-native-worklets
runtime it needs). ``experimental`` carries ExecuTorch with its XNNPACK backend
only. Packaging is only one check: device validation is still required.
"""
import argparse
from fnmatch import fnmatchcase
from zipfile import ZipFile

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
        'required': COMMON_REQUIRED | {LIB + 'libgeniex.so'},
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
# libraries it syncs into assets/ggml-hexagon, for Hexagon v73 (SM8550) to v81 (SM8850). Absent
# when llama.rn found no Hexagon SDK at build time or the build set xebench.excludeLlamaHexagon.
HEXAGON_LIBS = {LIB + f'librnllama{kind}_v8_2_dotprod_i8mm_hexagon_opencl.so' for kind in ('', '_jni')} | {
    f'assets/ggml-hexagon/libggml-htp-{arch}.so' for arch in ('v73', 'v75', 'v79', 'v81')
}


def check_apk(path, flavor='reference', require_qnn=False, require_hexagon=False):
    if require_qnn and flavor != 'experimental':
        raise ValueError('--require-qnn applies only to the experimental flavor')
    if require_hexagon and flavor != 'experimental':
        raise ValueError('--require-hexagon applies only to the experimental flavor')
    spec = dict(FLAVORS[flavor])
    if require_qnn:
        spec['required'] = spec['required'] | QNN_NPU_LIBS
    if require_hexagon:
        spec['required'] = spec['required'] | HEXAGON_LIBS
    with ZipFile(path) as apk:
        entries = apk.infolist()
        present = {entry.filename for entry in entries if entry.file_size > 0}
        # Native code is recognised by content, not name: an ELF staged under assets/
        # or renamed (e.g. a DSP skel) must still meet the allowlist.
        elf = set()
        for entry in entries:
            with apk.open(entry) as handle:
                if handle.read(4) == b'\x7fELF':
                    elf.add(entry.filename)
        dex = b''.join(apk.read(entry.filename) for entry in entries
                       if fnmatchcase(entry.filename, 'classes*.dex'))
    problems = []
    required_dex = COMMON_REQUIRED_DEX + spec.get('required_dex', [])
    missing = spec['required'] - present
    if missing:
        problems.append('missing ' + ', '.join(sorted(missing)))
    names = {entry.filename for entry in entries}
    unexpected = sorted(name for name in names
                        if any(fnmatchcase(name, pattern) for pattern in spec['forbidden']))
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
    args = parser.parse_args()
    check_apk(args.apk, args.flavor, args.require_qnn, args.require_hexagon)
    print(f'{args.flavor} APK packages its expected runtimes; device validation is still required.')
