"""scripts/stage_qairt_libs.py: the geniex flavor stages the QAIRT build of every QNN runtime file
geniex-android 0.8.0 packages, and app/build.gradle packages the staged copies in their place."""
from fnmatch import fnmatchcase
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("stage_qairt_libs", ROOT / "scripts/stage_qairt_libs.py")
stage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stage)

# The QNN runtime files in geniex-android-0.8.0.aar (jni/arm64-v8a), which carry QAIRT 2.45.0.
AAR_QNN = {
    "libCalculator_skel.so", "libcalculator.so", "libhta_hexagon_runtime_qnn.so", "libhta_hexagon_runtime_snpe.so",
    "libPlatformValidatorShared.so", "libQnnChrometraceProfilingReader.so", "libQnnHtaNetRunExtensions.so",
    "libQnnHta.so", "libQnnHtpNetRunExtensions.so", "libQnnHtpOptraceProfilingReader.so", "libQnnHtpPrepare.so",
    "libQnnHtpProfilingReader.so", "libQnnHtp.so", "libQnnHtpV79CalculatorStub.so", "libQnnHtpV79Skel.so",
    "libQnnHtpV79.so", "libQnnHtpV79Stub.so", "libQnnHtpV81CalculatorStub.so", "libQnnHtpV81Skel.so",
    "libQnnHtpV81.so", "libQnnHtpV81Stub.so", "libQnnIr.so", "libQnnJsonProfilingReader.so", "libQnnModelDlc.so",
    "libQnnNetRunDirectV79Skel.so", "libQnnNetRunDirectV79Stub.so", "libQnnNetRunDirectV81Skel.so",
    "libQnnNetRunDirectV81Stub.so", "libQnnSaver.so", "libQnnSystem.so",
}


def fake_sdk(root, libs, version="2.50.0"):
    sdk = Path(root) / "qairt"
    for source, _ in libs:
        path = sdk / source
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"{source} {version}".encode())
    (sdk / "sdk.yaml").write_text(f"name: qairt\nversion: {version}\n")
    return sdk


class GenieXFlavorTests(unittest.TestCase):
    def test_it_replaces_every_qnn_file_the_aar_packages(self):
        libs = stage.FLAVORS["geniex"]["libs"]
        self.assertEqual({name for _, name in libs}, AAR_QNN)
        self.assertEqual(len(libs), len(AAR_QNN))
        for source, name in libs:
            self.assertTrue(source.endswith("/" + name), source)
            # DSP skels and v79/v81 HTP libraries come from the Hexagon folders, the rest from the host one.
            dsp = name.endswith("Skel.so") or name.endswith("_skel.so") or re.fullmatch(r"libQnnHtpV[0-9]+\.so", name)
            self.assertTrue(source.startswith("lib/hexagon-v" if dsp else "lib/aarch64-android/"), source)
        self.assertEqual(stage.FLAVORS["geniex"]["dest"], ROOT / "xebenchapp/android/app/src/geniex/jniLibs/arm64-v8a")

    def test_build_gradle_packages_the_staged_copies_first_and_unstripped(self):
        gradle = (ROOT / "xebenchapp/android/app/build.gradle").read_text()
        block = re.search(r"def geniexQairtRuntime = \[(.*?)\]", gradle, re.S)[1]
        patterns = re.findall(r'"([^"]+)"', block)
        keep = re.findall(r'"([^"]+)"', re.search(r"keepDebugSymbols \+= \[(.*?)\]", gradle, re.S)[1])
        for _, name in stage.FLAVORS["geniex"]["libs"]:
            entry = "lib/arm64-v8a/" + name
            self.assertTrue(any(fnmatchcase(entry, pattern) for pattern in patterns), name)
            self.assertTrue(any(fnmatchcase(entry, pattern) for pattern in keep), name)
        self.assertIn("variant.packaging.jniLibs.pickFirsts.addAll(geniexQairtRuntime)", gradle)

    def test_staging_copies_records_and_checks(self):
        libs = stage.FLAVORS["geniex"]["libs"]
        with tempfile.TemporaryDirectory() as root, mock.patch.object(stage, "ignored_by_git", return_value=True):
            sdk = fake_sdk(root, libs)
            dest = Path(root) / "jniLibs/arm64-v8a"
            args = ["--qairt", str(sdk), "--flavor", "geniex", "--dest", str(dest)]
            with mock.patch("builtins.print"):
                self.assertEqual(stage.main(args), 0)
                self.assertEqual(stage.main(args + ["--check"]), 0)
            manifest = json.loads((dest.parent / stage.MANIFEST).read_text())
            self.assertEqual((manifest["flavor"], manifest["qairtVersion"]), ("geniex", "2.50.0"))
            self.assertEqual(sorted(entry["name"] for entry in manifest["libraries"]), sorted(AAR_QNN))
            (dest / "libQnnSystem.so").write_bytes(b"the 2.45 copy")
            with mock.patch("builtins.print"):
                self.assertEqual(stage.main(args + ["--check"]), 1)

    def test_another_qairt_or_a_tracked_destination_is_refused(self):
        libs = stage.FLAVORS["geniex"]["libs"]
        with tempfile.TemporaryDirectory() as root:
            sdk = fake_sdk(root, libs, version="2.45.0")
            with mock.patch.object(stage, "ignored_by_git", return_value=True), self.assertRaises(SystemExit):
                stage.main(["--qairt", str(sdk), "--flavor", "geniex", "--dest", str(Path(root) / "out")])
            sdk = fake_sdk(root, libs)
            with mock.patch.object(stage, "ignored_by_git", return_value=False), \
                    self.assertRaisesRegex(SystemExit, "git would track it"):
                stage.main(["--qairt", str(sdk), "--flavor", "geniex", "--dest", str(ROOT / "scripts")])

    def test_the_experimental_flavor_is_unchanged(self):
        self.assertEqual(stage.FLAVORS["experimental"]["libs"], stage.LIBS)
        self.assertEqual(stage.DEFAULT_DEST, ROOT / "xebenchapp/android/app/src/experimental/jniLibs/arm64-v8a")


if __name__ == "__main__":
    unittest.main()
