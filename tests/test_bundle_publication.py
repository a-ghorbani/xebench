"""Verified publication from evidence bundles (scripts/aggregate.mjs --from-bundles).

Every test runs against a temporary copy of scripts/ + data/ with bundles hosted under
its own evidence/<sha>/, so the checked-in data is never written. Bundles are built
from the app-emitted fixtures (tests/bundle_fixtures.py); no raw capture is committed.

XEBENCH_UPDATE_FIXTURES=1 rewrites tests/fixtures/verified-publication/ from the
current producer; CI fails while the committed files differ.
"""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

import bundle_fixtures as fx
from test_export_evidence import raw_session

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/verified-publication"
RAW_URL = "https://raw.githubusercontent.com/a-ghorbani/xebench/main/evidence/{}/manifest.json"
LEGACY_KEY = "llama.cpp|cpu|android|Google Pixel 9|Llama 3.2 1B|Q4_K_M|measured"
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
T = {name: f"2026-03-0{day}T00:00:00.000Z" for day, name in enumerate(("t0", "t1", "t2", "t3"), start=1)}


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def key_of(row):
    return "|".join(str(row.get(field)) for field in
                    ("engine", "backend", "platform", "device", "model", "quant", "provenance"))


def round1(value):
    """The producer's rounding (JavaScript Math.round: half up), not Python's round-half-even."""
    return math.floor(value * 10 + 0.5) / 10


class Workspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        shutil.copytree(ROOT / "scripts", self.root / "scripts")
        shutil.copytree(ROOT / "data", self.root / "data")
        (self.root / "evidence").mkdir()
        self.out = Path(self.tmp.name) / "work"
        self.canonical = self.root / "data/benchmarks.json"
        self.index = self.root / "data/sessions/index.json"
        self.captures = 0

    def tearDown(self):
        self.tmp.cleanup()

    def bundle(self, *records, hosted=True, **options):
        self.captures += 1
        directory, sha = fx.bundle(self.out, list(records), f"00000000-0000-4000-8000-{self.captures:012d}", **options)
        return (fx.host(directory, self.root) if hosted else directory), sha

    def node(self, script, *args):
        return subprocess.run(["node", str(self.root / "scripts" / script), *map(str, args)],
                              capture_output=True, text=True)

    def aggregate(self, *args):
        return self.node("aggregate.mjs", *args)

    def publish(self, *sessions, flags=(), source=None):
        args = ["--from-bundles", source or self.root / "evidence", "--update", *flags]
        for session in sessions:
            args += ["--session", session]
        result = self.aggregate(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        return result

    def dry_run(self, *args, source=None):
        return self.aggregate("--from-bundles", source or self.root / "evidence", "--dry-run", *args)

    def digests(self):
        return tuple(hashlib.sha256(path.read_bytes()).hexdigest() for path in (self.canonical, self.index))

    def no_leftovers(self):
        for directory in ("data", "data/sessions"):
            self.assertEqual(sorted(p.name for p in (self.root / directory).iterdir()),
                             sorted(p.name for p in (ROOT / directory).iterdir()), f"temporary files in {directory}")

    def assert_rejected(self, reason, *args):
        before = self.digests()
        result = self.aggregate(*args)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(reason, result.stderr)
        self.assertEqual(self.digests(), before, "canonical files changed on a rejected run")
        self.no_leftovers()
        return result

    def rows(self):
        return {key_of(row): row for row in load(self.canonical)["rows"]}

    def row(self, device="Google Fixture Phone", model="Llama 3.2 1B", quant="Q4_K_M", engine="llama.cpp",
            backend="cpu"):
        return self.rows()[f"{engine}|{backend}|android|{device}|{model}|{quant}|measured"]

    def entry(self, key):
        return next(item for item in load(self.index)["keys"] if item["key"] == key)

    def assert_valid_data(self):
        result = self.node("validate_data.mjs")
        self.assertEqual(result.returncode, 0, result.stderr)


class DryRunTests(Workspace):
    def test_dry_run_reports_every_session_and_writes_nothing(self):
        _, sha = self.bundle(
            fx.cpu_record("complete", "1767225600000-0-dry", device="Fixture Phone"),
            fx.cpu_record("flagged", "1767225600000-1-dry", device="Fixture Phone"),
            fx.cpu_record("noExpectedHash", "1767225600000-2-dry", device="Fixture Tablet"),
            fx.cpu_record("chargingRecordOnly", "1767225600000-3-dry"),
            fx.cpu_record("configurationAborted", "1767225600000-4-dry"))
        before = self.digests()
        result = self.dry_run()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.digests(), before)
        self.no_leftovers()
        # The session table has eight columns; the board plan below it has five.
        lines = {line.split(" | ")[1]: line for line in result.stdout.splitlines()
                 if "-dry |" in line and line.count(" | ") == 7}
        self.assertIn("| verified | — |", lines["1767225600000-0-dry"])
        self.assertIn("| failed |", lines["1767225600000-1-dry"])
        self.assertIn("| unverified | artifact unknown", lines["1767225600000-2-dry"])
        self.assertIn("not publishable (experiment session", lines["1767225600000-3-dry"])
        self.assertIn("not publishable (no measured repetition)", lines["1767225600000-4-dry"])
        self.assertIn(f"| {sha[:8]}… | 5 | yes | yes |", result.stdout)
        # The board plan: the verified session is displayed for its key; the failed one is history.
        self.assertRegex(result.stdout, r"Google Fixture Phone\\\|Llama 3.2 1B\\\|Q4_K_M\\\|measured \| "
                                        r"1767225600000-0-dry \| newest-verified \| new row")

        report = json.loads(self.dry_run("--json").stdout)
        decisions = {item["sessionId"]: item["decision"] for item in report["sessions"]}
        self.assertEqual(decisions["1767225600000-0-dry"]["validation"], "verified")
        self.assertEqual(report["plan"]["schemaVersion"], "1.2")
        filtered = json.loads(self.dry_run("--json", "--session", "1767225600000-2-dry").stdout)
        self.assertEqual([item["sessionId"] for item in filtered["sessions"]], ["1767225600000-2-dry"])

    def test_dry_run_reports_refused_bundles_and_exits_non_zero(self):
        directory, _ = self.bundle(fx.cpu_record("complete", "1767225600000-0-bad"))
        self.bundle(fx.cpu_record("complete", "1767225600000-1-good"))
        session = directory / "sessions/1767225600000-0-bad.json"
        session.write_bytes(session.read_bytes().replace(b"Llama 3.2 1B", b"Llama 3.2 3B"))
        before = self.digests()
        result = self.dry_run()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("checksum or size mismatch", result.stdout)
        self.assertIn("1767225600000-1-good", result.stdout)
        self.assertIn("1 bundle(s) refused", result.stderr)
        self.assertEqual(self.digests(), before)

    def test_usage(self):
        source = self.root / "evidence"
        for args in (["--from-bundles", source], ["--from-bundles", source, "--dry-run", "--update"],
                     ["--from-bundles", source, "--update"], ["--from-bundles", source, "--dry-run", "--allow-legacy"],
                     ["--from-bundles"], ["--from-bundles", source, "--dry-run", "--replace-legacy"],
                     ["--from-bundles", source, "--update", "--json", "--session", "x"]):
            with self.subTest(args=args):
                self.assertEqual(self.aggregate(*args).returncode, 2)


