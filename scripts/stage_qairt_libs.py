#!/usr/bin/env python3
"""Stage the QAIRT (QNN) runtime libraries a flavor's Qualcomm NPU path needs.

  stage_qairt_libs.py --qairt ~/qairt/2.50.0.260828 [--flavor experimental|geniex] [--dest DIR] [--check]

The libraries are Qualcomm-licensed and must never be committed. They are copied into the
flavor's jniLibs directory, which .gitignore excludes (**/jniLibs/). The script refuses a
destination that git would track, verifies the SDK version from sdk.yaml, and writes
qairt-libs.json next to the ABI directory (outside it, so it is never packaged), listing each
file's SHA-256 so a build can be traced to the exact QAIRT bits.

experimental (default): the libraries LiteRT-LM's Qualcomm NPU path loads.

geniex: the QAIRT build of every QNN library geniex-android 0.8.0 packages. That AAR carries the
QAIRT 2.45.0 runtime, and the GENIE-X QAIRT bundles of the model refresh are built with QAIRT 2.50;
GenieXSdk.setQairtRuntimePath cannot select another runtime in 0.8.0 (its JNI_OnLoad locks the path
first), so the APK packages QAIRT 2.50 in place of the AAR's copies (app/build.gradle picks the
staged files first). The set is the AAR's whole QNN runtime, so no 2.45 file is left beside them;
`check_reference_apk.py --flavor geniex --qairt-version` checks the result.

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
APP = ROOT / "xebenchapp/android/app/src"
# The QAIRT release that litertlm-android 0.18.0 and 0.18.1 + litert-npu-runtime-qualcomm 2.3.0 were
# validated against (docs/experiments/litert-accelerators.md), and the release the GENIE-X bundles are
# built with.
EXPECTED_VERSION = "2.50.0"
HOST, V79, V81 = "lib/aarch64-android", "lib/hexagon-v79/unsigned", "lib/hexagon-v81/unsigned"
# (source path relative to the SDK root, staged file name). Host-side stubs are arm64
# ELFs; skels are Hexagon DSP ELFs loaded by FastRPC from nativeLibraryDir.
LIBS = (
    (f"{HOST}/libQnnHtp.so", "libQnnHtp.so"),
    (f"{HOST}/libQnnSystem.so", "libQnnSystem.so"),
    (f"{HOST}/libQnnHtpV73Stub.so", "libQnnHtpV73Stub.so"),
    (f"{HOST}/libQnnHtpV81Stub.so", "libQnnHtpV81Stub.so"),
    ("lib/hexagon-v73/unsigned/libQnnHtpV73Skel.so", "libQnnHtpV73Skel.so"),
    (f"{V81}/libQnnHtpV81Skel.so", "libQnnHtpV81Skel.so"),
)
# Every QNN runtime file in geniex-android 0.8.0 (jni/arm64-v8a), from the SDK folder that holds
# the same kind of ELF (arm64 host libraries, or Hexagon v79/v81 DSP libraries).
GENIEX_HOST = (
    "libcalculator.so", "libhta_hexagon_runtime_qnn.so", "libhta_hexagon_runtime_snpe.so",
    "libPlatformValidatorShared.so", "libQnnChrometraceProfilingReader.so", "libQnnHta.so",
    "libQnnHtaNetRunExtensions.so", "libQnnHtp.so", "libQnnHtpNetRunExtensions.so",
    "libQnnHtpOptraceProfilingReader.so", "libQnnHtpPrepare.so", "libQnnHtpProfilingReader.so",
    "libQnnHtpV79CalculatorStub.so", "libQnnHtpV79Stub.so", "libQnnHtpV81CalculatorStub.so",
    "libQnnHtpV81Stub.so", "libQnnIr.so", "libQnnJsonProfilingReader.so", "libQnnModelDlc.so",
    "libQnnNetRunDirectV79Stub.so", "libQnnNetRunDirectV81Stub.so", "libQnnSaver.so", "libQnnSystem.so",
)
GENIEX_LIBS = tuple((f"{HOST}/{name}", name) for name in GENIEX_HOST) + (
    (f"{V79}/libQnnHtpV79Skel.so", "libQnnHtpV79Skel.so"),
    (f"{V79}/libQnnHtpV79.so", "libQnnHtpV79.so"),
    (f"{V79}/libQnnNetRunDirectV79Skel.so", "libQnnNetRunDirectV79Skel.so"),
    (f"{V81}/libQnnHtpV81Skel.so", "libQnnHtpV81Skel.so"),
    (f"{V81}/libQnnHtpV81.so", "libQnnHtpV81.so"),
    (f"{V81}/libQnnNetRunDirectV81Skel.so", "libQnnNetRunDirectV81Skel.so"),
    # geniex-android packages one calculator skel; the bundles target v81 (Snapdragon 8 Elite Gen 5).
    (f"{V81}/libCalculator_skel.so", "libCalculator_skel.so"),
)
FLAVORS = {
    "experimental": {"libs": LIBS, "dest": APP / "experimental/jniLibs/arm64-v8a"},
    "geniex": {"libs": GENIEX_LIBS, "dest": APP / "geniex/jniLibs/arm64-v8a"},
}
DEFAULT_DEST = FLAVORS["experimental"]["dest"]
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
    parser.add_argument("--flavor", choices=sorted(FLAVORS), default="experimental")
    parser.add_argument("--dest", type=Path, help="default: the flavor's jniLibs/arm64-v8a")
    parser.add_argument("--check", action="store_true", help="verify an existing staging; copy nothing")
    parser.add_argument("--allow-version", action="store_true",
                        help=f"accept a QAIRT version other than {EXPECTED_VERSION}")
    args = parser.parse_args(argv)
    libs = FLAVORS[args.flavor]["libs"]
    qairt = args.qairt.expanduser().resolve()
    dest = (args.dest or FLAVORS[args.flavor]["dest"]).resolve()
    version = sdk_version(qairt)
    if version != EXPECTED_VERSION and not args.allow_version:
        raise SystemExit(f"QAIRT {version} is not the validated {EXPECTED_VERSION}; pass --allow-version to override")
    if not ignored_by_git(dest):
        raise SystemExit(f"Refusing {dest}: git would track it. Proprietary QAIRT libraries must stay git-ignored.")
    entries = []
    for source, name in libs:
        path = qairt / source
        if not path.is_file():
            raise SystemExit(f"Missing {source} in {qairt}")
        entries.append({"name": name, "source": source, "sha256": sha256(path), "bytes": path.stat().st_size})
    manifest = {"qairtVersion": version, "qairtRoot": qairt.name, "flavor": args.flavor, "libraries": entries}
    if args.check:
        problems = [entry["name"] for entry in entries
                    if not (dest / entry["name"]).is_file() or sha256(dest / entry["name"]) != entry["sha256"]]
        if problems:
            print("Staged QAIRT libraries missing or different: " + ", ".join(problems), file=sys.stderr)
            return 1
        print(f"Staged QAIRT {version} libraries match ({len(entries)} files).")
        return 0
    dest.mkdir(parents=True, exist_ok=True)
    for (source, name), entry in zip(libs, entries):
        shutil.copyfile(qairt / source, dest / name)
        if sha256(dest / name) != entry["sha256"]:
            raise SystemExit(f"Copy of {name} does not match its source")
    (dest.parent / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    for entry in entries:
        print(f"{entry['sha256']}  {entry['name']}")
    print(f"Staged QAIRT {version} into {dest} (git-ignored, {args.flavor} flavor).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
