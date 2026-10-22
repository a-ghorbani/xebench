#!/usr/bin/env python3
"""Download, verify and unpack the Qualcomm GENIE-X QAIRT bundle; optionally push it to a phone.

  stage_geniex_bundle.py [--zip PATH] [--dest DIR]           download (or take) the zip and unpack it
  stage_geniex_bundle.py --check [--dest DIR]                verify an unpacked bundle against the pins
  stage_geniex_bundle.py --push --serial SERIAL [--dest DIR] verify, then push it into the app's files

The bundle is Qualcomm AI Hub's `geniex_qairt` W4A16 build of Qwen3 1.7B for "Snapdragon 8 Elite
Gen 5 for Galaxy" (release v0.65.0, QAIRT 2.50; qualcomm/Qwen3-1.7B release_assets.json). Its pins
(BUNDLE below) are the zip's size and SHA-256, every unpacked file's size and SHA-256, and the bundle
digest that the app records as the run's artifact: the SHA-256 of the bundle's `sha256sum` listing
("<sha256>  <name>" per file, sorted by name in byte order; xebenchapp/src/harness/bundle.ts), which
`ls | LC_ALL=C sort | xargs sha256sum -- | sha256sum` reproduces in the bundle directory.

The bundle is never committed, packaged into an APK or redistributed. The script unpacks it into a
git-ignored directory (default models/geniex/; a destination inside the repository that git would
track is refused), refuses
an archive member outside the bundle directory, a link, or a file the pins do not list, and writes
geniex-bundle.json beside the bundle directory (outside it, so the digest covers the bundle only)
with every hash. --push copies the directory to the app's external files directory under a
temporary name, hashes every file on the phone (sha256sum), and only then renames it into place.
Standard library only.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shlex
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEST = ROOT / "models/geniex"
APP = "com.xebenchapp"
# Context.getExternalFilesDir(null): the app loads `${ExternalDirectoryPath}/<modelFile>/genie_config.json`.
DEVICE_DIR = f"/sdcard/Android/data/{APP}/files"
MANIFEST = "geniex-bundle.json"

BUNDLE = {
    "id": "qwen3-1.7b-geniex-qairt-w4a16-sm8850-galaxy",
    "model": "Qwen3 1.7B",
    "baseModel": "Qwen/Qwen3-1.7B",
    "repo": "qualcomm/Qwen3-1.7B",
    # The revision whose release_assets.json lists this asset (version 0.65.0).
    "repoRevision": "72c571233b88645979859e0f0e91004494d32559",
    "aiHubRelease": "v0.65.0",
    "runtime": "geniex_qairt",
    "precision": "w4a16",
    "chipset": "qualcomm-snapdragon-8-elite-gen5-for-galaxy",
    "qairt": "2.50.0.260828221209",
    "license": "Apache-2.0 (model card metadata); Qualcomm AI Hub usage limitations on the card",
    "url": "https://qaihub-public-assets.s3.us-west-2.amazonaws.com/qai-hub-models/models/qwen3_1_7b/releases/"
           "v0.65.0/qwen3_1_7b-geniex_qairt-w4a16-qualcomm_snapdragon_8_elite_gen5_for_galaxy.zip",
    "zip": {"name": "qwen3_1_7b-geniex_qairt-w4a16-qualcomm_snapdragon_8_elite_gen5_for_galaxy.zip",
            "size": 1538593271, "sha256": "ab021d189e387ba417e9156fb8d5fcee7a98cf1befd899a8e6f2a9b65dbf9b2b"},
    # The archive's only top-level entry; the configuration's modelFile for the NPU run.
    "directory": "qwen3_1_7b-geniex_qairt-w4a16-qualcomm_snapdragon_8_elite_gen5_for_galaxy",
    "files": {
        "config.json": (1417, "03b237b8c6b128c1f8ba503f8442990269ebf70c2351799c9c6d70f7a7b7dcd9"),
        "genie_config.json": (1629, "52109d55a8cbd67b3d7024a2a7328a22bf7f0d89771782810836d2408892ca8f"),
        "htp_backend_ext_config.json": (217, "3ce32a5c9e100ae98e846c6f071b843210571c541d39422a74f278c2b61c79d8"),
        "metadata.json": (56686, "60ef524d1b927364357eee146f8e0f9067b417f4593d5d8e4bbd4e3d2de67cea"),
        "part1_of_4.bin": (622596096, "f01f9c7097f0e2889cdc1d5649e53bd194f862e5b53243b0feb6dbfa3bdd2ea6"),
        "part2_of_4.bin": (292835328, "984cdff4129db2f54c0cc4624a53d406f2e37b1045f380ad3b0799b918ea1c52"),
        "part3_of_4.bin": (286552064, "997c1ad0f57cf9b19633e9da25cccec5e2c143add55058efea02686bda906684"),
        "part4_of_4.bin": (550944768, "c4d3e82494d655a788dbebed9078cda699eddf86c7458b04691ae56e33363663"),
        "sample_prompt.txt": (179, "bcba092776d028282fad37d8778d128834a309691f8118c08a762ccd0c4f54ac"),
        "tokenizer.json": (11422650, "be75606093db2094d7cd20f3c2f385c212750648bd6ea4fb2bf507a6a4c55506"),
        "tokenizer_config.json": (5067, "9b301a839b6dc74e9cdce131a46482c4975831feb43449514c020505e0dbf290"),
    },
    "bundleSha256": "c41b7071b62e791b924254f1e8ed3245ee74825013c1723e3a62e8877b2ab5c2",
    "bundleBytes": 1764416101,
}


class StageError(Exception):
    """A refusal; ``code`` is the process exit status (1 verification, 2 usage/input)."""

    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def listing(hashes):
    """The `sha256sum` listing the bundle digest hashes: {name: sha256} sorted by name."""
    return "".join(f"{hashes[name]}  {name}\n" for name in sorted(hashes))


def bundle_digest(hashes):
    return hashlib.sha256(listing(hashes).encode("utf-8")).hexdigest()


def untracked_location(path):
    """True outside the repository, or inside it where git ignores the path."""
    try:
        path.resolve().relative_to(ROOT)
    except ValueError:
        return True
    result = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", str(path)], check=False)
    return result.returncode == 0


def download(url, target, expected, opener=urllib.request.urlopen):
    """Stream url to target.partial while hashing; rename only when size and SHA-256 match."""
    partial = target.with_name(target.name + ".partial")
    digest, size = hashlib.sha256(), 0
    with opener(url) as response, open(partial, "wb") as out:
        for block in iter(lambda: response.read(1 << 20), b""):
            digest.update(block)
            size += len(block)
            out.write(block)
    if size != expected["size"] or digest.hexdigest() != expected["sha256"]:
        partial.unlink()
        raise StageError(f"downloaded {size} bytes with sha256 {digest.hexdigest()}; expected {expected['size']} "
                         f"bytes and {expected['sha256']}")
    partial.rename(target)
    return target


def verify_zip(path, bundle=BUNDLE):
    size = path.stat().st_size
    digest = sha256_file(path)
    if size != bundle["zip"]["size"] or digest != bundle["zip"]["sha256"]:
        raise StageError(f"{path.name}: {size} bytes, sha256 {digest}; expected {bundle['zip']['size']} bytes, "
                         f"sha256 {bundle['zip']['sha256']}")


def members(archive, bundle=BUNDLE):
    """name -> ZipInfo of the archive's files, refusing anything outside the pinned bundle layout."""
    found = {}
    for info in archive.infolist():
        parts = PurePosixPath(info.filename).parts
        mode = info.external_attr >> 16
        if info.filename.startswith("/") or ".." in parts or not parts or parts[0] != bundle["directory"]:
            raise StageError(f"archive member outside {bundle['directory']}/: {info.filename!r}")
        if (mode & 0o170000) == 0o120000:
            raise StageError(f"archive member is a link: {info.filename!r}")
        if info.is_dir():
            if len(parts) != 1:
                raise StageError(f"archive holds a subdirectory: {info.filename!r}")
            continue
        if len(parts) != 2 or parts[1] not in bundle["files"]:
            raise StageError(f"archive member the pins do not list: {info.filename!r}")
        found[parts[1]] = info
    missing = sorted(set(bundle["files"]) - set(found))
    if missing:
        raise StageError(f"archive lacks {', '.join(missing)}")
    return found