class VerifiedPublicationTests(Workspace):
    def test_verified_row_comes_from_the_bundle_and_its_recomputed_repetitions(self):
        record = fx.cpu_record("complete", "1767225600000-0-verified", device="Fixture Phone")
        directory, sha = self.bundle(record)
        result = self.publish("1767225600000-0-verified")
        self.assertIn("new row", result.stderr)
        data = load(self.canonical)
        self.assertEqual(data["schemaVersion"], "1.2", "a row with build.runtimes makes the file 1.2")
        self.assertRegex(data["generatedAt"], ISO)
        row = self.row()
        exported = load(directory / "sessions/1767225600000-0-verified.json")["record"]
        complete = [attempt for attempt in exported["coldRuns"] if attempt["status"] == "complete"]
        prefill = raw_session.aggregate([attempt["prefillTps"] for attempt in complete])
        decode = raw_session.aggregate([attempt["decodeTps"] for attempt in complete])
        self.assertEqual((row["validation"], row["guardsPassed"]), ("verified", True))
        self.assertEqual(row["sampleCount"], len(complete))
        self.assertEqual((row["ppTps"], row["tgTps"]), (round1(prefill["median"]), round1(decode["median"])))
        self.assertEqual(row["dispersion"]["tgTps"],
                         {"iqr": round1(decode["iqr"]), "min": round1(decode["min"]), "max": round1(decode["max"])})
        self.assertEqual(row["evidence"], {"sessionIds": ["1767225600000-0-verified"], "bundleSha256": sha,
                                           "url": RAW_URL.format(sha)})
        self.assertEqual((row["protocolVersion"], row["conditionPolicy"], row["timingMethod"]),
                         ("cpu-reference-v3", "run-conditions-v2", "engine-timings"))
        self.assertEqual(row["build"]["engineVersion"], "llama.cpp b11385 (bf9a0cc)")
        self.assertEqual(row["build"]["bindingVersion"], "llama.rn 0.0.0-fixture")
        self.assertEqual(row["build"]["harnessCommit"], record["build"]["gitCommit"])
        self.assertEqual(row["build"]["runtimes"], [{"name": "llama.cpp", "version": "b11385"},
                                                    {"name": "llama.rn", "version": "0.0.0-fixture"}])
        self.assertNotIn("apkSha256", row["build"], "the fixture capture holds a base and a split APK")
        self.assertNotIn("memoryMb", row, "sampled PSS/RSS is not peak memory; nothing is invented")
        self.assertIn("publication-policy-v1 verified", row["asStated"])
        entry = self.entry(key_of(row))
        self.assertEqual((entry["displayed"], entry["rule"]), ("1767225600000-0-verified", "newest-verified"))
        self.assertEqual(entry["sessions"][0]["row"], row)
        self.assert_valid_data()
        self.no_leftovers()

    def test_contract_1_1_without_runtime_versions(self):
        self.bundle(fx.cpu_record("complete", "1767225600000-0-v11", device="Fixture Phone", runtimes=False),
                    apks=[{"name": "base.apk", "sha256": "d" * 64}])
        self.publish("1767225600000-0-v11")
        data = load(self.canonical)
        self.assertEqual(data["schemaVersion"], "1.1")
        build = self.row()["build"]
        self.assertNotIn("runtimes", build)
        self.assertNotIn("engineVersion", build, "no runtime versions: the engine version stays unknown")
        self.assertEqual(build["bindingVersion"], "llama.rn 0.0.0-fixture")
        self.assertEqual(build["apkSha256"], "d" * 64)
        self.assert_valid_data()

    def test_unverified_and_failed_rows_keep_their_evidence(self):
        self.bundle(fx.cpu_record("noExpectedHash", "1767225600000-0-u", device="Fixture Phone"),
                    fx.cpu_record("flagged", "1767225600000-1-f", device="Fixture Tablet"))
        self.publish("1767225600000-0-u", "1767225600000-1-f")
        unverified, failed = self.row(), self.row(device="Google Fixture Tablet")
        self.assertEqual((unverified["validation"], unverified["guardsPassed"]), ("unverified", None))
        self.assertIn("artifact unknown", unverified["asStated"])
        self.assertEqual((failed["validation"], failed["guardsPassed"]), ("failed", False))
        self.assertIn("conditions fail", failed["asStated"])
        for row in (unverified, failed):
            self.assertEqual(row["evidence"]["sessionIds"], [row["evidence"]["sessionIds"][0]])
            self.assertIn("dispersion", row)
        self.assert_valid_data()

    def test_accelerator_row_needs_the_gate_and_confirmed_backend(self):
        self.bundle(fx.accel_record("f8-llama-hexagon", "1767225600000-0-npu", device="Fixture Phone"),
                    fx.accel_record("f8-litert-gpu", "1767225600000-1-gpu", device="Fixture Phone"))
        self.publish("1767225600000-0-npu", "1767225600000-1-gpu")
        npu = self.row(backend="npu", model="Gemma 3 1B", quant="Q4_0 (GGUF)")
        self.assertEqual(npu["validation"], "verified")
        self.assertEqual(npu["protocolVersion"], "accelerator-v2")
        gpu = next(row for key, row in self.rows().items() if key.startswith("litert-lm|gpu|"))
        self.assertEqual(gpu["validation"], "unverified")
        self.assertIn("backend unknown", gpu["asStated"])

    def test_vendor_and_legacy_rows_are_unchanged(self):
        before = load(self.canonical)["rows"]
        self.bundle(fx.cpu_record("complete", "1767225600000-0-keep", device="Fixture Phone"))
        self.publish("1767225600000-0-keep")
        after = {key_of(row): row for row in load(self.canonical)["rows"]}
        for row in before:
            self.assertEqual(json.dumps(after[key_of(row)]), json.dumps(row))
        self.assertEqual(len(after), len(before) + 1)


