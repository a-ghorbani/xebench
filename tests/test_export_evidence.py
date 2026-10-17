import copy
import hashlib
import importlib.util
import json
import os
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
APP = json.loads((ROOT / "tests/fixtures/raw-session/app-emitted.json").read_text())["records"]

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


def make_capture(root, records, device_info=None, inline=(), checkpoints=(), extra_files=None):
    session = capture.Capture(root, dict(device_info or HOST_DEVICE))
    files, lines = dict(extra_files or {}), []
    for index, record in enumerate(list(checkpoints) + list(records)):
        name = f"{record['runId']}-{len(record['coldRuns'])}.json"
        files[name] = json.dumps(record)
        reference = {"name": name, "sha256": sha(files[name])}
        lines.append(f"{100 + index}.000 123 123 I ReactNativeJS: XEBENCH_RESULT_FILE {json.dumps(reference)}")
    lines.extend(f"200.000 123 123 I ReactNativeJS: {line}" for line in inline)
    session.ingest("\n".join(lines), files.__getitem__)
    session.app_identity.update(before=APKS, after=APKS, consistency="matched")
    session.finish("partial" if inline else "complete")
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
            self.assertEqual(sorted(captured), sorted(evidence.CAPTURE_FILE.fields))

    def test_private_values_in_approved_fields_refuse_the_export(self):
        def model_token(record):
            record["model"] = record["configuration"]["runs"][0]["model"] = f"Llama {TOKEN}"
        cases = {
            "email in engine version": (lambda r: r.update(engineVersion=f"llama.rn {EMAIL}"), None, EMAIL),
            "token in model label": (model_token, None, TOKEN),
            "android id in run id": (lambda r: r.update(runId=f"1767225600000-0-{ANDROID_ID}"), None, ANDROID_ID),
            "path as manufacturer": (lambda r: None, {**HOST_DEVICE, "manufacturer": HOST_PATH}, HOST_PATH),
            "serial-like digits in a status": (
                lambda r: r["coldRuns"][0]["conditions"]["before"].update(probeStatus=SERIAL), None, SERIAL),
        }
        for name, (change, device, needle) in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as root:
                record = fixture("complete", "1767225600000-0-fixture")
                change(record)
                self.assertEqual(raw_session.validate(record), [])
                directory = make_capture(Path(root) / "results", [record], device_info=device)
                out = Path(root) / "exports"
                with self.assertRaises(evidence.Refused) as caught:
                    evidence.export([directory], out)
                self.assertNotIn(needle, str(caught.exception))
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

    def test_missing_listed_file_fails(self):
        bundle = self.copy()
        (bundle / "sessions/1767225600000-1-fixture.json").unlink()
        self.assertIn("sessions/1767225600000-1-fixture.json: listed file is missing", evidence.verify(bundle)["errors"])


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
