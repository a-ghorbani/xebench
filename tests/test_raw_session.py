import copy
import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("raw_session", ROOT / "scripts/raw_session.py")
raw_session = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(raw_session)
FIXTURES = ROOT / "tests/fixtures/raw-session"
# Produced by xebenchapp/__tests__/rawSessionSchema.test.ts through runSession.
APP = json.loads((FIXTURES / "app-emitted.json").read_text())["records"]


def codes(record):
    return {item["code"] for item in raw_session.validate(record)}


def mutate(name, change):
    record = copy.deepcopy(APP[name])
    change(record)
    return record


class AppEmittedRecordTests(unittest.TestCase):
    def test_every_app_emitted_record_is_valid(self):
        self.assertEqual(set(APP), {"partial", "complete", "failed", "cleanupFailed", "flagged", "probeUnavailable",
                                    "inferenceFailed", "loadAndCleanupFailed", "nonFiniteMetrics",
                                    "noEngineInternal", "beforeProbeBusy", "afterProbeTimeout"})
        self.assertGreater(len(APP["inferenceFailed"]["coldRuns"][0]["error"]["message"]), 4096)
        self.assertIsNone(APP["nonFiniteMetrics"]["coldRuns"][0]["metrics"])
        for name, record in APP.items():
            with self.subTest(name=name):
                self.assertEqual(raw_session.validate(record), [])
                self.assertEqual(raw_session.validate_text(json.dumps(record)), [])

    def test_valid_partial_checkpoint_and_complete_session(self):
        partial, complete = APP["partial"], APP["complete"]
        self.assertEqual((partial["status"], len(partial["coldRuns"])), ("running", 1))
        self.assertEqual((complete["status"], len(complete["coldRuns"])), ("complete", complete["protocol"]["nColdRuns"]))
        self.assertEqual(complete["summary"]["decodeTps"]["n"], 4)

    def test_statistics_use_documented_interpolation(self):
        self.assertEqual(raw_session.aggregate([10, 20, 100]), {"median": 20, "iqr": 45, "min": 10, "max": 100, "n": 3})
        self.assertEqual(raw_session.aggregate([8]), {"median": 8, "iqr": 0, "min": 8, "max": 8, "n": 1})
        self.assertIsNone(raw_session.aggregate([]))
        complete = APP["complete"]
        for metric in raw_session.METRICS:
            values = [attempt[metric] for attempt in complete["coldRuns"]]
            self.assertEqual(raw_session.aggregate(values), complete["summary"][metric])


