#!/usr/bin/env python3
"""Stage the QAIRT (QNN) runtime libraries that LiteRT-LM's Qualcomm NPU path needs.

  stage_qairt_libs.py --qairt ~/qairt/2.50.0.260828 [--dest DIR] [--check]

The libraries are Qualcomm-licensed and must never be committed. They are copied
into the experimental flavor's jniLibs directory, which .gitignore excludes
(**/jniLibs/). The script refuses a destination that git would track, verifies the
SDK version from sdk.yaml, and writes qairt-libs.json next to the ABI directory
(outside it, so it is never packaged), listing
each file's SHA-256 so a build can be traced to the exact QAIRT bits.

--check verifies an existing staging instead of copying (exit 1 on any mismatch).
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEST = ROOT / "xebenchapp/android/app/src/experimental/jniLibs/arm64-v8a"
# The QAIRT release that litertlm-android 0.18.0 and 0.18.1 + litert-npu-runtime-qualcomm 2.3.0
# were validated against (docs/experiments/litert-accelerators.md).
EXPECTED_VERSION = "2.50.0"
# (source path relative to the SDK root, staged file name). Host-side stubs are arm64
# ELFs; skels are Hexagon DSP ELFs loaded by FastRPC from nativeLibraryDir.
LIBS = (
    ("lib/aarch64-android/libQnnHtp.so", "libQnnHtp.so"),
    ("lib/aarch64-android/libQnnSystem.so", "libQnnSystem.so"),
    ("lib/aarch64-android/libQnnHtpV73Stub.so", "libQnnHtpV73Stub.so"),
    ("lib/aarch64-android/libQnnHtpV81Stub.so", "libQnnHtpV81Stub.so"),
    ("lib/hexagon-v73/unsigned/libQnnHtpV73Skel.so", "libQnnHtpV73Skel.so"),
    ("lib/hexagon-v81/unsigned/libQnnHtpV81Skel.so", "libQnnHtpV81Skel.so"),
)
MANIFEST = "qairt-libs.json"


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def sdk_version(qairt):
    text = (qairt / "sdk.yaml").read_text(encoding="utf-8")
    match = re.search(r"^version:\s*([0-9.]+)\s*$", text, re.M)
    if not match:
        raise SystemExit(f"{qairt}/sdk.yaml has no version line")
    return match.group(1)


def ignored_by_git(dest):
    probe = dest / "libQnnHtp.so"
    result = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", str(probe)], check=False)
    return result.returncode == 0


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--qairt", required=True, type=Path, help="QAIRT SDK root (contains sdk.yaml)")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument("--check", action="store_true", help="verify an existing staging; copy nothing")
    parser.add_argument("--allow-version", action="store_true",
                        help=f"accept a QAIRT version other than {EXPECTED_VERSION}")
    args = parser.parse_args(argv)
    qairt, dest = args.qairt.expanduser().resolve(), args.dest.resolve()
    version = sdk_version(qairt)
    if version != EXPECTED_VERSION and not args.allow_version:
        raise SystemExit(f"QAIRT {version} is not the validated {EXPECTED_VERSION}; pass --allow-version to override")
    if not ignored_by_git(dest):
        raise SystemExit(f"Refusing {dest}: git would track it. Proprietary QAIRT libraries must stay git-ignored.")
    entries = []
    for source, name in LIBS:
        path = qairt / source
        if not path.is_file():
            raise SystemExit(f"Missing {source} in {qairt}")
        entries.append({"name": name, "source": source, "sha256": sha256(path), "bytes": path.stat().st_size})
    manifest = {"qairtVersion": version, "qairtRoot": qairt.name, "libraries": entries}
    if args.check:
        problems = [entry["name"] for entry in entries
                    if not (dest / entry["name"]).is_file() or sha256(dest / entry["name"]) != entry["sha256"]]
        if problems:
            print("Staged QAIRT libraries missing or different: " + ", ".join(problems), file=sys.stderr)
            return 1
        print(f"Staged QAIRT {version} libraries match ({len(entries)} files).")
        return 0
    dest.mkdir(parents=True, exist_ok=True)
    for (source, name), entry in zip(LIBS, entries):
        shutil.copyfile(qairt / source, dest / name)
        if sha256(dest / name) != entry["sha256"]:
            raise SystemExit(f"Copy of {name} does not match its source")
    (dest.parent / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    for entry in entries:
        print(f"{entry['sha256']}  {entry['name']}")
    print(f"Staged QAIRT {version} into {dest} (git-ignored).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
