#!/usr/bin/env python3
"""Verify a reference model file and stage it for the xebench app.

The expected bytes (Hugging Face repo, pinned revision, file name, size and
SHA-256) come from the committed manifest ``configs/models/reference-models.json``.
The script:

1. refuses a local file whose name, size or SHA-256 differs from the manifest;
2. pushes it with ``adb -s <serial>`` to the app's external files directory,
   under a temporary ``.partial`` name;
3. hashes the pushed copy on the device (``sha256sum``) and refuses on mismatch,
   deleting the partial copy, and only then renames it into place.

It never downloads the model: the weights are under the Llama 3.2 Community
License, which each user must read and accept. When the file is missing, or with
``--print-download``, it prints the documented download command for the pinned
revision instead. Standard library only.
"""
import argparse
import hashlib
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / 'configs/models/reference-models.json'
APP = 'com.xebenchapp'
# Context.getExternalFilesDir(null): the app loads `${ExternalDirectoryPath}/<modelFile>`.
DEVICE_DIR = f'/sdcard/Android/data/{APP}/files'

SHA256 = re.compile(r'^[0-9a-f]{64}$')
REVISION = re.compile(r'^[0-9a-f]{40}$')
REPO_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$')
BASENAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*\.gguf$')
SERIAL = re.compile(r'^[A-Za-z0-9._:-]+$')
REQUIRED = ('id', 'repo', 'revision', 'file', 'size', 'sha256', 'license', 'gated')


class StageError(Exception):
    """A refusal; ``code`` is the process exit status (1 verification, 2 usage/input)."""

    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def load_manifest(path):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise StageError(f'{path}: cannot read manifest: {error}', 2)
    models = data.get('models') if isinstance(data, dict) else None
    if not isinstance(data, dict) or data.get('schemaVersion') != 1 or not isinstance(models, list) or not models:
        raise StageError(f'{path}: unsupported manifest (expected schemaVersion 1 with models)', 2)
    for entry in models:
        if not isinstance(entry, dict) or any(key not in entry for key in REQUIRED):
            raise StageError(f'{path}: every model needs {", ".join(REQUIRED)}', 2)
        name = entry.get('id')
        valid = (
            isinstance(entry['id'], str) and entry['id']
            and isinstance(entry['repo'], str) and REPO_ID.match(entry['repo'])
            and isinstance(entry['revision'], str) and REVISION.match(entry['revision'])
            and isinstance(entry['file'], str) and BASENAME.match(entry['file'])
            and isinstance(entry['size'], int) and not isinstance(entry['size'], bool) and entry['size'] > 0
            and isinstance(entry['sha256'], str) and SHA256.match(entry['sha256'])
            and isinstance(entry['license'], str) and entry['license']
            and isinstance(entry['gated'], bool)
        )
        if not valid:
            raise StageError(f'{path}: model {name!r} has an invalid repo, revision (40-hex commit), '
                             'file (GGUF basename), size, sha256 (64 lowercase hex), license or gated value', 2)
    ids = [entry['id'] for entry in models]
    if len(set(ids)) != len(ids):
        raise StageError(f'{path}: model ids must be unique', 2)
    return models


def select(models, model_id=None):
    if model_id is None:
        if len(models) != 1:
            raise StageError('the manifest lists several models; pass --model-id', 2)
        return models[0]
    for model in models:
        if model['id'] == model_id:
            return model
    raise StageError(f'unknown model id {model_id!r}; known: {", ".join(m["id"] for m in models)}', 2)


def download_url(model):
    return f'https://huggingface.co/{model["repo"]}/resolve/{model["revision"]}/{model["file"]}'