def verify_directory(directory, bundle=BUNDLE):
    """{name: sha256} of an unpacked bundle, refusing any file that is missing, extra or different."""
    names = sorted(entry.name for entry in directory.iterdir())
    if names != sorted(bundle["files"]) or any(not (directory / name).is_file() or (directory / name).is_symlink()
                                               for name in names):
        raise StageError(f"{directory} does not hold exactly the pinned files")
    hashes = {}
    for name, (size, digest) in bundle["files"].items():
        path = directory / name
        actual = sha256_file(path)
        if path.stat().st_size != size or actual != digest:
            raise StageError(f"{path}: size {path.stat().st_size}, sha256 {actual}; expected {size}, {digest}")
        hashes[name] = actual
    if bundle_digest(hashes) != bundle["bundleSha256"]:
        raise StageError("the bundle digest does not match the pin")
    return hashes


def manifest(hashes, bundle=BUNDLE):
    return {"schemaVersion": 1, **{key: bundle[key] for key in (
        "id", "model", "baseModel", "repo", "repoRevision", "aiHubRelease", "runtime", "precision", "chipset",
        "qairt", "license", "url", "zip", "directory")},
        "files": [{"name": name, "bytes": bundle["files"][name][0], "sha256": hashes[name]} for name in sorted(hashes)],
        "bundleSha256": bundle_digest(hashes), "bundleBytes": sum(size for size, _ in bundle["files"].values()),
        "bundleSha256Rule": "sha256 of the sha256sum listing of the bundle's files, sorted by name"}


