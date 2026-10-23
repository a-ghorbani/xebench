import copy
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/export_evidence.py"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


capture = load("capture_app_run", ROOT / "scripts/capture_app_run.py")
evidence = load("export_evidence", SCRIPT)
raw_session = evidence.raw_session
# The privacy/determinism tests below were written against cpu-reference-v1 records
# (frozen fixture); cpu-reference-v2 app output is covered by ConditionsV2ExportTests.
APP = json.loads((ROOT / "tests/fixtures/raw-session/app-emitted-v1.json").read_text())["records"]
# Current app output (cpu-reference-v3) and the frozen cpu-reference-v2 output it extends.
APP_V2 = json.loads((ROOT / "tests/fixtures/raw-session/app-emitted.json").read_text())["records"]
FROZEN_V2 = json.loads((ROOT / "tests/fixtures/raw-session/app-emitted-v2.json").read_text())["records"]

# Planted private values. None of them may appear anywhere in a bundle.
SERIAL = "R5CX21PLANTED7"
ANDROID_ID = "3f9a1c2b7d4e8f60"
EMAIL = "alice.operator@example.com"
TOKEN = "ghp_PlantedTokenAbCdEf0123456789xyz"
INSTALL_PATH = "/data/app/~~PlantedZx9==/com.xebenchapp-AbC==/base.apk"
HOST_PATH = "/home/alice-planted/xebench/results"
USERNAME = "alice-planted"
LOG_TEXT = "PLANTED-LOG FATAL EXCEPTION: main"
CODENAME = "plantedcodename"
EXTRA_KEY = "plantedExtraField"
NEEDLES = (SERIAL, ANDROID_ID, EMAIL, TOKEN, INSTALL_PATH, "/data/app", HOST_PATH, "/home/", USERNAME, LOG_TEXT,
           CODENAME, EXTRA_KEY, "FATAL", "example.com", "ghp_")
HOST_DEVICE = {"manufacturer": "Google", "model": "Pixel 9", "soc": "Tensor G4", "device": CODENAME,
               "serial": SERIAL, "androidId": ANDROID_ID, "user": EMAIL}
APKS = {"status": "recorded", "apks": [{"name": "base.apk", "sha256": "b" * 64},
                                       {"name": "split_config.arm64_v8a.apk", "sha256": "c" * 64}]}


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def fixture(name, run_id):
    record = copy.deepcopy(APP[name])
    record["runId"] = run_id
    return record


def planted(name, run_id):
    """A valid raw-session-v1 record with private values in every open location."""
    record = fixture(name, run_id)
    record["protocol"][EXTRA_KEY] = SERIAL
    record["protocol"]["installPath"] = INSTALL_PATH
    record["protocol"]["prompt"] += f" {EMAIL} {TOKEN}"
    record["configuration"]["hostPath"] = HOST_PATH
    record["configuration"]["runs"][0]["operatorToken"] = TOKEN
    record["conditionPolicy"]["operator"] = EMAIL
    for attempt in record["coldRuns"]:
        conditions = attempt["conditions"]
        conditions["before"]["androidId"] = ANDROID_ID
        conditions["before"]["serialNo"] = SERIAL
        conditions["before"]["unavailable"] = conditions["before"]["unavailable"] + [SERIAL]
        conditions["after"]["user"] = USERNAME
        conditions["beforeAssessment"]["log"] = LOG_TEXT
        conditions["afterAssessment"]["reasons"] = conditions["afterAssessment"]["reasons"] + [USERNAME]
        conditions[EXTRA_KEY] = {"nested": HOST_PATH}
        if attempt["status"] == "failed":
            attempt["error"]["message"] = f"{LOG_TEXT} {INSTALL_PATH} {EMAIL} {SERIAL}"
            if "cleanupError" in attempt:
                attempt["cleanupError"]["message"] = f"{LOG_TEXT} {HOST_PATH}"
    record["guardNotes"] = record["guardNotes"] + [f"{LOG_TEXT} {EMAIL}", SERIAL, USERNAME]
    assert raw_session.validate(record) == [], raw_session.validate(record)
    return record


def make_capture(root, records, device_info=None, inline=(), checkpoints=(), extra_files=None, backend_log=None):
    """backend_log: a logcat -v epoch snapshot of process 123 the capture reads as the app's backend log."""
    session = capture.Capture(root, dict(device_info or HOST_DEVICE))
    files, lines = dict(extra_files or {}), []
    for index, record in enumerate(list(checkpoints) + list(records)):
        name = f"{record['runId']}-{len(record['coldRuns'])}.json"
        files[name] = json.dumps(record)
        reference = {"name": name, "sha256": sha(files[name])}
        lines.append(f"{100 + index}.000 123 123 I ReactNativeJS: XEBENCH_RESULT_FILE {json.dumps(reference)}")
    lines.extend(f"200.000 123 123 I ReactNativeJS: {line}" for line in [*inline, "XEBENCH_DONE"])
    session.ingest("\n".join(lines), files.__getitem__)
    if backend_log is not None:
        session.ingest_backend_log(backend_log, 123)
    session.app_identity.update(before=APKS, after=APKS, consistency="matched")
    # Mirrors capture_device: complete only without errors, rejections or failed records.
    session.finish("partial" if session.errors or session.invalid or session.failed_records else "complete")
    return session.directory


def make_planted_capture(root):
    secret_file = json.dumps({"schema": "xebench-raw", "serial": SERIAL, "note": LOG_TEXT})
    inline = [
        f"XEBENCH_ERROR {LOG_TEXT} {INSTALL_PATH} {EMAIL} {TOKEN}",
        f'XEBENCH_RESULT {{"schema": "xebench-raw", "androidId": "{ANDROID_ID}", "path": "{HOST_PATH}"',
        f'XEBENCH_RESULT {{"schema": "xebench-raw", "{EXTRA_KEY}": "{USERNAME}"}}',
        # A file reference whose bytes disagree with the claim is kept locally as an invalid payload.
        "XEBENCH_RESULT_FILE " + json.dumps({"name": "secret.json", "sha256": "0" * 64}),
    ]
    directory = make_capture(root, [planted("complete", "1767225600000-0-planted"),
                                    planted("failed", "1767225600000-1-planted"),
                                    planted("cleanupFailed", "1767225600000-2-planted"),
                                    planted("flagged", "1767225600000-3-planted")],
                             inline=inline, checkpoints=[planted("partial", "1767225600000-4-planted")],
                             extra_files={"secret.json": secret_file})
    (directory / "startup-logcat.txt").write_text(f"{LOG_TEXT}\n{INSTALL_PATH}\n{SERIAL}\n{EMAIL}\n")
    (directory / "operator-notes.txt").write_text(f"{USERNAME} {TOKEN} {HOST_PATH}\n")
    (directory / "invalid-extra.txt").write_text(secret_file)
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest.update({"hostUser": USERNAME, "outputDir": HOST_PATH, "adbSerial": SERIAL, EXTRA_KEY: TOKEN})
    for rejection in manifest["rejections"]:
        rejection["detail"] = f"{LOG_TEXT} {EMAIL}"
    manifest_path.write_text(json.dumps(manifest))
    return directory


def bundle_bytes(bundle):
    return {str(path.relative_to(bundle)): path.read_bytes() for path in sorted(Path(bundle).rglob("*"))
            if path.is_file()}