def download_help(model):
    return '\n'.join([
        f'{model["file"]} is licensed under the {model["license"]}'
        + (f' ({model["licenseUrl"]})' if model.get('licenseUrl') else '') + '.',
        'Read and accept those terms yourself; xebench does not download or redistribute the weights.',
        f'Download the pinned revision {model["revision"]} of {model["repo"]} with either:',
        f'  hf download {model["repo"]} {model["file"]} --revision {model["revision"]} --local-dir models',
        f'  curl -L --fail -o models/{model["file"]} {download_url(model)}',
        f'Expected size {model["size"]} bytes, sha256 {model["sha256"]}.',
    ])


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def verify_local(path, model):
    path = Path(path)
    if not path.is_file():
        raise StageError(f'{path}: model file not found.\n{download_help(model)}', 2)
    if path.name != model['file']:
        raise StageError(f'{path.name}: expected the file name {model["file"]}; the app loads the model '
                         'by the basename in its configuration')
    size = path.stat().st_size
    if size != model['size']:
        raise StageError(f'{path}: size {size} bytes does not match the manifest ({model["size"]}); refusing to stage')
    digest = sha256_file(path)
    if digest != model['sha256']:
        raise StageError(f'{path}: sha256 {digest} does not match the manifest ({model["sha256"]}); refusing to stage')
    return digest


class Adb:
    """Runs adb against one device; every call names the serial explicitly."""

    def __init__(self, serial, executable='adb'):
        if not SERIAL.match(serial):
            raise StageError(f'invalid device serial {serial!r}', 2)
        self.serial = serial
        self.executable = executable

    def run(self, *args, check=True):
        try:
            result = subprocess.run([self.executable, '-s', self.serial, *args],
                                    capture_output=True, text=True)
        except OSError as error:
            raise StageError(f'cannot run {self.executable}: {error}', 2)
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise StageError(f'adb {" ".join(args)} failed (exit {result.returncode}): {detail}')
        return result

    def shell(self, *words, check=True):
        return self.run('shell', ' '.join(shlex.quote(word) for word in words), check=check)

    def sha256(self, device_path):
        """Return the device-side digest, or None when it cannot be computed."""
        result = self.shell('sha256sum', device_path, check=False)
        fields = result.stdout.split()
        if result.returncode != 0 or not fields or not SHA256.match(fields[0].lower()):
            return None
        return fields[0].lower()


def stage(path, model, adb, out=print):
    verify_local(path, model)
    out(f'local {model["file"]}: size and sha256 match the manifest')
    installed = adb.shell('pm', 'path', APP, check=False)
    if installed.returncode != 0 or 'package:' not in installed.stdout:
        raise StageError(f'{APP} is not installed on {adb.serial}; install the app first so its '
                         'external files directory belongs to it', 2)
    target = f'{DEVICE_DIR}/{model["file"]}'
    if adb.sha256(target) == model['sha256']:
        out(f'{target} already present and verified on {adb.serial}; not pushing')
        return target
    partial = f'{target}.partial'
    adb.shell('mkdir', '-p', DEVICE_DIR)
    try:
        adb.run('push', str(path), partial)
        pushed = adb.sha256(partial)
        if pushed != model['sha256']:
            raise StageError(f'device-side sha256 of {partial} is {pushed or "unavailable"}, expected '
                             f'{model["sha256"]}; removed the partial copy and refusing to stage')
        adb.shell('mv', '-f', partial, target)
    except BaseException:
        adb.shell('rm', '-f', partial, check=False)
        raise
    final = adb.sha256(target)
    if final != model['sha256']:
        raise StageError(f'device-side sha256 of {target} is {final or "unavailable"} after the rename, '
                         f'expected {model["sha256"]}')
    out(f'staged {target} on {adb.serial}: device sha256 {final} matches the manifest')
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('file', nargs='?', type=Path,
                        help='local model file (default: models/<manifest file> in the repository)')
    parser.add_argument('-s', '--serial', help='adb device serial (required unless --print-download)')
    parser.add_argument('--model-id', help='manifest model id (default: the only model listed)')
    parser.add_argument('--manifest', type=Path, default=MANIFEST, help='model manifest (default: %(default)s)')
    parser.add_argument('--adb', default='adb', help='adb executable (default: %(default)s)')
    parser.add_argument('--print-download', action='store_true',
                        help='print the download command for the pinned revision and exit')
    args = parser.parse_args(argv)
    try:
        model = select(load_manifest(args.manifest), args.model_id)
        if args.print_download:
            print(download_help(model))
            return 0
        if not args.serial:
            raise StageError('pass --serial <device serial> (see `adb devices`)', 2)
        stage(args.file or REPO / 'models' / model['file'], model, Adb(args.serial, args.adb))
    except StageError as error:
        print(f'stage_model: {error}', file=sys.stderr)
        return error.code
    return 0


if __name__ == '__main__':
    sys.exit(main())