class FailClosedTests(Workspace):
    def test_recompute_mismatch_is_rejected(self):
        directory, _ = self.bundle(fx.cpu_record("complete", "1767225600000-0-stats", device="Fixture Phone"))
        shutil.rmtree(directory)  # host only the forged bundle

        def inflate(document):
            document["record"]["summary"]["decodeTps"]["median"] *= 1.5
        forged = fx.rehash(self.out / "bundles" / directory.name, "sessions/1767225600000-0-stats.json", inflate)
        fx.host(forged, self.root)
        result = self.assert_rejected("summary/decodeTps/median", "--from-bundles", self.root / "evidence",
                                      "--update", "--session", "1767225600000-0-stats")
        self.assertIn("bundle refused", result.stderr)
        self.assertEqual(self.dry_run().returncode, 1)

    def test_recompute_in_the_publisher_rejects_a_summary_its_repetitions_do_not_give(self):
        """The publisher recomputes statistics itself, independently of the Python verifier."""
        directory, sha = self.bundle(fx.cpu_record("complete", "1767225600000-0-js", device="Fixture Phone"))
        script = (
            "import {bundleRow, recomputeSummary, summaryMismatches} from './scripts/lib/bundles.mjs';"
            "import {readFileSync} from 'node:fs';"
            f"const doc = JSON.parse(readFileSync({json.dumps(str(directory / 'sessions/1767225600000-0-js.json'))}, 'utf8'));"
            "const ok = summaryMismatches(doc.record);"
            "doc.record.summary.prefillTps.iqr += 0.5; doc.record.summary.ttftMs.n += 1;"
            "let refused = null;"
            "try { bundleRow({doc, capture: {}, decision: {checks: [], policy: 'p', validation: 'verified', guardsPassed: true},"
            f" bundleSha256: '{sha}'}}); }} catch (error) {{ refused = error.message; }}"
            "console.log(JSON.stringify({ok, bad: summaryMismatches(doc.record), refused, n: recomputeSummary(doc.record).decodeTps.n}));")
        result = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True,
                                cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["ok"], [])
        self.assertEqual(out["bad"], ["summary.ttftMs.n", "summary.prefillTps.iqr"])
        self.assertIn("record summary disagrees with its repetitions", out["refused"])

    def test_tampered_bundle_is_rejected(self):
        directory, _ = self.bundle(fx.cpu_record("complete", "1767225600000-0-tamper", device="Fixture Phone"))
        session = directory / "sessions/1767225600000-0-tamper.json"
        session.write_bytes(session.read_bytes().replace(b'"verdict"', b'"verdict"').replace(b"Fixture", b"Fixturf", 1))
        self.assert_rejected("checksum or size mismatch", "--from-bundles", self.root / "evidence", "--update",
                             "--session", "1767225600000-0-tamper")

    def test_bytes_are_bound_to_the_verified_manifest(self):
        """Files the publisher reads after verification must still match the verified hashes."""
        directory, sha = self.bundle(fx.cpu_record("complete", "1767225600000-0-bind", device="Fixture Phone"))
        session = directory / "sessions/1767225600000-0-bind.json"
        session.write_bytes(session.read_bytes() + b" ")
        script = ("import {readBoundBundle} from './scripts/lib/bundles.mjs';"
                  "const out = [];"
                  f"const b = readBoundBundle({json.dumps(str(directory))}, '{sha}');"
                  "try { b.file('sessions/1767225600000-0-bind.json'); } catch (e) { out.push(e.message); }"
                  f"try {{ readBoundBundle({json.dumps(str(directory))}, '{'0' * 64}'); }} catch (e) {{ out.push(e.message); }}"
                  "console.log(JSON.stringify(out));")
        result = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True,
                                cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        messages = json.loads(result.stdout)
        self.assertIn("tampered", messages[0])
        self.assertIn("no longer hashes to the verified bundleSha256", messages[1])

    def test_misnamed_or_symlinked_bundle_directories_are_refused(self):
        directory, _ = self.bundle(fx.cpu_record("complete", "1767225600000-0-name", device="Fixture Phone"))
        directory.rename(directory.with_name("f" * 64))
        self.assert_rejected("SHA-256 differs", "--from-bundles", self.root / "evidence", "--update",
                             "--session", "1767225600000-0-name")
        (self.root / "evidence" / ("f" * 64)).rename(self.root / "evidence" / "not-a-hash")
        self.assert_rejected("not a <bundleSha256> directory", "--from-bundles", self.root / "evidence", "--update",
                             "--session", "1767225600000-0-name")

    def test_unhosted_bundle_is_refused_on_update(self):
        directory, sha = self.bundle(fx.cpu_record("complete", "1767225600000-0-local", device="Fixture Phone"),
                                     hosted=False)
        self.assert_rejected(f"is not hosted at evidence/{sha}/", "--from-bundles", directory.parent, "--update",
                             "--session", "1767225600000-0-local")
        result = self.dry_run(source=directory.parent)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"| {sha[:8]}… | 1 | yes | no |", result.stdout)

    def test_never_publishable_sessions_are_refused(self):
        self.bundle(fx.cpu_record("chargingRecordOnly", "1767225600000-0-exp"),
                    fx.cpu_record("configurationAborted", "1767225600000-1-none"))
        self.bundle(fx.accel_record("bugRepro-npu", "1767225600000-2-repro"))
        for session, reason in (("1767225600000-0-exp", "experiment session"),
                                ("1767225600000-1-none", "no measured repetition"),
                                ("1767225600000-2-repro", "bug-repro session"),
                                ("1767225600000-9-missing", "is not in any bundle")):
            with self.subTest(session):
                self.assert_rejected(reason, "--from-bundles", self.root / "evidence", "--update", "--session", session)

    def test_experiment_configuration_ids_are_refused_without_markers(self):
        record = fx.cpu_record("complete", "1767225600000-0-expid", device="Fixture Phone")
        record["configuration"]["id"] = "experiment-anything"
        self.bundle(record)
        self.assert_rejected("experiment-* configuration id", "--from-bundles", self.root / "evidence", "--update",
                             "--session", "1767225600000-0-expid")

    def test_malformed_index_fails_closed(self):
        self.bundle(fx.cpu_record("complete", "1767225600000-0-idx", device="Fixture Phone"))
        self.publish("1767225600000-0-idx")
        index = load(self.index)
        cases = {
            "displayed": lambda i: i["keys"][0].update(displayed="someone-else"),
            "row": lambda i: i["keys"][0]["sessions"][0]["row"].update(tgTps=999.0),
            "url": lambda i: i["keys"][0]["sessions"][0].update(url="https://example.com/x"),
            "unknown field": lambda i: i.update(extra=True),
        }
        for name, change in cases.items():
            with self.subTest(name):
                broken = copy.deepcopy(index)
                change(broken)
                self.index.write_text(json.dumps(broken, indent=2) + "\n")
                self.assertEqual(self.node("validate_data.mjs").returncode, 1)
                self.bundle(fx.cpu_record("complete", f"1767225600000-1-{name[:3]}", device="Fixture Tablet"))
                self.assert_rejected("data/sessions/index.json", "--from-bundles", self.root / "evidence",
                                     "--update", "--session", f"1767225600000-1-{name[:3]}")
        self.index.write_text(json.dumps(index, indent=2) + "\n")
        # A verified row without its session in the index is refused by every writer.
        data = load(self.canonical)
        orphan = copy.deepcopy(self.row())
        orphan["device"] = "Orphan Phone"
        data["rows"].append(orphan)
        data["rowCount"] += 1
        self.canonical.write_text(json.dumps(data, indent=2) + "\n")
        self.assertIn("refusing to emit validation=verified", self.node("validate_data.mjs").stderr)

    def test_raw_records_cannot_replace_a_bundle_row(self):
        from test_publication import legacy_record
        self.bundle(fx.cpu_record("complete", "1767225600000-0-raw", device="Fixture Phone"))
        self.publish("1767225600000-0-raw")
        raw = self.root / "raw.jsonl"
        record = legacy_record(model="Llama 3.2 1B", quant="Q4_K_M")
        record["deviceInfo"] = {"manufacturer": "Google", "model": "Fixture Phone", "soc": "x"}
        raw.write_text(json.dumps(record) + "\n")
        self.assert_rejected("published from evidence bundles", raw, "--update", "--allow-legacy")
        # Unrelated legacy updates still work and keep the index consistent.
        unrelated = self.root / "raw2.jsonl"
        unrelated.write_text(json.dumps(legacy_record()) + "\n")
        self.assertEqual(self.aggregate(unrelated, "--update", "--allow-legacy").returncode, 0)
        self.assert_valid_data()