class RejectionTests(unittest.TestCase):
    def assertCodes(self, record, expected):
        self.assertEqual(codes(record), set(expected), raw_session.validate(record))

    def test_truncated_and_malformed_text(self):
        self.assertEqual({r["code"] for r in raw_session.validate_text((FIXTURES / "truncated.json").read_text())},
                         {"json.parse"})
        for text in ("", "{", '{"schema": "xebench-raw", "schemaVersion": 1'):
            self.assertEqual([r["code"] for r in raw_session.validate_text(text)], ["json.parse"])
        self.assertEqual(raw_session.validate_text('{"a": 1, "a": 2}')[0]["code"], "json.duplicate-key")
        self.assertEqual(raw_session.validate_text("[]")[0]["code"], "json.not-object")

    def test_non_finite_numbers(self):
        for name in ("non-finite.json", "overflow.json"):
            with self.subTest(name=name):
                reasons = raw_session.validate_text((FIXTURES / name).read_text())
                self.assertEqual([r["code"] for r in reasons], ["json.non-finite"])
        record = mutate("complete", lambda r: r["coldRuns"][1].update(ttftMs=float("inf")))
        self.assertEqual(raw_session.validate(record),
                         [{"code": "json.non-finite", "path": "/coldRuns/1/ttftMs", "message": "Numbers must be finite"}])

    def test_unknown_or_missing_schema_version(self):
        for version in (2, 0, "1", True, None, 1.5):
            with self.subTest(version=version):
                self.assertCodes(mutate("complete", lambda r: r.update(schemaVersion=version)), {"version.unsupported"})
        self.assertCodes(mutate("complete", lambda r: r.pop("schemaVersion")), {"version.missing"})
        self.assertCodes(mutate("complete", lambda r: r.update(schema="other")), {"record.schema"})

    def test_incomplete_records(self):
        self.assertCodes(mutate("complete", lambda r: r.pop("summary")), {"schema.required"})
        record = mutate("complete", lambda r: r["coldRuns"][2].pop("decodeTps"))
        self.assertEqual(raw_session.validate(record)[0]["path"], "/coldRuns/2/decodeTps")
        self.assertCodes(record, {"schema.required"})
        self.assertCodes(mutate("failed", lambda r: r["coldRuns"][-1].pop("error")), {"schema.required"})
        # Complete status without every configured repetition.
        self.assertCodes(mutate("complete", lambda r: r.update(coldRuns=r["coldRuns"][:3])),
                         {"status.attempts", "summary.count"})

    def test_wrong_repetition_count(self):
        def extra(record):
            record["coldRuns"].append(dict(record["coldRuns"][-1], runIndex=4))
        self.assertCodes(mutate("complete", extra), {"attempts.count", "status.attempts", "summary.count"})
        def more_configured(record):
            record["protocol"]["nColdRuns"] = record["configuration"]["nColdRuns"] = 5
        self.assertCodes(mutate("complete", more_configured), {"status.attempts"})
        self.assertCodes(mutate("complete", lambda r: r["summary"]["decodeTps"].update(n=3)), {"summary.count"})
        self.assertCodes(mutate("complete", lambda r: r.update(coldRuns=[])), {"schema.min-items"})

    def test_summary_must_match_repetitions(self):
        reasons = raw_session.validate(mutate("complete", lambda r: r["summary"]["decodeTps"].update(median=30.5)))
        self.assertEqual([(r["code"], r["path"]) for r in reasons], [("summary.mismatch", "/summary/decodeTps/median")])
        self.assertCodes(mutate("complete", lambda r: r["summary"]["ttftMs"].update(iqr=0)), {"summary.mismatch"})
        self.assertCodes(mutate("complete", lambda r: r["coldRuns"][0].update(loadMs=99)), {"summary.mismatch"})
        self.assertCodes(mutate("complete", lambda r: r["summary"].update(prefillTps=None)), {"summary.mismatch"})
        no_success = mutate("cleanupFailed", lambda r: r["summary"].update(
            loadMs={"median": 17, "iqr": 0, "min": 17, "max": 17, "n": 1}))
        self.assertCodes(no_success, {"summary.mismatch"})

    def test_status_must_agree_with_attempts(self):
        self.assertCodes(mutate("failed", lambda r: r.update(status="complete")), {"status.attempts"})
        self.assertCodes(mutate("failed", lambda r: r.update(status="running")), {"status.attempts"})
        self.assertCodes(mutate("complete", lambda r: r.update(status="failed")), {"status.attempts"})
        self.assertCodes(mutate("complete", lambda r: r.update(status="running")), {"status.attempts"})
        self.assertCodes(mutate("partial", lambda r: r.update(status="complete")), {"status.attempts"})
        def failed_first(record):
            record["coldRuns"].reverse()
            for index, attempt in enumerate(record["coldRuns"]):
                attempt["runIndex"] = index
        self.assertCodes(mutate("failed", failed_first), {"status.attempts"})
        self.assertCodes(mutate("complete", lambda r: r["coldRuns"][1].update(runIndex=7)), {"attempts.run-index"})
        self.assertCodes(mutate("complete", lambda r: r.update(status="paused")), {"schema.enum"})

    def test_configuration_agreement(self):
        self.assertCodes(mutate("complete", lambda r: r["protocol"].update(nThreads=8)), {"configuration.run-mismatch"})
        self.assertCodes(mutate("complete", lambda r: r.update(quant="Q8_0")), {"configuration.run-mismatch"})
        self.assertCodes(mutate("complete", lambda r: r.update(configurationRunId="other")), {"configuration.unknown-run"})
        self.assertCodes(mutate("complete", lambda r: r["protocol"].update(cooldownSec=0)),
                         {"protocol.configuration-mismatch"})
        self.assertCodes(mutate("complete", lambda r: r["configuration"]["runs"].append(r["configuration"]["runs"][0])),
                         {"configuration.duplicate-run-id"})

    def test_guards_cannot_hide_failed_conditions_or_claim_verification(self):
        self.assertCodes(mutate("flagged", lambda r: r.update(guardsPassed=None)), {"guards.inconsistent"})
        self.assertCodes(mutate("flagged", lambda r: r.update(guardNotes=[])), {"guards.inconsistent"})
        self.assertCodes(mutate("complete", lambda r: r.update(guardsPassed=True)), {"schema.enum"})

    def test_strict_core_and_permissive_extension_points(self):
        self.assertCodes(mutate("complete", lambda r: r.update(extra=1)), {"schema.additional-property"})
        self.assertCodes(mutate("complete", lambda r: r["coldRuns"][0].update(extra=1)), {"schema.additional-property"})
        self.assertCodes(mutate("complete", lambda r: r["summary"]["loadMs"].update(p90=1)), {"schema.additional-property"})
        self.assertCodes(mutate("complete", lambda r: r.update(observedBackend="cpu")), {"schema.const"})
        self.assertCodes(mutate("complete", lambda r: r["coldRuns"][0].update(decodeTps=0)), {"schema.exclusive-minimum"})
        self.assertCodes(mutate("complete", lambda r: r["coldRuns"][0].update(promptTokens=1.5)), {"schema.type"})
        self.assertCodes(mutate("complete", lambda r: r["coldRuns"][0].update(status="skipped")), {"schema.one-of"})
        self.assertCodes(mutate("complete", lambda r: r["artifact"].update(sha256="A" * 64)), {"schema.pattern"})
        self.assertCodes(mutate("complete", lambda r: r.update(modelFile="../x.gguf")), {"schema.pattern"})
        self.assertCodes(mutate("complete", lambda r: r["coldRuns"][0]["conditions"]["before"].update(
            batteryPct="80")), {"schema.type"})
        # Later condition/protocol work may add fields to these objects.
        def extend(record):
            attempt = record["coldRuns"][0]
            attempt["conditions"].update(readiness={"waitedMs": 0})
            attempt["conditions"]["before"].update(headroom=0.5)
            attempt["conditions"]["beforeAssessment"].update(policy="x")
            record["protocol"].update(runKind="fresh", order={"seed": 1})
            record["conditionPolicy"].update(charging={"publication": "exclude"})
            record["configuration"].update(seed=1)
            record["configuration"]["runs"][0].update(runKind="fresh")
        self.assertCodes(mutate("complete", extend), set())

    def test_reasons_are_machine_readable_and_bounded(self):
        def many(record):
            for attempt in record["coldRuns"]:
                attempt["conditions"]["before"]["unavailable"] = list(range(200))
        reasons = raw_session.validate(mutate("complete", many))
        self.assertEqual(len(reasons), raw_session.MAX_REASONS + 1)
        self.assertEqual(reasons[-1]["code"], "reasons.truncated")
        self.assertTrue(all(set(r) == {"code", "path", "message"} for r in reasons))


