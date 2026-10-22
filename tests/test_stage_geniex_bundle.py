"""scripts/stage_geniex_bundle.py: the Qualcomm GENIE-X QAIRT bundle is pinned, unpacked safely,
verified file by file, kept out of git, and pushed to a phone only after the phone's hashes match."""
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("stage_geniex_bundle", ROOT / "scripts/stage_geniex_bundle.py")
stage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stage)

DIRECTORY = "fixture_bundle-geniex_qairt-w4a16"
FILES = {"genie_config.json": b'{"dialog": {}}', "part1_of_2.bin": b"\x01" * 300, "part2_of_2.bin": b"\x02" * 200,
         "tokenizer.json": b"{}"}


def fixture_bundle(files=FILES):
    """A pin set for a small bundle, shaped like stage.BUNDLE."""
    pins = {name: (len(data), hashlib.sha256(data).hexdigest()) for name, data in files.items()}
    bundle = copy.deepcopy(stage.BUNDLE)
    bundle.update(directory=DIRECTORY, files=pins,
                  bundleSha256=stage.bundle_digest({name: digest for name, (_, digest) in pins.items()}),
                  bundleBytes=sum(size for size, _ in pins.values()))
    return bundle


def write_zip(path, files=FILES, directory=DIRECTORY, extra=()):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(directory + "/", b"")
        for name, data in files.items():
            archive.writestr(f"{directory}/{name}", data)
        for info, data in extra:
            archive.writestr(info, data)
    bundle = fixture_bundle(files)
    data = Path(path).read_bytes()
    bundle["zip"] = {"name": Path(path).name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    return bundle


class PinTests(unittest.TestCase):
    def test_the_pins_are_consistent(self):
        bundle = stage.BUNDLE
        hashes = {name: digest for name, (_, digest) in bundle["files"].items()}
        self.assertEqual(stage.bundle_digest(hashes), bundle["bundleSha256"])
        self.assertEqual(sum(size for size, _ in bundle["files"].values()), bundle["bundleBytes"])
        self.assertEqual(bundle["zip"]["name"], bundle["directory"] + ".zip")
        self.assertTrue(bundle["url"].endswith("/" + bundle["zip"]["name"] + "?versionId=Yb0NrZDlpe5O5Hc3SM4NUGSYelSDiEle"))
        self.assertIn("/releases/" + bundle["aiHubRelease"] + "/", bundle["url"])
        self.assertIn("genie_config.json", bundle["files"])
        # The directory name is the NPU run's modelFile (config.ts BUNDLE_DIRECTORY).
        self.assertRegex(bundle["directory"], r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
        for digest in hashes.values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_the_pins_agree_with_the_model_manifest(self):
        """configs/models/experimental-models.json pins the zip (what is downloaded) and the bundle
        directory (what the app loads and a configuration's modelSha256 names)."""
        manifest = json.loads((ROOT / "configs/models/experimental-models.json").read_text())
        pinned = {entry["file"]: entry for entry in manifest["pinnedArtifacts"]}
        bundle = stage.BUNDLE
        archive = pinned[bundle["zip"]["name"]]
        self.assertEqual((archive["size"], archive["sha256"], archive["downloadUrl"], archive["revision"],
                          archive["qairt"]),
                         (bundle["zip"]["size"], bundle["zip"]["sha256"], bundle["url"], bundle["repoRevision"],
                          bundle["qairt"]))
        directory = pinned[bundle["directory"]]
        self.assertEqual((directory["size"], directory["sha256"], directory["sourceFile"], directory["revision"]),
                         (bundle["bundleBytes"], bundle["bundleSha256"], bundle["zip"]["name"], bundle["repoRevision"]))
        models_md = (ROOT / "MODELS.md").read_text()
        for value in (bundle["zip"]["sha256"], bundle["bundleSha256"], "Yb0NrZDlpe5O5Hc3SM4NUGSYelSDiEle"):
            self.assertIn(value, models_md)

    def test_the_digest_is_the_sha256sum_listing_in_byte_order(self):
        hashes = {"tokenizer_config.json": "b" * 64, "tokenizer.json": "a" * 64}
        self.assertEqual(stage.listing(hashes), f"{'a' * 64}  tokenizer.json\n{'b' * 64}  tokenizer_config.json\n")
        # The app computes the same value (xebenchapp/src/harness/bundle.ts bundleListing).
        source = (ROOT / "xebenchapp/src/harness/bundle.ts").read_text()
        self.assertIn("`${entry.sha256}  ${entry.name}\\n`", source)


class UnpackTests(unittest.TestCase):
    def test_a_pinned_zip_unpacks_and_verifies(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "bundle.zip"
            bundle = write_zip(path)
            stage.verify_zip(path, bundle)
            target = stage.unpack(path, Path(root) / "out", bundle, out=lambda _: None)
            self.assertEqual(sorted(entry.name for entry in target.iterdir()), sorted(FILES))
            hashes = stage.verify_directory(target, bundle)
            listing = "".join(f"{hashlib.sha256(FILES[name]).hexdigest()}  {name}\n" for name in sorted(FILES))
            self.assertEqual(stage.bundle_digest(hashes), hashlib.sha256(listing.encode()).hexdigest())
            manifest = json.loads((Path(root) / "out" / stage.MANIFEST).read_text())
            self.assertEqual(manifest["bundleSha256"], bundle["bundleSha256"])
            self.assertEqual([item["name"] for item in manifest["files"]], sorted(FILES))
            self.assertFalse((Path(root) / "out" / (DIRECTORY + ".partial")).exists())

    def test_unsafe_or_unpinned_members_are_refused(self):
        link = zipfile.ZipInfo(f"{DIRECTORY}/link")
        link.external_attr = 0o120777 << 16
        cases = {
            "outside": [(zipfile.ZipInfo("../evil.bin"), b"x")],
            "absolute": [(zipfile.ZipInfo("/tmp/evil.bin"), b"x")],
            "other directory": [(zipfile.ZipInfo("other/evil.bin"), b"x")],
            "nested": [(zipfile.ZipInfo(f"{DIRECTORY}/sub/evil.bin"), b"x")],
            "unpinned": [(zipfile.ZipInfo(f"{DIRECTORY}/evil.so"), b"x")],
            "link": [(link, b"/etc/passwd")],
        }
        for name, extra in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as root:
                path = Path(root) / "bundle.zip"
                bundle = write_zip(path, extra=extra)
                with self.assertRaises(stage.StageError):
                    stage.unpack(path, Path(root) / "out", bundle, out=lambda _: None)
                self.assertFalse((Path(root) / "out" / DIRECTORY).exists())
                self.assertFalse((Path(root) / "evil.bin").exists())

    def test_a_missing_or_different_file_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "bundle.zip"
            bundle = write_zip(path, {**FILES, "part2_of_2.bin": b"\x03" * 200})
            bundle["files"] = fixture_bundle()["files"]
            bundle["bundleSha256"] = fixture_bundle()["bundleSha256"]
            with self.assertRaisesRegex(stage.StageError, "part2_of_2.bin"):
                stage.unpack(path, Path(root) / "out", bundle, out=lambda _: None)
            partial = dict(FILES)
            del partial["tokenizer.json"]
            bundle = write_zip(path, partial)
            bundle["files"] = fixture_bundle()["files"]
            with self.assertRaisesRegex(stage.StageError, "lacks tokenizer.json"):
                stage.unpack(path, Path(root) / "out", bundle, out=lambda _: None)

    def test_a_wrong_zip_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "bundle.zip"
            bundle = write_zip(path)
            bundle["zip"]["sha256"] = "0" * 64
            with self.assertRaisesRegex(stage.StageError, "expected"):
                stage.verify_zip(path, bundle)

    def test_a_download_is_kept_only_when_it_matches(self):
        data = b"zip bytes"
        expected = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "bundle.zip"
            stage.download("https://example.invalid/x.zip", target, expected, opener=lambda url: io.BytesIO(data))
            self.assertEqual(target.read_bytes(), data)
            other = Path(root) / "other.zip"
            with self.assertRaises(stage.StageError):
                stage.download("https://example.invalid/x.zip", other, expected,
                               opener=lambda url: io.BytesIO(data + b"!"))
            self.assertEqual(list(Path(root).iterdir()), [target])

    def test_the_bundle_never_goes_where_git_tracks_files(self):
        self.assertFalse(stage.untracked_location(ROOT / "scripts" / DIRECTORY))
        self.assertTrue(stage.untracked_location(ROOT / "models/geniex" / DIRECTORY))
        with tempfile.TemporaryDirectory() as root:
            self.assertTrue(stage.untracked_location(Path(root) / DIRECTORY))
        self.assertEqual(stage.main(["--check", "--dest", str(ROOT / "scripts")], out=lambda _: None), 2)


class FakePhone:
    """adb over a dict of device files: push, sh -c 'cd … && sha256sum -- *', rm -rf, mkdir -p, mv."""

    def __init__(self, corrupt=None):
        self.files, self.corrupt, self.calls = {}, corrupt, []

    def __call__(self, argv, capture_output, text):
        args = argv[3:]
        self.calls.append(args)
        out, code = "", 0
        if args[0] == "push":
            data = Path(args[1]).read_bytes()
            self.files[args[2]] = data + b"!" if self.corrupt and args[2].endswith(self.corrupt) else data
        elif args[0] == "shell":
            command = args[1]
            directory = re.search(r"cd (\S+?) &&", command)
            if directory:
                prefix = directory[1].strip("'") + "/"
                found = {path[len(prefix):]: data for path, data in self.files.items() if path.startswith(prefix)}
                out = "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in sorted(found.items()))
                code = 0 if found else 1
            elif command.startswith("rm -rf "):
                prefix = command.split()[-1] + "/"
                self.files = {path: data for path, data in self.files.items() if not path.startswith(prefix)}
            elif command.startswith("mv "):
                source, target = command.split()[1:3]
                self.files = {(target + path[len(source):] if path.startswith(source + "/") else path): data
                              for path, data in self.files.items()}
        return subprocess.CompletedProcess(argv, code, out, "")


class PushTests(unittest.TestCase):
    def unpacked(self, root):
        path = Path(root) / "bundle.zip"
        bundle = write_zip(path)
        return stage.unpack(path, Path(root) / "out", bundle, out=lambda _: None), bundle

    def test_push_renames_only_after_every_phone_hash_matches(self):
        with tempfile.TemporaryDirectory() as root:
            directory, bundle = self.unpacked(root)
            phone = FakePhone()
            target = stage.push(directory, stage.Adb("SERIAL1", runner=phone), bundle, out=lambda _: None)
            self.assertEqual(target, f"{stage.DEVICE_DIR}/{DIRECTORY}")
            self.assertEqual(sorted(path.rsplit("/", 1)[1] for path in phone.files), sorted(FILES))
            self.assertTrue(all(path.startswith(target + "/") for path in phone.files))
            # A second push finds the verified bundle and copies nothing.
            pushes = sum(1 for call in phone.calls if call[0] == "push")
            stage.push(directory, stage.Adb("SERIAL1", runner=phone), bundle, out=lambda _: None)
            self.assertEqual(sum(1 for call in phone.calls if call[0] == "push"), pushes)

    def test_a_corrupted_copy_is_never_renamed_into_place(self):
        with tempfile.TemporaryDirectory() as root:
            directory, bundle = self.unpacked(root)
            phone = FakePhone(corrupt="part1_of_2.bin")
            with self.assertRaisesRegex(stage.StageError, "do not match"):
                stage.push(directory, stage.Adb("SERIAL1", runner=phone), bundle, out=lambda _: None)
            self.assertEqual(phone.files, {})

    def test_serials_are_plain(self):
        with self.assertRaises(stage.StageError):
            stage.Adb("serial; rm -rf /")


if __name__ == "__main__":
    unittest.main()