class ReplacementTests(Workspace):
    def test_a_legacy_row_is_replaced_only_with_replace_legacy_and_is_kept(self):
        legacy = self.rows()[LEGACY_KEY]
        self.bundle(fx.cpu_record("complete", "1767225600000-0-pixel", device="Pixel 9"))
        self.assert_rejected("pass --replace-legacy", "--from-bundles", self.root / "evidence", "--update",
                             "--session", "1767225600000-0-pixel")
        result = self.publish("1767225600000-0-pixel", flags=["--replace-legacy"])
        self.assertIn("replaces the legacy row", result.stderr)
        self.assertEqual(self.rows()[LEGACY_KEY]["validation"], "verified")
        entry = self.entry(LEGACY_KEY)
        self.assertEqual(entry["replacedRows"], [{"reason": "replace-legacy", "replacedBy": "1767225600000-0-pixel",
                                                  "row": legacy}])
        self.assertEqual(json.dumps(entry["replacedRows"][0]["row"]), json.dumps(legacy), "kept byte for byte")
        self.assert_valid_data()

    def test_a_failed_row_is_replaced_only_with_replace_failed(self):
        data = load(self.canonical)
        target = next(row for row in data["rows"] if key_of(row) == LEGACY_KEY)
        target["validation"] = "failed"  # e.g. invalidated by a later output-correctness re-check
        self.canonical.write_text(json.dumps(data, indent=2) + "\n")
        self.assert_valid_data()
        self.bundle(fx.cpu_record("complete", "1767225600000-0-again", device="Pixel 9"))
        for flags in ([], ["--replace-legacy"]):
            with self.subTest(flags=flags):
                self.assert_rejected("pass --replace-failed", "--from-bundles", self.root / "evidence", "--update",
                                     *flags, "--session", "1767225600000-0-again")
        self.publish("1767225600000-0-again", flags=["--replace-failed"])
        self.assertEqual(self.entry(LEGACY_KEY)["replacedRows"][0]["reason"], "replace-failed")
        self.assertEqual(self.entry(LEGACY_KEY)["replacedRows"][0]["row"], target)