class SchemaAndCliTests(unittest.TestCase):
    def test_schema_uses_only_supported_keywords(self):
        raw_session.load_schema()
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "schema.json"
            for bad in ({"type": "object", "properties": {"a": {"format": "date-time"}}},
                        {"properties": {"properties": {"format": "date-time"}}},
                        {"type": "decimal"},
                        {"type": "array", "items": True},
                        {"$defs": {"a": {"type": "string"}}, "$ref": "#/$defs/a", "minLength": 1}):
                with self.subTest(schema=bad):
                    path.write_text(json.dumps(bad))
                    with self.assertRaises(ValueError):
                        raw_session.load_schema(path)

    def test_pre_condition_snapshot_records_remain_valid_but_never_mixed(self):
        def strip(record):
            record.pop("conditionPolicy")
            for attempt in record["coldRuns"]:
                attempt.pop("conditions")
        for name in ("complete", "failed", "partial"):
            with self.subTest(name=name):
                self.assertEqual(raw_session.validate(mutate(name, strip)), [])
        self.assertEqual(codes(mutate("complete", lambda r: r.pop("conditionPolicy"))), {"conditions.inconsistent"})
        self.assertEqual(codes(mutate("complete", lambda r: r["coldRuns"][1].pop("conditions"))),
                         {"conditions.inconsistent"})
        def strip_flagged(record):
            strip(record)
            record["guardNotes"] = record["guardNotes"][:1]
        self.assertEqual(codes(mutate("flagged", strip_flagged)), {"guards.inconsistent"})
        self.assertEqual(raw_session.validate(mutate("beforeProbeBusy", lambda r: None)), [])
        self.assertEqual(codes(mutate("beforeProbeBusy", strip)), {"conditions.inconsistent"})

    def test_patterns_are_anchored_like_ecma_regexes(self):
        for change in (lambda r: r.update(runId=r["runId"] + "\n"),
                       lambda r: r["artifact"].update(sha256="a" * 64 + "\n"),
                       lambda r: r.update(timestampIso=r["timestampIso"] + "\n")):
            self.assertEqual(codes(mutate("complete", change)), {"schema.pattern"})

    def test_hostile_json_is_rejected_not_raised(self):
        # Deep nesting either fails to parse or is scanned iteratively; huge
        # integers exceed Python's digit limit. None may escape as an exception.
        cases = {"[" * 100000 + "]" * 100000: {"json.parse", "json.not-object"},
                 '{"a": ' + "1" * 5000 + "}": {"json.parse"},
                 '{"a": [' * 1800 + "1" + "]}" * 1800: {"json.parse", "record.schema"}}
        for text, allowed in cases.items():
            with self.subTest(size=len(text)):
                reasons = raw_session.validate_text(text)
                self.assertEqual(len(reasons), 1)
                self.assertIn(reasons[0]["code"], allowed)

    def test_cli_reports_each_record(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(raw_session.main([str(FIXTURES / "app-emitted.json")]), 0)
        results = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual(len(results), len(APP))
        self.assertTrue(all(result["valid"] for result in results))
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(raw_session.main([str(FIXTURES / "truncated.json")]), 1)
        self.assertEqual(json.loads(out.getvalue())["reasons"][0]["code"], "json.parse")


if __name__ == "__main__":
    unittest.main()