class PrivacyTests(unittest.TestCase):
    def test_planted_private_values_never_reach_the_bundle(self):
        with tempfile.TemporaryDirectory() as root:
            directory = make_planted_capture(Path(root) / "results")
            # Precondition: the capture really does hold every planted value locally.
            local = b"".join(path.read_bytes() for path in directory.iterdir())
            for needle in NEEDLES:
                self.assertIn(needle.encode(), local, needle)
            report = evidence.export([directory], Path(root) / "exports")
            files = bundle_bytes(report["bundle"])
            self.assertEqual(len([path for path in files if path.startswith("sessions/")]), 4)
            for path, data in files.items():
                for needle in NEEDLES:
                    with self.subTest(path=path, needle=needle):
                        self.assertNotIn(needle.encode(), data)
                        self.assertNotIn(needle.lower().encode(), data.lower())
                        self.assertNotIn(needle, path)
                self.assertNotIn(b"planted-log", data.lower())
                self.assertNotIn(str(root).encode(), data)
            # Dropped fields are reported locally, never passed through.
            dropped = {(item["path"], item["kind"]) for item in report["dropped"]}
            for expected in [(f"/protocol/{EXTRA_KEY}", "unknown-field"), ("/protocol/prompt", "excluded"),
                             ("/configuration/hostPath", "unknown-field"), ("/deviceInfo/serial", "unknown-field"),
                             ("/deviceInfo/device", "excluded"), ("/coldRuns/1/error/message", "excluded"),
                             ("/coldRuns/0/conditions/before/androidId", "unknown-field"),
                             ("/guardNotes/1", "unrecognized-value"), ("/hostUser", "unknown-field")]:
                self.assertIn(expected, dropped)

    def test_approved_fields_keep_identity_and_only_counts_of_rejections(self):
        with tempfile.TemporaryDirectory() as root:
            directory = make_planted_capture(Path(root) / "results")
            report = evidence.export([directory], Path(root) / "exports")
            bundle = Path(report["bundle"])
            session = json.loads((bundle / "sessions/1767225600000-0-planted.json").read_text())
            self.assertEqual(session["device"], {"manufacturer": "Google", "model": "Fixture", "soc": "Tensor G4",
                                                 "androidRelease": "16"})
            record = session["record"]
            self.assertEqual(record["artifact"], APP["complete"]["artifact"])
            self.assertEqual(record["protocol"]["promptSha256"],
                             sha(APP["complete"]["protocol"]["prompt"] + f" {EMAIL} {TOKEN}"))
            self.assertNotIn("prompt", record["protocol"])
            self.assertEqual(record["coldRuns"][0]["conditions"]["before"]["batteryTempC"], 30.5)
            self.assertEqual(record["coldRuns"][0]["conditions"]["before"]["unavailable"], ["unrecognized"])
            self.assertEqual(record["guardNotes"], APP["complete"]["guardNotes"] + ["unrecognized"] * 3)
            self.assertEqual([run["status"] for run in record["coldRuns"]], ["complete"] * 4)
            failed = json.loads((bundle / "sessions/1767225600000-1-planted.json").read_text())["record"]
            self.assertEqual(failed["coldRuns"][-1]["error"], {"phase": "load"})
            captured = json.loads(next((bundle / "captures").iterdir()).read_text())
            self.assertEqual(captured["rejections"], {"count": 3, "codes": {
                "json.parse": 1, "schema.required": 1, "transport.checksum": 1}})
            self.assertEqual((captured["engineErrors"], captured["checkpointCount"]), (1, 1))
            self.assertEqual(captured["appIdentity"]["after"], APKS)
            self.assertEqual(captured["device"], {"manufacturer": "Google", "model": "Pixel 9", "soc": "Tensor G4"})
            # backendLog is present only when the capture read the app's backend log (backend_log.py).
            self.assertEqual(sorted(captured), sorted(set(evidence.CAPTURE_FILE.fields) - {"backendLog"}))

    def test_private_values_in_approved_fields_refuse_the_export(self):
        def model_token(record):
            record["model"] = record["configuration"]["runs"][0]["model"] = f"Llama {TOKEN}"
        # name: (change, host device facts, planted value, the check expected to refuse)
        cases = {
            "email in engine version": (
                lambda r: r.update(engineVersion=f"llama.rn {EMAIL}"), None, EMAIL, "not a label"),
            "token in model label": (model_token, None, TOKEN, "looks like token"),
            "android id in run id": (
                lambda r: r.update(runId=f"1767225600000-0-{ANDROID_ID}"), None, ANDROID_ID, "looks like android-id"),
            "path as manufacturer": (
                lambda r: None, {**HOST_DEVICE, "manufacturer": HOST_PATH}, HOST_PATH, "not a device property"),
            "serial-like digits in a status": (
                lambda r: r["coldRuns"][0]["conditions"]["before"].update(probeStatus=SERIAL), None, SERIAL,
                "not a lowercase enumerated value"),
        }
        for name, (change, device, needle, check) in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as root:
                record = fixture("complete", "1767225600000-0-fixture")
                change(record)
                self.assertEqual(raw_session.validate(record), [])
                directory = make_capture(Path(root) / "results", [record], device_info=device)
                out = Path(root) / "exports"
                with self.assertRaises(evidence.Refused) as caught:
                    evidence.export([directory], out)
                self.assertNotIn(needle, str(caught.exception))
                self.assertIn(check, str(caught.exception))
                self.assertFalse(out.exists() and any(out.iterdir()))

    def test_privacy_screen_names_patterns_not_values(self):
        findings = evidence.privacy_findings({"a": [f"x {EMAIL}"], "b": {"c": TOKEN}, "d": "10.0.2.2:5555",
                                              "e": "emulator-5554", "f": "C:/Users/x", "g": "a" * 64})
        self.assertEqual(findings, ["/a/0: looks like email", "/b/c: looks like token",
                                    "/d: looks like network-serial", "/e: looks like network-serial",
                                    "/f: looks like path"])