def unpack(zip_path, dest, bundle=BUNDLE, out=print):
    """Unpack into dest/<directory> via a temporary directory; verify every file; write the manifest."""
    target = dest / bundle["directory"]
    staging = dest / (bundle["directory"] + ".partial")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            for name, info in members(archive, bundle).items():
                with archive.open(info) as source, open(staging / name, "wb") as out_file:
                    shutil.copyfileobj(source, out_file, 1 << 20)
        hashes = verify_directory(staging, bundle)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if target.exists():
        shutil.rmtree(target)
    staging.rename(target)
    (dest / MANIFEST).write_text(json.dumps(manifest(hashes, bundle), indent=2) + "\n", encoding="utf-8")
    out(f"unpacked {target} (bundle digest {bundle['bundleSha256']}, git-ignored)")
    return target


class Adb:
    """Runs adb against one device; every call names the serial explicitly."""

    def __init__(self, serial, executable="adb", runner=subprocess.run):
        if not serial or not all(ch.isalnum() or ch in "._:-" for ch in serial):
            raise StageError(f"invalid device serial {serial!r}", 2)
        self.serial, self.executable, self.runner = serial, executable, runner

    def run(self, *args, check=True):
        result = self.runner([self.executable, "-s", self.serial, *args], capture_output=True, text=True)
        if check and result.returncode != 0:
            raise StageError(f"adb {' '.join(args)} failed (exit {result.returncode}): "
                             f"{(result.stderr or result.stdout).strip()}")
        return result

    def shell(self, *words, check=True):
        return self.run("shell", " ".join(shlex.quote(word) for word in words), check=check)

    def hashes(self, directory):
        """{name: sha256} of the files directly in a device directory; None when unavailable."""
        result = self.shell("sh", "-c", f"cd {shlex.quote(directory)} && sha256sum -- *", check=False)
        if result.returncode != 0:
            return None
        found = {}
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) != 2:
                return None
            found[fields[1]] = fields[0].lower()
        return found


def push(directory, adb, bundle=BUNDLE, out=print):
    expected = {name: digest for name, (_, digest) in bundle["files"].items()}
    target = f"{DEVICE_DIR}/{bundle['directory']}"
    if adb.hashes(target) == expected:
        out(f"{target} already present and verified on {adb.serial}; not pushing")
        return target
    partial = f"{target}.partial"
    adb.shell("rm", "-rf", partial)
    adb.shell("mkdir", "-p", partial)
    try:
        for name in sorted(expected):
            adb.run("push", str(directory / name), f"{partial}/{name}")
        pushed = adb.hashes(partial)
        if pushed != expected:
            raise StageError(f"device-side hashes of {partial} do not match the pins; refusing to stage")
        adb.shell("rm", "-rf", target)
        adb.shell("mv", partial, target)
    except BaseException:
        adb.shell("rm", "-rf", partial, check=False)
        raise
    if adb.hashes(target) != expected:
        adb.shell("rm", "-rf", target, check=False)
        raise StageError(f"device-side hashes of {target} do not match after the rename; removed it")
    out(f"staged {target} on {adb.serial}: every file matches its pin (bundle digest {bundle['bundleSha256']})")
    return target


def main(argv=None, out=print):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--zip", type=Path, help="a local copy of the zip (default: download it into --dest)")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST, help="git-ignored directory (default: %(default)s)")
    parser.add_argument("--check", action="store_true", help="verify the unpacked bundle; download nothing")
    parser.add_argument("--push", action="store_true", help="verify, then push the bundle to the phone")
    parser.add_argument("-s", "--serial", help="adb device serial (--push)")
    parser.add_argument("--adb", default="adb")
    args = parser.parse_args(argv)
    dest = args.dest.expanduser().resolve()
    try:
        if not untracked_location(dest / BUNDLE["directory"]):
            raise StageError(f"refusing {dest}: git would track it; the Qualcomm bundle must stay git-ignored", 2)
        directory = dest / BUNDLE["directory"]
        if args.check or args.push:
            verify_directory(directory)
            out(f"{directory}: every file and the bundle digest {BUNDLE['bundleSha256']} match the pins")
            if args.push:
                if not args.serial:
                    raise StageError("pass --serial <device serial> (see `adb devices`)", 2)
                push(directory, Adb(args.serial, args.adb), out=out)
            return 0
        dest.mkdir(parents=True, exist_ok=True)
        zip_path = args.zip.expanduser().resolve() if args.zip else dest / BUNDLE["zip"]["name"]
        if not zip_path.is_file():
            if args.zip:
                raise StageError(f"{zip_path}: not found", 2)
            out(f"downloading {BUNDLE['url']}")
            download(BUNDLE["url"], zip_path, BUNDLE["zip"])
        verify_zip(zip_path)
        out(f"{zip_path.name}: size and sha256 match the pin")
        unpack(zip_path, dest, out=out)
    except StageError as error:
        print(f"stage_geniex_bundle: {error}", file=sys.stderr)
        return error.code
    return 0


if __name__ == "__main__":
    sys.exit(main())
