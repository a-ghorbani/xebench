#!/usr/bin/env python3
"""Check the arm64 reference APK includes its JS bundle and CPU runtime."""
import argparse
from zipfile import ZipFile

REQUIRED = {
    'assets/index.android.bundle',
    'lib/arm64-v8a/librnllama.so',
    'lib/arm64-v8a/librnllama_jni_v8.so',
    'lib/arm64-v8a/librnllama_v8.so',
}


def check_apk(path):
    with ZipFile(path) as apk:
        present = {entry.filename for entry in apk.infolist() if entry.file_size > 0}
    missing = REQUIRED - present
    if missing:
        raise ValueError('Missing APK artifacts: ' + ', '.join(sorted(missing)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('apk')
    check_apk(parser.parse_args().apk)
    print('Reference CPU runtime and JS bundle are packaged; device validation is still required.')
