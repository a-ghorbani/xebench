#!/usr/bin/env python3
"""Check an arm64 xebench APK carries exactly its build flavor's native runtimes.

The default ``reference`` flavor must contain the JS bundle and llama.rn CPU
libraries and nothing from the accelerator or vendor runtimes (Hexagon/HTP,
OpenCL, QNN/QAIRT, LiteRT-LM, GENIE-X, ExecuTorch). Packaging is only one
check: device validation is still required.
"""
import argparse
from fnmatch import fnmatchcase
from zipfile import ZipFile

LIB = 'lib/arm64-v8a/'

COMMON_REQUIRED = {
    'assets/index.android.bundle',
    LIB + 'librnllama.so',
    LIB + 'librnllama_jni_v8.so',
    LIB + 'librnllama_v8.so',
}

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
DEX_LITERT = [b'Lcom/google/ai/edge/litertlm/', b'Lcom/xebenchapp/LiteRtBench']
DEX_GENIEX = [b'Lcom/geniex/sdk/', b'Lcom/xebenchapp/GenieXBench']

FLAVORS = {
    'reference': {
        'required': COMMON_REQUIRED,
        'forbidden': (LLAMA_ACCELERATORS + GGML_ACCELERATORS + VENDOR_QNN + LITERT
                      + GENIEX + EXECUTORCH),
        'forbidden_dex': DEX_LITERT + DEX_GENIEX,
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
        dex = b''.join(apk.read(entry.filename) for entry in entries
                       if fnmatchcase(entry.filename, 'classes*.dex'))
    problems = []
    missing = spec['required'] - present
    if missing:
        problems.append('missing ' + ', '.join(sorted(missing)))
    names = {entry.filename for entry in entries}
    unexpected = sorted(name for name in names
                        if any(fnmatchcase(name, pattern) for pattern in spec['forbidden']))
    if unexpected:
        problems.append('unexpected ' + ', '.join(unexpected))
    unexpected_dex = [d.decode() for d in spec.get('forbidden_dex', []) if d in dex]
    if unexpected_dex:
        problems.append('unexpected classes ' + ', '.join(unexpected_dex))
    missing_dex = [d.decode() for d in spec.get('required_dex', []) if d not in dex]
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