class HistoryTests(Workspace):
    KEY = "llama.cpp|cpu|android|Google Fixture Phone|Llama 3.2 1B|Q4_K_M|measured"

    def sessions(self):
        """One bundle with an unverified (t1), a verified (t0), a failed (t2) and a newer verified (t3) session."""
        return self.bundle(
            fx.cpu_record("noExpectedHash", "1767225600000-0-u", timestamp=T["t1"], device="Fixture Phone"),
            fx.cpu_record("complete", "1767225600000-1-v", timestamp=T["t0"], device="Fixture Phone"),
            fx.cpu_record("flagged", "1767225600000-2-f", timestamp=T["t2"], device="Fixture Phone"),
            fx.cpu_record("orderedLaterRun", "1767225600000-3-v2", timestamp=T["t3"], device="Fixture Phone"))

    def displayed(self):
        entry = self.entry(self.KEY)
        self.assertEqual(self.rows()[self.KEY], next(s["row"] for s in entry["sessions"] if s["sessionId"] == entry["displayed"]))
        return entry["displayed"], entry["rule"]

    def test_selection_prefers_the_newest_verified_and_keeps_every_session(self):
        self.sessions()
        self.publish("1767225600000-0-u")
        self.assertEqual(self.displayed(), ("1767225600000-0-u", "newest-unverified"))
        self.publish("1767225600000-1-v")  # older, but verified
        self.assertEqual(self.displayed(), ("1767225600000-1-v", "newest-verified"))
        result = self.publish("1767225600000-2-f")  # newest, but failed: never selected over verified
        self.assertIn("history only", result.stderr)
        self.assertEqual(self.displayed(), ("1767225600000-1-v", "newest-verified"))
        self.publish("1767225600000-3-v2")
        self.assertEqual(self.displayed(), ("1767225600000-3-v2", "newest-verified"))
        entry = self.entry(self.KEY)
        self.assertEqual([s["sessionId"] for s in entry["sessions"]],
                         ["1767225600000-1-v", "1767225600000-0-u", "1767225600000-2-f", "1767225600000-3-v2"])
        self.assertEqual([s["validation"] for s in entry["sessions"]], ["verified", "unverified", "failed", "verified"])
        self.assert_valid_data()

    def test_a_displayed_failed_row_changes_only_with_replace_failed(self):
        self.sessions()
        self.publish("1767225600000-2-f")
        self.assertEqual(self.displayed(), ("1767225600000-2-f", "newest-failed"))
        self.assert_rejected("pass --replace-failed", "--from-bundles", self.root / "evidence", "--update",
                             "--session", "1767225600000-0-u")
        self.publish("1767225600000-0-u", flags=["--replace-failed"])
        self.assertEqual(self.displayed(), ("1767225600000-0-u", "newest-unverified"))
        self.assertEqual(self.entry(self.KEY)["replacedRows"], [], "the failed session stays in the history")

    def test_history_is_deterministic_whatever_the_order(self):
        orders = (["0-u", "1-v", "2-f", "3-v2"], ["3-v2", "2-f", "1-v", "0-u"], ["2-f", "0-u", "3-v2", "1-v"])
        results = []
        for order in orders:
            with self.subTest(order=order):
                self.tearDown()
                self.setUp()
                self.sessions()
                for name in order:  # a later session may change a displayed failed row
                    self.publish(f"1767225600000-{name}", flags=["--replace-failed"])
                results.append((self.index.read_bytes(), load(self.canonical)["rows"]))
        self.tearDown()
        self.setUp()
        self.sessions()
        self.publish(*(f"1767225600000-{name}" for name in orders[1]))  # all in one run
        results.append((self.index.read_bytes(), load(self.canonical)["rows"]))
        self.assertTrue(all(item == results[0] for item in results))

    def test_republishing_is_a_no_op_and_sessions_are_never_rewritten(self):
        directory, _ = self.sessions()
        self.publish("1767225600000-1-v")
        before = self.digests()
        result = self.publish("1767225600000-1-v")
        self.assertIn("already published with identical content", result.stderr)
        self.assertEqual(self.digests(), before)
        # The same session in a second, different bundle: ambiguous in one run, refused once published.
        second, _ = self.bundle(fx.cpu_record("complete", "1767225600000-1-v", timestamp=T["t0"], device="Fixture Phone"),
                                fx.cpu_record("complete", "1767225600000-9-other", device="Fixture Tablet"))
        self.assert_rejected("refusing to pick one", "--from-bundles", self.root / "evidence", "--update",
                             "--session", "1767225600000-1-v")
        result = self.dry_run("--session", "1767225600000-1-v")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("not publishable (in 2 bundles; --update refuses to pick one)", result.stdout)
        self.assert_rejected("is already published with different content", "--from-bundles", second,
                             "--update", "--session", "1767225600000-1-v")
        self.assert_rejected("not in any bundle", "--from-bundles", self.root / "evidence", "--dry-run",
                             "--session", "1767225600000-8-nowhere")


