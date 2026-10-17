#!/usr/bin/env python3
"""Check an arm64 xebench APK carries exactly its build flavor's native runtimes.

The default ``reference`` flavor must contain the JS bundle and every llama.rn
CPU variant, and no native library outside React Native core and llama.rn CPU:
in particular nothing from the accelerator or vendor runtimes (Hexagon/HTP,
OpenCL, QNN/QAIRT, LiteRT-LM, GENIE-X, ExecuTorch). Packaging is only one
check: device validation is still required.
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
EXECUTORCH = ['lib/*/libexecutorch.so', 'lib/*/libreact-native-executorch.so',
              'lib/*/libcpuinfo.so', 'lib/*/libpthreadpool.so']

# Dex type descriptors of engine bridges and SDKs that a flavor must not contain.
DEX_LITERT = [b'Lcom/google/ai/edge/litertlm/', b'Lcom/google/ai/edge/litert/', b'Lcom/xebenchapp/LiteRtBench']
DEX_GENIEX = [b'Lcom/geniex/sdk/', b'Lcom/xebenchapp/GenieXBench']

FLAVORS = {
    'reference': {
        'required': COMMON_REQUIRED,
        'forbidden': (LLAMA_ACCELERATORS + GGML_ACCELERATORS + VENDOR_QNN + LITERT
                      + GENIEX + EXECUTORCH),
        # Qualcomm Java SDKs (QNN/QAIRT/GENIE-X packaging) have no place in reference.
        'forbidden_dex': DEX_LITERT + DEX_GENIEX + [b'Lcom/qualcomm/'],
        'allowed_libs': REFERENCE_LIBS,
    },
    'experimental': {
        'required': COMMON_REQUIRED | {
            LIB + 'liblitertlm_jni.so',
            LIB + 'libexecutorch.so',
            LIB + 'libreact-native-executorch.so',
        },
        'forbidden': GENIEX,
        'forbidden_dex': DEX_GENIEX,
        'required_dex': [b'Lcom/xebenchapp/LiteRtBench'],
    },
    'geniex': {
        'required': COMMON_REQUIRED | {LIB + 'libgeniex.so'},
        'forbidden': LLAMA_ACCELERATORS + LITERT + EXECUTORCH,
        'forbidden_dex': DEX_LITERT,
        'required_dex': [b'Lcom/geniex/sdk/', b'Lcom/xebenchapp/GenieXBench'],
    },
}


def check_apk(path, flavor='reference'):
    spec = FLAVORS[flavor]
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
    args = parser.parse_args()
    check_apk(args.apk, args.flavor)
    print(f'{args.flavor} APK packages its expected runtimes; device validation is still required.')