class FailClosedTests(unittest.TestCase):
    def export_fails(self, directories, message):
        with tempfile.TemporaryDirectory() as out:
            with self.assertRaises(evidence.Refused) as caught:
                evidence.export(directories, out)
            self.assertIn(message, str(caught.exception))
            self.assertEqual(list(Path(out).iterdir()), [])

    def raw_file(self, directory):
        return next(directory.glob("raw-*.jsonl"))

    def test_tampered_and_invalid_records_are_refused(self):
        with tempfile.TemporaryDirectory() as root:
            directory = make_capture(Path(root), [fixture("complete", "1767225600000-0-fixture")])
            path = self.raw_file(directory)
            original = path.read_bytes()
            path.write_bytes(original.replace(b'"loadMs": 17', b'"loadMs": 18', 1))
            self.export_fails([directory], "do not match the capture checksum")
            # Even with a consistent checksum, the record must re-validate.
            manifest = json.loads((directory / "manifest.json").read_text())
            manifest["records"][0]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            (directory / "manifest.json").write_text(json.dumps(manifest))
            self.export_fails([directory], "summary.mismatch")
            path.write_bytes(original[:-20])
            manifest["records"][0]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            (directory / "manifest.json").write_text(json.dumps(manifest))
            self.export_fails([directory], "Invalid JSON")

    def test_legacy_unlisted_missing_and_duplicate_inputs_are_refused(self):
        with tempfile.TemporaryDirectory() as root:
            legacy = capture.Capture(Path(root) / "legacy", {})
            legacy.ingest('1 I ReactNativeJS: XEBENCH_RESULT {"schema": "xebench-raw", "engine": "llama.cpp", '
                          '"backend": "cpu", "model": "m", "quant": "q", "deviceInfo": {}}')
            legacy.finish("complete")
            self.export_fails([legacy.directory], "Only raw-session-v1 records")

            directory = make_capture(Path(root) / "ok", [fixture("complete", "1767225600000-0-fixture")])
            self.export_fails([directory, directory], "Capture given more than once")
            other = make_capture(Path(root) / "other", [fixture("complete", "1767225600000-0-fixture")])
            self.export_fails([directory, other], "Session appears in more than one record")
            shutil.copy(self.raw_file(directory), directory / "raw-00000000-0000-0000-0000-000000000000.jsonl")
            self.export_fails([directory], "not listed in the manifest")

            empty = capture.Capture(Path(root) / "empty", {})
            empty.ingest("1 I ReactNativeJS: XEBENCH_RESULT {broken")
            empty.finish("partial")
            self.export_fails([empty.directory], "no terminal session record")
            (empty.directory / "manifest.json").unlink()
            self.export_fails([empty.directory], "Missing or non-regular file")

    def test_symlinked_record_and_complete_status_with_failures_are_refused(self):
        with tempfile.TemporaryDirectory() as root:
            directory = make_capture(Path(root) / "a", [fixture("complete", "1767225600000-0-fixture")])
            path = self.raw_file(directory)
            outside = Path(root) / "outside.jsonl"
            shutil.move(path, outside)
            path.symlink_to(outside)
            self.export_fails([directory], "Missing or non-regular file")
            directory = make_capture(Path(root) / "b", [fixture("failed", "1767225600000-0-fixture")])
            manifest_path = directory / "manifest.json"
            manifest_path.write_text(json.dumps({**json.loads(manifest_path.read_text()), "status": "complete"}))
            self.export_fails([directory], "A complete capture must be finished")

    def test_rejection_codes_are_a_closed_vocabulary(self):
        with tempfile.TemporaryDirectory() as root:
            directory = make_capture(Path(root), [fixture("complete", "1767225600000-0-fixture")],
                                     inline=["XEBENCH_RESULT {broken"])
            manifest_path = directory / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["rejections"][0]["codes"] = ["json.parse", "alice.corp.internal", USERNAME, 1]
            manifest_path.write_text(json.dumps(manifest))
            report = evidence.export([directory], Path(root) / "out")
            data = b"".join(bundle_bytes(report["bundle"]).values())
            self.assertNotIn(b"alice", data)
            captured = json.loads(next((Path(report["bundle"]) / "captures").iterdir()).read_text())
            self.assertEqual(captured["rejections"], {"count": 1, "codes": {"json.parse": 1, "unrecognized": 1}})
            self.assertEqual(sum(item["kind"] == "unrecognized-value" for item in report["dropped"]), 3)

    def test_vocabulary_covers_every_host_reason_code(self):
        source = (ROOT / "scripts/raw_session.py").read_text() + (ROOT / "scripts/capture_app_run.py").read_text()
        literals = set(re.findall(r'(?:reason|fail)\("([a-z-]+\.[a-z-]+)"', source))
        self.assertGreater(len(literals), 30)
        self.assertEqual(literals - evidence.REJECTION_CODES.values, set())

    def test_lowercase_words_in_enumerated_fields_are_a_closed_vocabulary(self):
        # A lowercase word passes the enum form check but can still be a username
        # or hostname; only app/host words may pass through.
        user = "aghorbaniplanted"
        prose = "operator aghorbaniplanted at home office, call 555 0100"
        with tempfile.TemporaryDirectory() as root:
            record = fixture("complete", "1767225600000-0-fixture")
            attempt = record["coldRuns"][0]
            attempt["conditions"]["before"]["probeStatus"] = user
            attempt["conditions"]["beforeAssessment"]["status"] = user
            record["conditionPolicy"]["mode"] = user
            record["protocol"]["coldDefinition"] = prose
            self.assertEqual(raw_session.validate(record), [])
            report = evidence.export([make_capture(Path(root), [record])], Path(root) / "out")
            data = b"".join(bundle_bytes(report["bundle"]).values())
            self.assertNotIn(user.encode(), data)
            self.assertNotIn(b"555 0100", data)
            exported = json.loads((Path(report["bundle"]) / "sessions/1767225600000-0-fixture.json").read_text())
            self.assertEqual(exported["record"]["conditionPolicy"]["mode"], "unrecognized")
            self.assertEqual(exported["record"]["protocol"]["coldDefinition"], "unrecognized")
            dropped = {(item["path"], item["kind"]) for item in report["dropped"]}
            for path in ("/coldRuns/0/conditions/before/probeStatus", "/coldRuns/0/conditions/beforeAssessment/status",
                         "/conditionPolicy/mode", "/protocol/coldDefinition"):
                self.assertIn((path, "unrecognized-value"), dropped)
            self.assertTrue(evidence.verify(report["bundle"])["valid"])

    def test_enum_vocabulary_covers_every_app_word(self):
        source = "".join(path.read_text() for path in (ROOT / "xebenchapp/src/harness").rglob("*.ts"))
        literals = {word for word in re.findall(r"['`]([a-z][a-z-]{0,63})['`]", source) if not word.endswith("-")}
        # Field names and typeof checks are not enumerated values.
        literals -= {"id", "model", "quant", "runs", "status", "order", "string", "number", "boolean", "object", "function",
                     "react-native", "react-native-executorch", "xebench-raw", "artifact", "init",
                     "gpu", "devices",  # LlamaContext field names in the Pick<> guard
                     # accelerator-v1 field names, and gate probe IDs/expected answers (exported as labels).
                     "backend", "purpose", "reasons", "libraries", "phase", "role", "report", "kind",
                     "source", "summary", "paris", "pelican", "short-capital", "long-fact-first", "long-fact-last",
                     "long-fact-middle", "measured-prompt",
                     # runRepetitions' internal executionReports option, never recorded.
                     "always", "when-reported",
                     # ExecutorchAdapter: module names, the worklet directive, field names, the program
                     # schema's dimension kinds and method name it reads (never recorded), and
                     # distribution.backends' value (a closed vocabulary of its own).
                     "react-native-worklets", "worklet", "program", "methods", "forward", "constant", "range",
                     "enum", "xnnpack",
                     # GENIE-X's stop reasons and plugin names, checked by GenieXAdapter, never recorded.
                     "eos", "length", "qairt"}
        self.assertGreater(len(literals), 30)
        self.assertEqual(literals - evidence.ENUM.values - evidence.REASON.values, set())
        self.assertIn("fresh engine instance in warm app process; artifact hashing warms OS file cache",
                      evidence.COLD_DEFINITION.values)
        self.assertIn(re.search(r"coldDefinition: '([^']+)'", source)[1], evidence.COLD_DEFINITION.values)

    def test_refusal_messages_do_not_echo_directory_names_or_run_ids(self):
        with tempfile.TemporaryDirectory() as root:
            run_id = f"1767225600000-0-{ANDROID_ID}"
            one = make_capture(Path(root) / EMAIL, [fixture("complete", run_id)])
            two = make_capture(Path(root) / "two", [fixture("complete", run_id)])
            for directories in ([one, two], [Path(root) / EMAIL / "missing"]):
                with self.assertRaises(evidence.Refused) as caught:
                    evidence.export(directories, Path(root) / "out")
                self.assertNotIn(ANDROID_ID, str(caught.exception))
                self.assertNotIn(EMAIL, str(caught.exception))

    def test_inconsistent_manifest_counts_are_refused(self):
        with tempfile.TemporaryDirectory() as root:
            directory = make_capture(Path(root), [fixture("failed", "1767225600000-0-fixture")],
                                     inline=["XEBENCH_RESULT {broken"])
            path = directory / "manifest.json"
            manifest = json.loads(path.read_text())
            for key, value, message in (("invalidRecords", 0, "invalidRecords"),
                                        ("failedRecords", 0, "failedRecords"),
                                        ("status", "certified", "Unknown capture status")):
                with self.subTest(key=key):
                    path.write_text(json.dumps({**manifest, key: value}))
                    self.export_fails([directory], message)


class DeterminismTests(unittest.TestCase):
    def test_same_input_gives_the_same_bundle_and_hash(self):
        with tempfile.TemporaryDirectory() as root:
            one = make_capture(Path(root) / "a", [fixture("complete", "1767225600000-0-one"),
                                                  fixture("failed", "1767225600000-1-one")])
            two = make_planted_capture(Path(root) / "b")
            first = evidence.export([one, two], Path(root) / "out1")
            for path in list(one.iterdir()) + list(two.iterdir()):
                os.utime(path, (1, 1))
            second = evidence.export([two, one], Path(root) / "out2" / "nested")
            again = evidence.export([one, two], Path(root) / "out1")
            self.assertEqual(first["evidence"], second["evidence"])
            self.assertEqual(again["bundle"], first["bundle"])
            self.assertEqual(bundle_bytes(first["bundle"]), bundle_bytes(second["bundle"]))
            digest = first["evidence"]["bundleSha256"]
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
            self.assertEqual(Path(first["bundle"]).name, digest)
            self.assertEqual(hashlib.sha256((Path(first["bundle"]) / "manifest.json").read_bytes()).hexdigest(), digest)
            self.assertEqual(first["evidence"]["sessionIds"], sorted(first["evidence"]["sessionIds"]))

    def test_canonical_form_is_fixed(self):
        self.assertEqual(evidence.canonical({"b": [1, 2.5, None], "a": "\u00e9"}), b'{"a":"\\u00e9","b":[1,2.5,null]}\n')
        with self.assertRaises(ValueError):
            evidence.canonical({"a": float("nan")})

    def test_different_input_gives_a_different_hash(self):
        with tempfile.TemporaryDirectory() as root:
            one = make_capture(Path(root) / "a", [fixture("complete", "1767225600000-0-one")])
            two = make_capture(Path(root) / "b", [fixture("complete", "1767225600000-0-two")])
            self.assertNotEqual(evidence.export([one], Path(root) / "out")["evidence"]["bundleSha256"],
                                evidence.export([two], Path(root) / "out")["evidence"]["bundleSha256"])


class VerifyTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        directory = make_capture(self.root / "results", [fixture("complete", "1767225600000-0-fixture"),
                                                         fixture("failed", "1767225600000-1-fixture")])
        self.report = evidence.export([directory], self.root / "exports")
        self.bundle = Path(self.report["bundle"])
        self.digest = self.report["evidence"]["bundleSha256"]

    def copy(self):
        target = self.root / "copy" / self.bundle.name
        shutil.rmtree(target.parent, ignore_errors=True)
        shutil.copytree(self.bundle, target)
        return target

    def rehash(self, bundle, path, document):
        """Rewrite a file and its manifest entry consistently, as a careful forger would."""
        data = evidence.canonical(document)
        (bundle / path).write_bytes(data)
        manifest = json.loads((bundle / "manifest.json").read_text())
        for item in manifest["files"]:
            if item["path"] == path:
                item.update(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
        (bundle / "manifest.json").write_bytes(evidence.canonical(manifest))
        renamed = bundle.parent / hashlib.sha256((bundle / "manifest.json").read_bytes()).hexdigest()
        bundle.rename(renamed)
        return renamed

    def test_fresh_bundle_verifies_and_medians_are_recomputed(self):
        result = evidence.verify(self.bundle, self.digest)
        self.assertTrue(result["valid"], result["errors"])
        complete = APP["complete"]
        for metric in raw_session.METRICS:
            values = [attempt[metric] for attempt in complete["coldRuns"]]
            self.assertEqual(result["sessions"]["1767225600000-0-fixture"]["median"][metric],
                             raw_session.aggregate(values)["median"])
        self.assertEqual(result["sessions"]["1767225600000-1-fixture"]["repetitions"], 2)

    def test_wrong_expected_hash_fails(self):
        result = evidence.verify(self.bundle, "0" * 64)
        self.assertFalse(result["valid"])
        self.assertIn("expected bundleSha256", result["errors"][0])

    def test_tampered_bytes_fail_their_checksum(self):
        bundle = self.copy()
        path = bundle / "sessions/1767225600000-0-fixture.json"
        path.write_bytes(path.read_bytes().replace(b'"decodeTps":30.125', b'"decodeTps":31.125', 1))
        self.assertIn("sessions/1767225600000-0-fixture.json: checksum or size mismatch",
                      evidence.verify(bundle)["errors"])

    def test_consistently_rehashed_statistics_still_fail_recomputation(self):
        path = "sessions/1767225600000-0-fixture.json"
        document = json.loads((self.bundle / path).read_text())
        document["record"]["coldRuns"][0]["decodeTps"] = 99.0
        bundle = self.rehash(self.copy(), path, document)
        result = evidence.verify(bundle)
        self.assertFalse(result["valid"])
        self.assertTrue(any("/summary/decodeTps/" in error for error in result["errors"]), result["errors"])

    def test_extra_fields_files_and_non_canonical_manifest_fail(self):
        path = "sessions/1767225600000-0-fixture.json"
        document = json.loads((self.bundle / path).read_text())
        document["record"]["protocol"]["serialNo"] = "R5CX"
        result = evidence.verify(self.rehash(self.copy(), path, document))
        self.assertIn(f"{path}/record/protocol/serialNo: field is not in the export allowlist", result["errors"])

        bundle = self.copy()
        (bundle / "sessions/notes.txt").write_text("extra")
        self.assertIn("sessions/notes.txt: file is not listed in the manifest", evidence.verify(bundle)["errors"])

        bundle = self.copy()
        manifest = json.loads((bundle / "manifest.json").read_text())
        (bundle / "manifest.json").write_text(json.dumps(manifest, indent=2))
        errors = evidence.verify(bundle)["errors"]
        self.assertIn("manifest.json: not in canonical form", errors)
        self.assertIn("manifest.json: SHA-256 differs from the bundle's content address", errors)

    def remanifest(self, bundle, change):
        manifest = json.loads((bundle / "manifest.json").read_text())
        change(manifest)
        (bundle / "manifest.json").write_bytes(evidence.canonical(manifest))
        renamed = bundle.parent / hashlib.sha256((bundle / "manifest.json").read_bytes()).hexdigest()
        bundle.rename(renamed)
        return renamed

    def session_doc(self, run="0"):
        return json.loads((self.bundle / f"sessions/1767225600000-{run}-fixture.json").read_text())

    def capture_path(self):
        return str(next((self.bundle / "captures").iterdir()).relative_to(self.bundle))

    def test_malformed_types_are_errors_not_crashes(self):
        path = "sessions/1767225600000-0-fixture.json"
        mutations = [
            lambda doc: doc.update(sessionId=["x"]),
            lambda doc: doc["record"]["coldRuns"][0].update(status=[]),
            lambda doc: doc["record"].update(coldRuns="x"),
            lambda doc: doc.update(record=None),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                document = self.session_doc()
                mutate(document)
                result = evidence.verify(self.rehash(self.copy(), path, document))
                self.assertFalse(result["valid"])
                self.assertFalse(any("malformed or unreadable" in error for error in result["errors"]),
                                 result["errors"])
        capture_path = self.capture_path()
        document = json.loads((self.bundle / capture_path).read_text())
        document["sessionIds"].append(["x"])
        self.assertFalse(evidence.verify(self.rehash(self.copy(), capture_path, document))["valid"])

    def test_forged_manifest_fields_fail(self):
        for change in (lambda m: m.update(publicationReady=True), lambda m: m.update(schemaVersion=True),
                       lambda m: m.update(hashRule="anything"), lambda m: m.pop("statistics"),
                       lambda m: m["files"][0].update(extra=1)):
            with self.subTest(change=change):
                result = evidence.verify(self.remanifest(self.copy(), change))
                self.assertFalse(result["valid"])
                self.assertTrue(all(error.startswith("manifest.json") for error in result["errors"]),
                                result["errors"])

    def test_capture_files_are_bound_to_their_id_and_counts(self):
        capture_path = self.capture_path()
        document = json.loads((self.bundle / capture_path).read_text())
        # A second capture document with the same ID under another name.
        bundle = self.copy()
        forged = {**document, "status": "complete", "engineErrors": 99}
        data = evidence.canonical(forged)
        (bundle / "captures/forged-name.json").write_bytes(data)
        bundle = self.remanifest(bundle, lambda m: (m["files"].insert(0, {
            "path": "captures/forged-name.json", "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}),
            m["files"].sort(key=lambda item: item["path"])))
        self.assertIn("captures/forged-name.json: file name does not match its capture ID",
                      evidence.verify(bundle)["errors"])
        for change, message in ((lambda d: d.update(failedRecords=5), "counts disagree"),
                                (lambda d: d["rejections"].update(count=9), "counts disagree"),
                                (lambda d: d["device"].update(soc="Other SoC"), "disagrees with its capture"),
                                (lambda d: d.update(sessionIds=d["sessionIds"][:1]), "does not match the sessions")):
            with self.subTest(message=message):
                changed = copy.deepcopy(document)
                change(changed)
                errors = evidence.verify(self.rehash(self.copy(), capture_path, changed))["errors"]
                self.assertTrue(any(message in error for error in errors), errors)

    def test_session_semantics_are_rechecked(self):
        path = "sessions/1767225600000-0-fixture.json"
        for change in (lambda r: r["coldRuns"][2].update(runIndex=5),
                       lambda r: r["protocol"].update(nColdRuns=3),
                       lambda r: r.update(status="failed")):
            with self.subTest(change=change):
                document = self.session_doc()
                change(document["record"])
                self.assertFalse(evidence.verify(self.rehash(self.copy(), path, document))["valid"])

    def test_empty_bundle_and_expected_sessions(self):
        bundle = self.copy()
        for path in list((bundle / "sessions").iterdir()) + list((bundle / "captures").iterdir()):
            path.unlink()
        bundle = self.remanifest(bundle, lambda m: m.update(files=[], sessionIds=[], captureIds=[]))
        self.assertEqual(evidence.verify(bundle)["errors"], ["bundle: holds no session"])
        self.assertTrue(evidence.verify(self.bundle, expected_sessions=["1767225600000-0-fixture"])["valid"])
        self.assertIn("bundle: expected session [0] is missing",
                      evidence.verify(self.bundle, expected_sessions=["1767225600000-9-other"])["errors"])

    def test_symlinked_bundle_content_fails(self):
        bundle = self.copy()
        target = bundle / "sessions/1767225600000-1-fixture.json"
        outside = self.root / "outside.json"
        shutil.move(target, outside)
        target.symlink_to(outside)
        self.assertIn("sessions/1767225600000-1-fixture.json: listed file is missing or not a regular file",
                      evidence.verify(bundle)["errors"])

    def test_missing_listed_file_fails(self):
        bundle = self.copy()
        (bundle / "sessions/1767225600000-1-fixture.json").unlink()
        self.assertIn("sessions/1767225600000-1-fixture.json: listed file is missing or not a regular file",
                      evidence.verify(bundle)["errors"])


class BranchRecordTests(unittest.TestCase):
    """Fields from the conditions-enforcement branch are optional allowlist entries."""

    def branch_record(self):
        record = fixture("complete", "1767225600000-0-fixture")
        record.update(harnessVersion="xebench-0.6-reference", repetitionsPlanned=4, stopReason=None,
                      runOrder={"method": "mulberry32-fisher-yates-v1", "seed": 123, "seedSource": "generated",
                                "order": ["llama32-q4km-cpu"], "position": 0},
                      chargingPolicy={"version": "charging-policy-v1",
                                      "publicationRequires": "external-power-disconnected",
                                      "effect": "publication-eligibility-only"},
                      conditionVerdict={"status": "unverified", "reasons": [], "charging": "unknown"})
        record["protocol"].update(version="cpu-reference-v2", readinessTimeoutSec=180,
                                  measurementKind="fresh-instance-first-request", warmRequests="not-measured",
                                  processRestartBetweenRepetitions=False)
        record["configuration"]["readinessTimeoutSec"] = 180
        record["conditionPolicy"] = {
            "version": "run-conditions-v2", "required": ["batteryPct", "focusLossCount"],
            "readiness": {"mode": "wait-then-skip", "pollSec": 5, "maxThermalStatus": 0, "maxBatteryTempC": 40,
                          "maxBatteryTempRiseC": 2, "maxThermalHeadroom": 0.8, "headroom": "applied-when-measured",
                          "headroomConfirmMs": 1500},
            "run": {"mode": "record-and-flag", "minBatteryPct": 50, "maxThermalStatus": 2,
                    "sampling": {"intervalMs": 1000, "maxDurationMs": 900000, "persistedSamples": 30,
                                 "maxGapFactor": 2, "signals": "cheap-native-reads-no-pss"},
                    "interruptions": "focus-loss-count"},
            "memory": "endpoint-pss-and-sampled-rss-not-peak"}
        sample = {**record["coldRuns"][0]["conditions"]["before"], "elapsedMs": 5000, "thermalHeadroom": 0.4,
                  "focusLossCount": 0, "processUptimeMs": 900, "rssMb": 512.5}
        for attempt in record["coldRuns"]:
            attempt.update(cooldownMs=30000, instance={"kind": "fresh-instance", "request": "first-request-on-instance",
                                                       "processInstanceId": "mabc123-k3j2h4g5",
                                                       "modelLoadsBeforeInProcess": attempt["runIndex"]},
                           readiness={"status": "ready", "waitedMs": 5000, "polls": 2, "timeoutSec": 180,
                                      "baselineBatteryTempC": 30.5, "headroomApplied": True,
                                      "notReady": {"battery-not-cooled": 1, USERNAME: 2}, "reasons": []})
            attempt["conditions"].update(
                before=sample, after=sample, duringAssessment={"status": "pass", "reasons": []},
                during={"status": "complete", "intervalMs": 1000, "startedElapsedMs": 5000, "stoppedElapsedMs": 9000,
                        "ticks": 4, "dropped": 0, "expired": False, "maxSampleCostMs": 3, "totalSampleCostMs": 9,
                        "summary": {"samples": 4, "incompleteSamples": 0, "failures": [], "maxThermalStatus": 0,
                                    "maxThermalHeadroom": 0.4, "maxBatteryTempC": 30.5, "minBatteryPct": 80,
                                    "maxRssMb": 512.5, "maxGapMs": 1000,
                                    "externalPower": {"connected": 0, "disconnected": 4, "unknown": 0}},
                        "samples": [sample]})
        record["coldRuns"].append({"runIndex": 4, "status": "skipped", "reason": "readiness-timeout",
                                   "cooldownMs": 30000, "instance": None,
                                   "readiness": {**record["coldRuns"][0]["readiness"], "status": "timeout"},
                                   "conditions": {"before": sample, "beforeAssessment": {"status": "pass",
                                                                                         "reasons": []},
                                                  "during": None, "duringAssessment": None, "after": None,
                                                  "afterAssessment": None}})
        return record

    def test_branch_fields_are_exported_and_extras_still_dropped(self):
        record = self.branch_record()
        record["coldRuns"][0]["readiness"][EXTRA_KEY] = HOST_PATH
        ctx = evidence.Context()
        record.pop("deviceInfo")
        out = evidence.RECORD.apply(record, "", ctx)
        kinds = {(item["path"], item["kind"]) for item in ctx.dropped}
        self.assertEqual(kinds, {("/protocol/prompt", "excluded"),
                                 (f"/coldRuns/0/readiness/{EXTRA_KEY}", "unknown-field"),
                                 *((f"/coldRuns/{index}/readiness/notReady/{USERNAME}", "unrecognized-key")
                                   for index in range(5))})
        for key in ("runOrder", "repetitionsPlanned", "stopReason", "chargingPolicy", "conditionVerdict"):
            self.assertEqual(out[key], record[key])
        self.assertEqual(out["conditionPolicy"], record["conditionPolicy"])
        first = out["coldRuns"][0]
        self.assertEqual(first["instance"], record["coldRuns"][0]["instance"])
        self.assertEqual(first["readiness"]["notReady"], {"battery-not-cooled": 1, "unrecognized": 2})
        self.assertEqual(first["conditions"]["during"], record["coldRuns"][0]["conditions"]["during"])
        self.assertEqual(out["coldRuns"][4]["status"], "skipped")
        self.assertEqual(evidence.privacy_findings(out), [])
        # The sanitized record also passes the verifier's statistics check.
        errors = []
        evidence._check_statistics(out, "", errors)
        self.assertEqual(errors, [])


class ConditionsV2ExportTests(unittest.TestCase):
    """End to end: conditions-enforcement app records -> capture -> raw_session -> export -> verify."""

    def records(self):
        terminal = [name for name, record in APP_V2.items() if record["status"] != "running"]
        result = []
        for index, name in enumerate(sorted(terminal)):
            record = copy.deepcopy(APP_V2[name])
            record["runId"] = f"1767225600000-{index}-v2"
            self.assertEqual(raw_session.validate(record), [], name)
            result.append((name, record))
        return result

    def test_every_app_record_exports_without_loss_and_verifies(self):
        named = self.records()
        with tempfile.TemporaryDirectory() as root:
            checkpoint = dict(APP_V2["partial"], runId="1767225600000-99-v2")
            directory = make_capture(Path(root) / "results", [record for _, record in named],
                                     checkpoints=[checkpoint])
            manifest = json.loads((directory / "manifest.json").read_text())
            self.assertEqual(manifest["invalidRecords"], 0)
            self.assertTrue(all(entry["validation"] == raw_session.VALIDATION for entry in manifest["records"]))
            report = evidence.export([directory], Path(root) / "out")
            # Only the prompt text and free-text error messages are withheld; no app field is
            # dropped as unknown or reduced to "unrecognized".
            # (The capture-level drops are the planted host device facts of HOST_DEVICE.)
            record_drops = [item for item in report["dropped"] if "/raw-" in item["source"]]
            self.assertEqual({item["kind"] for item in record_drops}, {"excluded"}, record_drops)
            self.assertEqual({item["path"].rsplit("/", 1)[-1] for item in record_drops},
                             {"prompt", "message", "signingCertSha256"})
            result = evidence.verify(report["bundle"], report["evidence"]["bundleSha256"])
            self.assertTrue(result["valid"], result["errors"])
            bundle = Path(report["bundle"])
            for name, record in named:
                exported = json.loads((bundle / f"sessions/{record['runId']}.json").read_text())["record"]
                with self.subTest(name=name):
                    for key in ("runOrder", "repetitionsPlanned", "stopReason", "chargingPolicy", "conditionVerdict",
                                "conditionPolicy", "guardNotes", "artifact"):
                        self.assertEqual(exported[key], record[key])
                    for raw, out in zip(record["coldRuns"], exported["coldRuns"]):
                        self.assertEqual(out["conditions"], raw["conditions"])
                        self.assertEqual(out["readiness"], raw["readiness"])
                        self.assertEqual(out.get("reason"), raw.get("reason"))
                    self.assertEqual(evidence.privacy_findings(exported), [])
            self.assertEqual(len(result["sessions"]), len(named))

    def test_new_charging_fields_reach_the_bundle(self):
        record = dict(copy.deepcopy(APP_V2["chargingDuringRun"]), runId="1767225600000-0-charge")
        before = record["coldRuns"][0]["conditions"]["before"]
        self.assertEqual((before["batteryStatus"], before["plugged"], before["currentNowUa"]), (4, 2, -1500))
        ctx = evidence.Context()
        record.pop("deviceInfo", None)
        out = evidence.RECORD.apply(record, "", ctx)
        self.assertEqual(out["coldRuns"][0]["conditions"]["before"]["currentNowUa"], -1500)
        self.assertEqual(out["coldRuns"][0]["conditions"]["during"]["summary"]["chargingStates"],
                         record["coldRuns"][0]["conditions"]["during"]["summary"]["chargingStates"])
        self.assertEqual(out["coldRuns"][0]["conditions"]["charging"], "violated")
        self.assertEqual({item["kind"] for item in ctx.dropped}, {"excluded"})

    def test_experiment_marker_reaches_the_bundle_and_verifies(self):
        """EXPERIMENT ONLY (docs/experiments/charging-ab.md): the record-only marker is never hidden."""
        record = dict(copy.deepcopy(APP_V2["chargingRecordOnly"]), runId="1767225600000-0-experiment")
        with tempfile.TemporaryDirectory() as root:
            report = evidence.export([make_capture(Path(root), [record])], Path(root) / "out")
            exported = json.loads((Path(report["bundle"]) / "sessions/1767225600000-0-experiment.json").read_text())
            self.assertEqual(exported["record"]["configuration"]["experimentChargingReadiness"], "record-only")
            self.assertEqual(exported["record"]["chargingPolicy"]["readiness"], "record-only-experiment")
            self.assertNotIn("unrecognized-value", {item["kind"] for item in report["dropped"]})
            self.assertTrue(evidence.verify(report["bundle"])["valid"])


class IdentityV3ExportTests(unittest.TestCase):
    """Build, configuration and execution identity: strict shapes and closed vocabularies only."""

    def export_one(self, record, root):
        directory = make_capture(Path(root) / "results", [record])
        report = evidence.export([directory], Path(root) / "out")
        bundle = Path(report["bundle"])
        result = evidence.verify(bundle, report["evidence"]["bundleSha256"])
        self.assertTrue(result["valid"], result["errors"])
        exported = json.loads((bundle / f"sessions/{record['runId']}.json").read_text())["record"]
        return report, exported, b"".join(bundle_bytes(bundle).values())

    def test_identity_fields_reach_the_bundle_in_closed_form(self):
        record = dict(copy.deepcopy(APP_V2["complete"]), runId="1767225600000-0-identity")
        with tempfile.TemporaryDirectory() as root:
            report, out, data = self.export_one(record, root)
        build = dict(record["build"])
        self.assertEqual(build.pop("signingCertSha256"), [evidence.REPOSITORY_DEBUG_CERT_SHA256])
        self.assertEqual(out["build"], {**build, "signingKey": "repository-debug-key"})
        self.assertNotIn(evidence.REPOSITORY_DEBUG_CERT_SHA256.encode(), data)
        self.assertEqual(out["configurationSource"], record["configurationSource"])
        self.assertEqual(out["effectiveSettings"], record["effectiveSettings"])
        self.assertEqual(out["artifact"], record["artifact"])
        self.assertEqual(out["configuration"]["runs"][0]["modelSha256"], record["artifact"]["expectedSha256"])
        report0 = out["executionReports"][0]
        self.assertEqual({key: report0[key] for key in ("runIndex", "gpu", "reasonNoGPU", "devices", "androidLib")},
                         {key: record["executionReports"][0][key] for key in
                          ("runIndex", "gpu", "reasonNoGPU", "devices", "androidLib")})
        # The free-text system_info line is never exported; only its parsed, closed form is.
        self.assertEqual(report0["systemInfo"], {"nThreads": 4, "nThreadsBatch": 4, "hardwareConcurrency": 8,
                                                 "backends": [{"name": "CPU", "features": {
                                                     "NEON": 1, "ARM_FMA": 1, "FP16_VA": 1, "MATMUL_INT8": 1,
                                                     "DOTPROD": 1, "OPENMP": 1, "REPACK": 1}}]})
        self.assertNotIn(b"system_info", data)
        self.assertEqual(evidence.privacy_findings(out), [])
        drops = {item["path"] for item in report["dropped"] if "/raw-" in item["source"]}
        self.assertIn("/build/signingCertSha256", drops)

    def test_mismatch_and_unavailable_records_export(self):
        for index, name in enumerate(("modelHashMismatch", "buildUnavailable", "executionUnreported",
                                      "stagedConfiguration", "noExpectedHash", "setupFailed")):
            record = dict(copy.deepcopy(APP_V2[name]), runId=f"1767225600000-{index}-identity")
            with self.subTest(name=name), tempfile.TemporaryDirectory() as root:
                _, out, _ = self.export_one(record, root)
                self.assertEqual(out["stopReason"], record["stopReason"])
                self.assertEqual(out["artifact"], record["artifact"])
                self.assertEqual(out["configurationSource"], record["configurationSource"])
        self.assertEqual(evidence.BUILD.apply(APP_V2["buildUnavailable"]["build"], "", evidence.Context()),
                         {**{key: None for key in APP_V2["buildUnavailable"]["build"] if key != "signingCertSha256"},
                          "status": "unavailable", "signingKey": None})

    def test_planted_values_in_identity_fields_never_reach_the_bundle(self):
        record = dict(copy.deepcopy(APP_V2["complete"]), runId="1767225600000-0-planted")
        planted_info = (f"system_info: n_threads = 4 / 8 | CPU : NEON = 1 | {SERIAL} = 1 | "
                        f"PLANTEDBACKEND : DOTPROD = 1 | ")
        for item in record["executionReports"]:
            item.update(reasonNoGPU=f"{LOG_TEXT} {SERIAL}", systemInfo=planted_info)
        record["executionReports"][1]["systemInfo"] = f"{HOST_PATH} {EMAIL} {TOKEN}"[:2048]
        record["build"]["signingCertSha256"] = ["e" * 64]
        self.assertEqual(raw_session.validate(record), [])
        with tempfile.TemporaryDirectory() as root:
            report, out, data = self.export_one(record, root)
        for needle in (SERIAL, LOG_TEXT, "alice", "PLANTEDBACKEND", "/home/", "example.com", "ghp_", "e" * 64):
            self.assertNotIn(needle.encode(), data, needle)
        first = out["executionReports"][0]
        self.assertEqual(first["reasonNoGPU"], "unrecognized")
        # Device names are a closed vocabulary too (a CPU reference record never lists any).
        devices = evidence.EXECUTION_REPORT.apply({**record["executionReports"][0], "devices": ["HTP0", SERIAL]},
                                                  "", evidence.Context())["devices"]
        self.assertEqual(devices, ["HTP0", "unrecognized"])
        self.assertEqual(first["systemInfo"]["backends"], [
            {"name": "CPU", "features": {"NEON": 1, "unrecognized": 1}},
            {"name": "unrecognized", "features": {"DOTPROD": 1}}])
        self.assertIsNone(out["executionReports"][1]["systemInfo"])
        self.assertEqual(out["build"]["signingKey"], "other")
        kinds = {(item["path"], item["kind"]) for item in report["dropped"] if "/raw-" in item["source"]}
        self.assertIn(("/executionReports/1/systemInfo", "unparsed-value"), kinds)
        self.assertIn(("/executionReports/0/reasonNoGPU", "unrecognized-value"), kinds)

    def test_free_text_in_strict_identity_fields_refuses_the_export(self):
        # The raw schema accepts any short version name; export accepts only the app's own form.
        for change in (lambda r: r["build"].update(versionName="alice-laptop"),
                       lambda r: r["build"].update(versionName="1.0-alice")):
            record = dict(copy.deepcopy(APP_V2["complete"]), runId="1767225600000-0-refused")
            change(record)
            self.assertEqual(raw_session.validate(record), [])
            with self.subTest(), tempfile.TemporaryDirectory() as root:
                directory = make_capture(Path(root) / "results", [record])
                with self.assertRaises(evidence.Refused) as caught:
                    evidence.export([directory], Path(root) / "out")
                self.assertNotIn("alice", str(caught.exception))
                self.assertFalse((Path(root) / "out").exists() and any((Path(root) / "out").iterdir()))

    def test_system_info_parser(self):
        parse = evidence.parse_system_info
        self.assertEqual(parse("system_info: n_threads = 6 / 8 | CPU : NEON = 1 | SVE_CNT = 16 | "),
                         {"nThreads": 6, "nThreadsBatch": None, "hardwareConcurrency": 8,
                          "backends": [{"name": "CPU", "features": {"NEON": 1, "SVE_CNT": 16}}]})
        for text in ("", "n_threads = 4", "system_info: n_threads = 4 / 8 | /data/local : X = 1 | ",
                     "system_info: n_threads = 4 / 8 | CPU : NEON = yes | "):
            self.assertIsNone(parse(text), text)

    def test_frozen_v2_records_still_export(self):
        record = dict(copy.deepcopy(FROZEN_V2["complete"]), runId="1767225600000-0-frozen")
        with tempfile.TemporaryDirectory() as root:
            _, out, _ = self.export_one(record, root)
        self.assertEqual(out["protocol"]["version"], "cpu-reference-v2")
        self.assertNotIn("build", out)


ACCEL = json.loads((ROOT / "tests/fixtures/raw-session/app-emitted-accelerator.json").read_text())["records"]
# Frozen pre-port accelerator-v1 records (no build/execution identity); they export and verify too.
ACCEL_V1 = json.loads((ROOT / "tests/fixtures/raw-session/app-emitted-accelerator-v1.json").read_text())["records"]


class AcceleratorExportTests(unittest.TestCase):
    """accelerator-v1 app records -> capture -> raw_session -> export -> verify."""

    def export(self, root):
        named = []
        for index, name in enumerate(sorted(ACCEL)):
            record = copy.deepcopy(ACCEL[name])
            record["runId"] = f"1767225600000-{index}-accel"
            # The f8-* records come from one configuration invocation; every other fixture record
            # is the only one kept from its invocation, so give it its own configuration.
            if not name.startswith("f8-"):
                record["configuration"]["id"] = f"fixture-{name}"
            named.append((name, record))
        directory = make_capture(Path(root) / "results", [record for _, record in named])
        return named, evidence.export([directory], Path(root) / "out")

    def test_records_export_with_only_text_withheld_and_verify(self):
        with tempfile.TemporaryDirectory() as root:
            named, report = self.export(root)
            record_drops = [item for item in report["dropped"] if "/raw-" in item["source"]]
            self.assertEqual({item["kind"] for item in record_drops}, {"excluded"}, record_drops)
            # Prompts, model outputs and free engine text stay in local raw evidence only.
            self.assertEqual({item["path"].rsplit("/", 1)[-1] for item in record_drops},
                             {"prompt", "message", "output", "reasonNoGpu", "signingCertSha256"})
            result = evidence.verify(report["bundle"], report["evidence"]["bundleSha256"])
            self.assertTrue(result["valid"], result["errors"])
            bundle = Path(report["bundle"])
            data = b"".join(bundle_bytes(report["bundle"]).values())
            self.assertNotIn(b"PELICAN", data)
            for name, record in named:
                exported = json.loads((bundle / f"sessions/{record['runId']}.json").read_text())["record"]
                with self.subTest(name=name):
                    for key in ("backend", "observedBackend", "purpose", "comparison", "modelFile", "runtimeVersions"):
                        self.assertEqual(exported[key], record[key])
                    self.assertEqual(exported["correctness"]["status"], record["correctness"]["status"])
                    for raw, out in zip(record["correctness"]["probes"], exported["correctness"]["probes"]):
                        self.assertEqual(out["passed"], raw["passed"])
                        self.assertEqual(out["outputSha256"], None if raw["output"] is None else sha(raw["output"]))
                        self.assertEqual(out["promptSha256"], sha(raw["prompt"]))
                    for raw, out in zip(record["coldRuns"], exported["coldRuns"]):
                        self.assertEqual(out.get("observedBackend") is None, raw.get("observedBackend") is None)
                    self.assertEqual(evidence.privacy_findings(exported), [])

    def test_executorch_records_keep_their_settings_tokenizer_and_runner_statistics(self):
        with tempfile.TemporaryDirectory() as root:
            named, report = self.export(root)
            record = dict(named)["executorch-cpu"]
            exported = json.loads((Path(report["bundle"]) / f"sessions/{record['runId']}.json").read_text())["record"]
            for key in ("effectiveSettings", "tokenizerArtifact", "engineVersion", "runtimeVersions"):
                self.assertEqual(exported[key], record[key])
            for raw, out in zip(record["coldRuns"], exported["coldRuns"]):
                self.assertEqual(out["executorchStats"], raw["executorchStats"])
                self.assertEqual(out["observedBackend"], raw["observedBackend"])
            self.assertEqual(exported["configuration"], record["configuration"])

    def test_candidate_reference_must_match_the_reference_session(self):
        with tempfile.TemporaryDirectory() as root:
            named, report = self.export(root)
            self.assertTrue(evidence.verify(report["bundle"])["valid"])
            bundle = Path(report["bundle"])
            path = f"sessions/{dict(named)['f8-litert-gpu']['runId']}.json"
            document = json.loads((bundle / path).read_text())
            document["record"]["correctness"]["probes"][1]["reference"]["outputSha256"] = "e" * 64
            data = evidence.canonical(document)
            (bundle / path).write_bytes(data)
            manifest = json.loads((bundle / "manifest.json").read_text())
            for item in manifest["files"]:
                if item["path"] == path:
                    item.update(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
            (bundle / "manifest.json").write_bytes(evidence.canonical(manifest))
            renamed = bundle.parent / hashlib.sha256((bundle / "manifest.json").read_bytes()).hexdigest()
            bundle.rename(renamed)
            result = evidence.verify(renamed)
            self.assertFalse(result["valid"])
            self.assertTrue(any("reference session" in error for error in result["errors"]), result["errors"])

    def forged(self, root, name, change):
        """Export the fixtures, apply change(record) to one session and re-hash the bundle consistently."""
        named, report = self.export(root)
        bundle = Path(report["bundle"])
        path = f"sessions/{dict(named)[name]['runId']}.json"
        document = json.loads((bundle / path).read_text())
        change(document["record"])
        data = evidence.canonical(document)
        (bundle / path).write_bytes(data)
        manifest = json.loads((bundle / "manifest.json").read_text())
        for item in manifest["files"]:
            if item["path"] == path:
                item.update(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
        (bundle / "manifest.json").write_bytes(evidence.canonical(manifest))
        renamed = bundle.parent / hashlib.sha256((bundle / "manifest.json").read_bytes()).hexdigest()
        bundle.rename(renamed)
        return evidence.verify(renamed)

    def test_bundle_cannot_relabel_a_run_against_its_configuration(self):
        def own_run(record):
            return next(run for run in record["configuration"]["runs"] if run["id"] == record["configurationRunId"])

        def relabel_purpose(record):
            record["purpose"] = "bug-repro"
            record["comparison"] = {"status": "invalid", "reasons": ["bug-repro"]}

        def relabel_everywhere(record):
            relabel_purpose(record)
            own_run(record)["purpose"] = "bug-repro"

        def swap_reference(record):
            record["correctness"]["reference"]["configurationRunId"] = "llama-cpu"

        cases = {
            "record purpose differs from its configured run": (relabel_purpose, "disagrees with the configured run"),
            "configuration edited in one session of a capture": (relabel_everywhere, "disagree on configuration"),
            "reference is not the configured CPU run": (swap_reference, "not the configured CPU run"),
        }
        for label, (change, message) in cases.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as root:
                result = self.forged(root, "f8-litert-gpu", change)
                self.assertFalse(result["valid"])
                self.assertTrue(any(message in error for error in result["errors"]), result["errors"])

    def test_frozen_accelerator_v1_records_export_and_verify(self):
        with tempfile.TemporaryDirectory() as root:
            named = []
            for index, name in enumerate(sorted(ACCEL_V1)):
                record = copy.deepcopy(ACCEL_V1[name])
                record["runId"] = f"1767225600000-{index}-accelv1"
                if not name.startswith("f8-"):
                    record["configuration"]["id"] = f"fixture-{name}"
                named.append(record)
            directory = make_capture(Path(root) / "results", named)
            report = evidence.export([directory], Path(root) / "out")
            result = evidence.verify(report["bundle"], report["evidence"]["bundleSha256"])
            self.assertTrue(result["valid"], result["errors"])
            self.assertEqual(len(result["sessions"]), len(named))
            exported = json.loads((Path(report["bundle"]) / f"sessions/{named[0]['runId']}.json").read_text())["record"]
            self.assertEqual(exported["protocol"]["version"], "accelerator-v1")
            self.assertNotIn("build", exported)

    def test_rehashed_comparison_claims_still_fail_verification(self):
        with tempfile.TemporaryDirectory() as root:
            named, report = self.export(root)
            bundle = Path(report["bundle"])
            run_id = dict(named)["f8-litert-npu"]["runId"]
            path = f"sessions/{run_id}.json"
            document = json.loads((bundle / path).read_text())
            document["record"]["comparison"] = {"status": "valid", "reasons": []}
            data = evidence.canonical(document)
            (bundle / path).write_bytes(data)
            manifest = json.loads((bundle / "manifest.json").read_text())
            for item in manifest["files"]:
                if item["path"] == path:
                    item.update(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
            (bundle / "manifest.json").write_bytes(evidence.canonical(manifest))
            renamed = bundle.parent / hashlib.sha256((bundle / "manifest.json").read_bytes()).hexdigest()
            bundle.rename(renamed)
            result = evidence.verify(renamed)
            self.assertFalse(result["valid"])
            self.assertTrue(any("/comparison" in error for error in result["errors"]), result["errors"])


class LabelAlphabetTests(unittest.TestCase):
    """The board's quant labels (docs/STYLE.md) export; '/' and ',' stay confined to their positions."""

    BOARD_LABELS = ("mixed 2/4/8-bit", "mixed 4/16-bit", "4-bit weights, 8-bit activations")

    def relabelled(self, quant):
        record = copy.deepcopy(ACCEL["executorch-cpu"])
        record["runId"] = "1767225600000-0-label"
        record["quant"] = quant
        for run in record["configuration"]["runs"]:
            if run["id"] == record["configurationRunId"]:
                run["quant"] = quant
        self.assertEqual(raw_session.validate(record), [])
        return record

    def test_board_quant_labels_export_and_verify(self):
        for quant in self.BOARD_LABELS:
            with self.subTest(quant), tempfile.TemporaryDirectory() as root:
                directory = make_capture(Path(root) / "results", [self.relabelled(quant)])
                report = evidence.export([directory], Path(root) / "out")
                result = evidence.verify(report["bundle"], report["evidence"]["bundleSha256"])
                self.assertTrue(result["valid"], result["errors"])
                exported = json.loads((Path(report["bundle"]) / "sessions/1767225600000-0-label.json")
                                      .read_text())["record"]
                self.assertEqual(exported["quant"], quant)
                self.assertEqual({run["quant"] for run in exported["configuration"]["runs"]
                                  if run["id"] == exported["configurationRunId"]}, {quant})

    def test_label_form_accepts_only_the_separator_positions_the_labels_use(self):
        accepted = (*self.BOARD_LABELS, "Q4_0", "Q4_0 (QAT)", "4-bit QAT + LoRA", "W4A16", "1/2", "x" * 128)
        refused = (
            "/data/app", "data/app", "a/b", "2/b", "b/2", "2/", "/2", "1//2", "2/ 4", "lib/arm64/libx.so",
            "x,y", "x ,y", ",x", "x,,y", "x, ", "x,  y", "x" * 129, "x" * 127 + "/", "mixed 2/4/8-bit\n", "x\\y", "x@y",
            "mixed 2⁄4-bit", "4-bit weights， 8-bit",
        )
        for value in accepted:
            with self.subTest(accepted=value):
                self.assertEqual(evidence.LABEL.apply(value, "/quant", evidence.Context()), value)
        for value in refused:
            with self.subTest(refused=value), self.assertRaises(evidence.Refused) as caught:
                evidence.LABEL.apply(value, "/quant", evidence.Context())
            self.assertIn("not a label", str(caught.exception))
            self.assertNotIn(value, str(caught.exception))

    def test_private_values_in_a_label_with_separators_still_refuse_the_export(self):
        cases = {"token": f"mixed 2/4/8-bit {TOKEN}", "android-id": f"4-bit weights, {ANDROID_ID}",
                 "network-serial": "mixed 10.0.2.2/16-bit"}
        for name, quant in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as root:
                directory = make_capture(Path(root) / "results", [self.relabelled(quant)])
                out = Path(root) / "exports"
                with self.assertRaises(evidence.Refused) as caught:
                    evidence.export([directory], out)
                self.assertIn(f"looks like {name}", str(caught.exception))
                self.assertNotIn(quant, str(caught.exception))
                self.assertFalse(out.exists() and any(out.iterdir()))


class ScreenPreparationExportTests(unittest.TestCase):
    def test_the_screen_preparation_stays_in_the_local_manifest(self):
        with tempfile.TemporaryDirectory() as root:
            directory = make_capture(Path(root) / "results", [fixture("complete", "1767225600000-0-screen")])
            manifest_path = directory / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            manifest["screenPreparation"] = {"wake": "KEYCODE_WAKEUP", "keyguard": "dismissed"}
            manifest_path.write_text(json.dumps(manifest))
            report = evidence.export([directory], Path(root) / "out")
            self.assertIn({"source": manifest["captureId"], "path": "/screenPreparation", "kind": "excluded"},
                          report["dropped"])
            self.assertNotIn(b"screenPreparation", b"".join(bundle_bytes(report["bundle"]).values()))
            self.assertTrue(evidence.verify(report["bundle"])["valid"])


class CommandLineTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)

    def test_export_then_verify_round_trip(self):
        with tempfile.TemporaryDirectory() as root:
            directory = make_planted_capture(Path(root) / "results")
            out = Path(root) / "exports"
            exported = self.run_cli("export", "--out", out, "--base-url", "https://evidence.example.org/xebench",
                                    directory)
            self.assertEqual(exported.returncode, 0, exported.stderr)
            report = json.loads(exported.stdout)
            digest = report["evidence"]["bundleSha256"]
            self.assertEqual(set(report["evidence"]), {"sessionIds", "bundleSha256", "url"})
            self.assertEqual(report["evidence"]["url"], f"https://evidence.example.org/xebench/{digest}/manifest.json")
            verified = self.run_cli("verify", out / digest, "--sha256", digest)
            self.assertEqual(verified.returncode, 0, verified.stdout)
            self.assertTrue(json.loads(verified.stdout)["valid"])
            self.assertEqual(self.run_cli("verify", out / digest, "--sha256", "f" * 64).returncode, 1)

    def test_refusals_exit_non_zero_without_output(self):
        with tempfile.TemporaryDirectory() as root:
            directory = make_capture(Path(root) / "results", [fixture("complete", "1767225600000-0-fixture")])
            next(directory.glob("raw-*.jsonl")).write_text("{}")
            out = Path(root) / "exports"
            result = self.run_cli("export", "--out", out, directory)
            self.assertEqual(result.returncode, 1)
            self.assertIn("Export refused", result.stderr)
            self.assertEqual(result.stdout, "")
            self.assertFalse(out.exists() and any(out.iterdir()))
            insecure = self.run_cli("export", "--out", out, "--base-url", "http://example.org", directory)
            self.assertEqual(insecure.returncode, 1)


if __name__ == "__main__":
    unittest.main()