HOOK = """
import fs from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
const real = fs.renameSync;
const from = Number(process.env.XEBENCH_FAIL_RENAME_FROM);
const only = process.env.XEBENCH_FAIL_RENAME_ONLY === '1';
let count = 0;
fs.renameSync = (source, target) => {
  count += 1;
  if (count === from || (!only && count > from)) {
    const error = new Error('injected rename failure');
    error.code = 'EIO';
    throw error;
  }
  return real(source, target);
};
syncBuiltinESMExports();
"""


class ReviewRegressionTests(Workspace):
    def test_every_published_row_must_reproduce_from_its_bundle(self):
        self.bundle(fx.cpu_record("complete", "1767225600000-0-one", device="Fixture Phone"))
        self.publish("1767225600000-0-one")
        key = "llama.cpp|cpu|android|Google Fixture Phone|Llama 3.2 1B|Q4_K_M|measured"
        board, index = load(self.canonical), load(self.index)
        good = (self.canonical.read_text(), self.index.read_text())
        # A consistent hand edit of both files: they agree with each other, not with the bundle.
        next(row for row in board["rows"] if key_of(row) == key)["tgTps"] = 99.9
        index["keys"][0]["sessions"][0]["row"]["tgTps"] = 99.9
        self.canonical.write_text(json.dumps(board, indent=2) + "\n")
        self.index.write_text(json.dumps(index, indent=2) + "\n")
        self.assertEqual(self.node("validate_data.mjs", self.canonical).returncode, 0, "contract-valid alone")
        result = self.node("validate_data.mjs")
        self.assertEqual(result.returncode, 1)
        self.assertIn("does not reproduce from evidence/", result.stderr)
        self.bundle(fx.cpu_record("complete", "1767225600000-1-two", device="Fixture Tablet"))
        self.assert_rejected("does not reproduce from the hosted bundles", "--from-bundles", self.root / "evidence",
                             "--update", "--session", "1767225600000-1-two")
        self.canonical.write_text(good[0])
        self.index.write_text(good[1])
        self.assert_valid_data()
        for bundle in (self.root / "evidence").iterdir():
            shutil.rmtree(bundle)
        result = self.node("validate_data.mjs")
        self.assertEqual(result.returncode, 1)
        self.assertIn("bundle is not hosted", result.stderr)

    def test_replaced_rows_keep_the_kind_they_name(self):
        self.bundle(fx.cpu_record("complete", "1767225600000-0-pixel", device="Pixel 9"))
        self.publish("1767225600000-0-pixel", flags=["--replace-legacy"])
        index = load(self.index)
        entry = next(item for item in index["keys"] if item["key"] == LEGACY_KEY)
        entry["replacedRows"][0]["reason"] = "replace-failed"
        self.index.write_text(json.dumps(index, indent=2) + "\n")
        result = self.node("validate_data.mjs")
        self.assertEqual(result.returncode, 1)
        self.assertIn("a replace-failed entry must keep a failed row", result.stderr)

    def run_with_hook(self, fail_from, only):
        hook = Path(self.tmp.name) / "hook.mjs"
        hook.write_text(HOOK)
        env = {**os.environ, "XEBENCH_FAIL_RENAME_FROM": str(fail_from), "XEBENCH_FAIL_RENAME_ONLY": "1" if only else "0"}
        return subprocess.run(["node", "--import", hook.as_uri(), str(self.root / "scripts/aggregate.mjs"),
                               "--from-bundles", str(self.root / "evidence"), "--update", "--session",
                               "1767225600000-0-roll"], capture_output=True, text=True, env=env)

    def test_a_failed_second_rename_restores_the_first_file(self):
        self.bundle(fx.cpu_record("complete", "1767225600000-0-roll", device="Fixture Phone"))
        before = self.digests()
        result = self.run_with_hook(2, only=True)  # the index is renamed, data/benchmarks.json is not
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("injected rename failure", result.stderr)
        self.assertIn("were not modified", result.stderr)
        self.assertEqual(self.digests(), before)
        self.no_leftovers()

    def test_a_failed_restore_is_reported_not_hidden(self):
        self.bundle(fx.cpu_record("complete", "1767225600000-0-roll", device="Fixture Phone"))
        result = self.run_with_hook(2, only=False)  # the restore's rename fails too
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("restoring the earlier content failed too: index.json now holds the new content", result.stderr)
        self.assertIn("checkout -- data/", result.stderr)
        self.assertNotIn("not modified", result.stderr)
        self.no_leftovers()

    def test_newest_compares_instants_not_text(self):
        script = ("import {compareSessions, selectSession} from './scripts/lib/history.mjs';"
                  "const s = [{sessionId: 'a', timestampIso: '2026-03-01T00:00:00.500Z', validation: 'verified'},"
                  "{sessionId: 'b', timestampIso: '2026-03-01T00:00:00Z', validation: 'verified'},"
                  "{sessionId: 'c', timestampIso: '2026-03-01T00:00:00.250+00:00', validation: 'verified'}];"
                  "console.log(JSON.stringify([s.slice().sort(compareSessions).map((x) => x.sessionId),"
                  " selectSession(s).sessionId, selectSession(s.slice().reverse()).sessionId]));")
        result = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True,
                                cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [["b", "c", "a"], "a", "a"])


def consumer_fixtures(test):
    """Publish the fixture scenario into a small board and return {file name: text}."""
    real = load(ROOT / "data/benchmarks.json")
    vendor = next(row for row in real["rows"] if row["provenance"] == "vendor")
    legacy = next(row for row in real["rows"] if key_of(row) == LEGACY_KEY)
    outputs = {}
    for version, runtimes in (("1.2", True), ("1.1", False)):
        test.tearDown()
        test.setUp()
        start = {"schemaVersion": "1.1", "generatedAt": "2026-10-18T00:00:00Z",
                 "source": "https://github.com/a-ghorbani/xebench",
                 "description": "Consumer fixture: rows published from SYNTHETIC evidence bundles built from the "
                                "app-emitted test fixtures. Evidence URLs name the fixture bundles' real hashes but "
                                "do not resolve.",
                 "rowCount": 2, "rows": [vendor, legacy]}
        test.canonical.write_text(json.dumps(start, indent=2) + "\n")
        test.bundle(
            fx.cpu_record("complete", "1767225600000-0-pixel", timestamp=T["t0"], device="Pixel 9", runtimes=runtimes),
            fx.cpu_record("flagged", "1767225600000-1-pixel", timestamp=T["t1"], device="Pixel 9", runtimes=runtimes),
            fx.cpu_record("noExpectedHash", "1767225600000-2-phone", device="Fixture Phone", runtimes=runtimes),
            fx.cpu_record("interrupted", "1767225600000-3-tablet", device="Fixture Tablet", runtimes=runtimes))
        test.bundle(fx.accel_record("f8-llama-hexagon", "1767225600000-4-npu", device="Fixture Phone",
                                    runtimes=runtimes))
        test.publish("1767225600000-0-pixel", "1767225600000-1-pixel", "1767225600000-2-phone",
                     "1767225600000-3-tablet", "1767225600000-4-npu", flags=["--replace-legacy"])
        test.assert_valid_data()  # every published row reproduces from its hosted bundle
        board = load(test.canonical)
        test.assertEqual(board["schemaVersion"], version)
        board["generatedAt"] = "2026-10-18T00:00:00Z"
        outputs[f"board-{version}.json"] = json.dumps(board, indent=2) + "\n"
        outputs[f"sessions-{version}.json"] = test.index.read_text()
    return outputs


class ConsumerFixtureTests(Workspace):
    def test_fixtures_are_what_the_producer_writes(self):
        outputs = consumer_fixtures(self)
        if os.environ.get("XEBENCH_UPDATE_FIXTURES") == "1":
            for name, text in outputs.items():
                (FIXTURES / name).write_text(text)
        for name, text in outputs.items():
            with self.subTest(name):
                self.assertEqual((FIXTURES / name).read_text(), text,
                                 "regenerate with XEBENCH_UPDATE_FIXTURES=1 after an intended producer change")

    def test_fixtures_validate_and_cover_every_validation(self):
        index = load(FIXTURES / "index.json")
        self.assertEqual(sorted(item["file"] for item in index["fixtures"]),
                         sorted(path.name for path in FIXTURES.glob("*.json") if path.name != "index.json"))
        for item in index["fixtures"]:
            if item["kind"] == "board":
                result = self.node("validate_data.mjs", FIXTURES / item["file"])
                self.assertEqual(result.returncode, 0, result.stderr)
        for version in ("1.1", "1.2"):
            board, sessions = load(FIXTURES / f"board-{version}.json"), load(FIXTURES / f"sessions-{version}.json")
            validations = {row.get("validation") for row in board["rows"]}
            self.assertEqual(validations, {None, "verified", "unverified", "failed"})
            history = next(entry for entry in sessions["keys"] if entry["key"] == LEGACY_KEY)
            self.assertEqual([s["validation"] for s in history["sessions"]], ["verified", "failed"])
            self.assertEqual((history["displayed"], history["rule"]), ("1767225600000-0-pixel", "newest-verified"))
            self.assertEqual(history["replacedRows"][0]["reason"], "replace-legacy")


if __name__ == "__main__":
    unittest.main()
