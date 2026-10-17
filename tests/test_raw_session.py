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
# Frozen output of the cpu-reference-v1 app (PR #13); historical records must keep validating.
LEGACY = json.loads((FIXTURES / "app-emitted-v1.json").read_text())["records"]


def codes(record):
    return {item["code"] for item in raw_session.validate(record)}


def mutate(name, change, source=None):
    """Mutate a record; the cpu-reference-v1 rule tests below use the legacy fixture."""
    record = copy.deepcopy((LEGACY if source is None else source)[name])
    change(record)
    return record


class AppEmittedRecordTests(unittest.TestCase):
    def test_every_app_emitted_record_is_valid(self):
        self.assertEqual(set(LEGACY), {"partial", "complete", "failed", "cleanupFailed", "flagged", "probeUnavailable",
                                       "inferenceFailed", "loadAndCleanupFailed", "nonFiniteMetrics",
                                       "noEngineInternal", "beforeProbeBusy", "afterProbeTimeout"})
        self.assertEqual(set(APP), set(LEGACY) | {
            "interrupted", "monitorIncomplete", "monitorUnavailable", "monitorStuck", "monitorBusy",
            "readinessTimeout", "orderedLaterRun", "configurationAborted", "setupFailed", "chargingDuringRun",
            "chargingTimeout", "chargingUnknown", "inferenceFailedThenProbePending", "monitorStartTimeout",
            "cleanupFailedProbePending", "laterReadinessTimeout", "headroomUnavailable", "monitorStopError",
            "decimatedSamples", "onBattery", "riseOverride"})
        for records in (APP, LEGACY):
            self.assertGreater(len(records["inferenceFailed"]["coldRuns"][0]["error"]["message"]), 4096)
            self.assertIsNone(records["nonFiniteMetrics"]["coldRuns"][0]["metrics"])
            for name, record in records.items():
                with self.subTest(name=name, protocol=record["protocol"]["version"]):
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


def v2(name, change):
    return mutate(name, change, APP)


class ConditionsV2Tests(unittest.TestCase):
    """cpu-reference-v2 records: readiness, sampling, verdict and order are recomputed."""

    def assertCodes(self, record, expected):
        self.assertEqual(codes(record), set(expected), raw_session.validate(record))

    def test_branches_are_selected_by_protocol_and_never_mixed(self):
        self.assertEqual(APP["complete"]["protocol"]["version"], "cpu-reference-v2")
        self.assertEqual(LEGACY["complete"]["protocol"]["version"], "cpu-reference-v1")
        # New evidence fields are not accepted on the legacy branch, and are required on v2.
        self.assertCodes(mutate("complete", lambda r: r.update(stopReason=None)), {"schema.additional-property"})
        for field in ("runOrder", "repetitionsPlanned", "stopReason", "chargingPolicy", "conditionVerdict",
                      "conditionPolicy"):
            with self.subTest(field=field):
                self.assertCodes(v2("complete", lambda r: r.pop(field)), {"schema.required"})
        self.assertCodes(v2("complete", lambda r: r["coldRuns"][0].pop("readiness")), {"schema.required"})
        # A wrong harness version is reported where it is, on the branch the rest of the record selects.
        reasons = raw_session.validate(v2("complete", lambda r: r.update(harnessVersion="xebench-0.5-reference")))
        self.assertEqual([(r["code"], r["path"]) for r in reasons], [("schema.const", "/harnessVersion")])
        # Legacy records keep precise messages through the top-level oneOf.
        for change, expected in ((lambda r: r.pop("harnessVersion"), ("schema.required", "/harnessVersion")),
                                 (lambda r: r.update(harnessVersion="x"), ("schema.const", "/harnessVersion")),
                                 (lambda r: r["coldRuns"][0].update(loadMs=-1), ("schema.minimum", "/coldRuns/0/loadMs"))):
            self.assertEqual([(r["code"], r["path"]) for r in raw_session.validate(mutate("complete", change))],
                             [expected])
        # A v2 failed attempt with a null instance is still reported as a failed attempt.
        reasons = raw_session.validate(v2("beforeProbeBusy", lambda r: r["coldRuns"][0].update(cooldownMs=-5)))
        self.assertEqual([(r["code"], r["path"]) for r in reasons], [("schema.minimum", "/coldRuns/0/cooldownMs")])
        self.assertCodes(v2("complete", lambda r: r["protocol"].update(version="cpu-reference-v1")), {"schema.const"})
        self.assertCodes(v2("complete", lambda r: r.update(guardsPassed=True)), {"schema.enum"})

    def test_skipped_repetitions_and_stop_reasons(self):
        skipped = APP["readinessTimeout"]["coldRuns"][-1]
        self.assertEqual((skipped["status"], skipped["instance"], skipped["conditions"]["after"]), ("skipped", None, None))
        self.assertCodes(v2("readinessTimeout", lambda r: r.update(stopReason="repetition-failed")), {"status.attempts"})
        self.assertCodes(v2("readinessTimeout", lambda r: r.update(status="complete", stopReason=None)),
                         {"status.attempts"})
        self.assertCodes(v2("failed", lambda r: r.update(stopReason=None)), {"status.attempts"})
        self.assertCodes(v2("failed", lambda r: r.update(stopReason="cleanup-failed")), {"status.attempts"})
        self.assertCodes(v2("cleanupFailed", lambda r: r.update(stopReason="repetition-failed")), {"status.attempts"})
        self.assertCodes(v2("inferenceFailed", lambda r: r.update(stopReason="condition-probe-pending")),
                         {"status.attempts"})
        self.assertCodes(v2("complete", lambda r: r.update(stopReason="readiness-timeout")), {"status.attempts"})
        self.assertCodes(v2("partial", lambda r: r.update(status="failed")), {"status.attempts"})
        # A skipped repetition never carries metrics or run-time evidence.
        self.assertCodes(v2("readinessTimeout", lambda r: r["coldRuns"][-1].update(loadMs=17)),
                         {"schema.additional-property"})
        self.assertCodes(v2("readinessTimeout", lambda r: r["coldRuns"][-1]["conditions"].update(
            after=r["coldRuns"][-1]["conditions"]["before"], afterAssessment={"status": "pass", "reasons": []})),
            {"conditions.inconsistent"})
        self.assertCodes(v2("readinessTimeout", lambda r: r["coldRuns"][-1].update(reason="other")), {"schema.enum"})

    def test_unmeasured_runs_have_no_attempts_or_artifact(self):
        self.assertEqual(APP["configurationAborted"]["coldRuns"], [])
        self.assertIsNone(APP["setupFailed"]["artifact"])
        self.assertCodes(v2("complete", lambda r: r.update(artifact=None)), {"status.artifact"})
        self.assertCodes(v2("setupFailed", lambda r: r.update(artifact=APP["complete"]["artifact"])), {"status.artifact"})
        self.assertCodes(v2("complete", lambda r: r.update(coldRuns=[])), {"status.attempts", "summary.mismatch",
                                                                         "guards.recomputed", "guards.inconsistent"})

    def test_unknown_evidence_cannot_be_recorded_as_ready_or_passing(self):
        def unknown_thermal(record):
            before = record["coldRuns"][0]["conditions"]["before"]
            before["thermalStatus"] = None
            before["unavailable"].append("thermalStatus")
        self.assertCodes(v2("complete", unknown_thermal), {"readiness.recomputed", "conditions.recomputed"})
        # Even with assessments rewritten to hide it, the verdict cannot pass.
        def hidden(record):
            unknown_thermal(record)
            record["coldRuns"][0]["readiness"].update(status="ready")
        self.assertIn("readiness.recomputed", codes(v2("complete", hidden)))
        self.assertCodes(v2("monitorIncomplete", lambda r: r.update(
            conditionVerdict=dict(r["conditionVerdict"], status="pass"))), {"guards.recomputed"})
        self.assertCodes(v2("monitorIncomplete", lambda r: r["coldRuns"][0]["conditions"].update(
            duringAssessment={"status": "pass", "reasons": []})),
            {"conditions.recomputed", "guards.recomputed", "guards.inconsistent"})
        self.assertCodes(v2("probeUnavailable", lambda r: r["coldRuns"][0]["readiness"].update(status="ready")),
                         {"readiness.inconsistent", "readiness.recomputed"})
        def no_samples(record):
            during = record["coldRuns"][0]["conditions"]["during"]
            during.update(samples=[], ticks=0)
            during["summary"].update(samples=0, maxGapMs=1500,
                                     externalPower={"connected": 0, "disconnected": 0, "unknown": 0},
                                     chargingStates={"notCharging": 0, "charging": 0, "onBattery": 0, "unknown": 0})
        # maxGapMs left at 1500 no longer matches the (empty) samples: both checks fire, and an
        # unsampled run cannot keep its satisfied charging status.
        self.assertCodes(v2("complete", no_samples), {"conditions.recomputed", "conditions.monitor",
                                                      "guards.recomputed"})

    def test_failures_cannot_be_hidden(self):
        flagged = APP["flagged"]
        self.assertEqual(flagged["conditionVerdict"]["reasons"], ["severe-thermal-state", "power-saving"])
        self.assertCodes(v2("flagged", lambda r: r.update(guardsPassed=None)), {"guards.inconsistent"})
        self.assertCodes(v2("flagged", lambda r: r.update(guardNotes=r["guardNotes"][:1])), {"guards.inconsistent"})
        self.assertCodes(v2("flagged", lambda r: r["coldRuns"][0]["conditions"]["during"]["summary"].update(failures=[])),
                         {"conditions.monitor", "conditions.recomputed"})
        self.assertCodes(v2("interrupted", lambda r: r["coldRuns"][0]["conditions"]["after"].update(focusLossCount=0)),
                         {"conditions.recomputed"})
        self.assertCodes(v2("monitorStuck", lambda r: r.update(conditionVerdict=dict(r["conditionVerdict"],
                         status="unverified", reasons=[]), guardsPassed=None)), {"guards.recomputed", "guards.inconsistent"})
        self.assertCodes(v2("complete", lambda r: r["conditionPolicy"]["readiness"].update(maxBatteryTempC=60)),
                         {"conditions.policy"})
        self.assertCodes(v2("complete", lambda r: r.update(conditionVerdict=dict(r["conditionVerdict"],
                                                                                 charging="unknown"))),
                         {"guards.recomputed"})

    def test_charging_policy_is_recomputed_and_unknown_is_never_eligible(self):
        self.assertEqual([APP[name]["conditionVerdict"]["charging"] for name in
                          ("complete", "chargingDuringRun", "chargingTimeout", "chargingUnknown")],
                         ["satisfied", "violated", "violated", "unknown"])
        self.assertEqual(APP["chargingTimeout"]["coldRuns"][0]["reason"], "charging")
        self.assertEqual(raw_session.charging_state({"batteryStatus": 2, "currentNowUa": 10}), "charging")
        # current_now is diagnostic only; discharging is not one of the eligible statuses.
        for current in (-5000, 0, 900000, None):
            self.assertEqual(raw_session.charging_state({"batteryStatus": None, "currentNowUa": current}), "unknown")
        # DISCHARGING is the known on-battery state (v3); the earlier v2 policy counted it as unknown.
        self.assertEqual(raw_session.charging_state({"batteryStatus": 3}), "onBattery")
        self.assertEqual(raw_session.charging_state({"batteryStatus": 3}, raw_session.CHARGING_POLICY_V2), "unknown")
        self.assertEqual(APP["onBattery"]["conditionVerdict"]["charging"], "on-battery")
        self.assertCodes(v2("onBattery", lambda r: r["coldRuns"][0]["conditions"].update(charging="satisfied")),
                         {"conditions.recomputed"})
        self.assertCodes(v2("onBattery", lambda r: r["coldRuns"][0]["conditions"]["during"]["summary"]["chargingStates"]
                            .pop("onBattery")), {"conditions.monitor"})
        # Records written under the earlier status-only charging-policy-v2 keep validating with its rules.
        def earlier_policy(record):
            record["chargingPolicy"] = dict(raw_session.CHARGING_POLICY_V2)
            for attempt in record["coldRuns"]:
                attempt["conditions"]["during"]["summary"]["chargingStates"].pop("onBattery")
        self.assertCodes(v2("complete", earlier_policy), set())
        self.assertCodes(v2("onBattery", earlier_policy), {"conditions.monitor", "conditions.recomputed", "guards.recomputed"})
        # A configured rise bound is recorded and used when recomputing readiness.
        override = APP["riseOverride"]
        self.assertEqual(override["protocol"]["maxBatteryTempRiseC"], 0.5)
        self.assertEqual(override["coldRuns"][0]["readiness"]["reasons"], ["battery-not-cooled"])
        self.assertCodes(v2("riseOverride", lambda r: (r["protocol"].update(maxBatteryTempRiseC=2),
                                                       r["configuration"].update(maxBatteryTempRiseC=2))),
                         {"readiness.recomputed"})
        self.assertCodes(v2("riseOverride", lambda r: r["protocol"].update(maxBatteryTempRiseC=3)),
                         {"protocol.configuration-mismatch", "readiness.recomputed"})
        self.assertEqual(raw_session.charging_state({"batteryStatus": 4}), "notCharging")
        def hide_charging(record):
            summary = record["coldRuns"][0]["conditions"]["during"]["summary"]
            summary["chargingStates"].update(charging=0, notCharging=summary["samples"])
        self.assertCodes(v2("chargingDuringRun", hide_charging), {"conditions.monitor", "conditions.recomputed",
                                                                  "guards.recomputed"})
        self.assertCodes(v2("chargingUnknown", lambda r: r["coldRuns"][0]["conditions"].update(charging="satisfied")),
                         {"conditions.recomputed"})
        self.assertCodes(v2("chargingUnknown", lambda r: r.update(conditionVerdict=dict(r["conditionVerdict"],
                                                                                       charging="satisfied"))),
                         {"guards.recomputed"})
        # An unsampled stretch (here an expired monitor) is unknown, even when every persisted
        # sample says not charging, under both policy versions (no leniency by relabelling).
        self.assertEqual(APP["monitorIncomplete"]["coldRuns"][0]["conditions"]["during"]["expired"], True)
        self.assertEqual(APP["monitorIncomplete"]["conditionVerdict"]["charging"], "unknown")
        def claim_satisfied(record):
            record["coldRuns"][0]["conditions"]["charging"] = "satisfied"
            record["conditionVerdict"]["charging"] = "satisfied"
        self.assertCodes(v2("monitorIncomplete", claim_satisfied), {"conditions.recomputed", "guards.recomputed"})
        self.assertCodes(v2("monitorIncomplete", lambda r: (earlier_policy(r), claim_satisfied(r))),
                         {"conditions.recomputed", "guards.recomputed"})
        self.assertCodes(v2("monitorIncomplete", earlier_policy), set())
        self.assertCodes(v2("chargingTimeout", lambda r: r["coldRuns"][0].update(reason="readiness-timeout")),
                         {"readiness.inconsistent"})
        self.assertCodes(v2("readinessTimeout", lambda r: r["coldRuns"][-1].update(reason="charging")),
                         {"readiness.inconsistent"})
        self.assertCodes(v2("complete", lambda r: r["chargingPolicy"].update(notChargingStatuses=[2, 4])),
                         {"schema.enum"})
        self.assertCodes(v2("complete", lambda r: r["chargingPolicy"].update(notChargingStatuses=[5, 4])),
                         {"conditions.policy"})
        def charging_before(record):
            before = record["coldRuns"][0]["conditions"]["before"]
            before["batteryStatus"] = 2
        self.assertIn("readiness.recomputed", codes(v2("complete", charging_before)))

    def test_snapshots_are_typed_and_samples_support_the_summary(self):
        # Type confusion in required or optional signals is rejected by the v2 snapshot schema.
        for change in ({"focusLossCount": "unknown"}, {"focusLossCount": True}, {"thermalHeadroom": False},
                       {"batteryTempC": -1000}, {"thermalHeadroom": -50}, {"batteryStatus": 2.5}):
            with self.subTest(change=change):
                record = v2("complete", lambda r: [attempt["conditions"]["before"].update(change)
                                                   for attempt in r["coldRuns"]])
                self.assertIn(codes(record) & {"schema.type", "schema.minimum"}, ({"schema.type"}, {"schema.minimum"}))
        def unavailable_samples(record):
            for sample in record["coldRuns"][0]["conditions"]["during"]["samples"]:
                sample.update(probeStatus="unavailable", thermalStatus=None)
        self.assertIn("conditions.monitor", codes(v2("complete", unavailable_samples)))
        self.assertIn("conditions.monitor", codes(v2("complete", lambda r: r["coldRuns"][0]["conditions"]["during"]
                                                     .update(samples=[]))))
        def charging_samples(record):
            for sample in record["coldRuns"][0]["conditions"]["during"]["samples"]:
                sample.update(batteryStatus=2)
        self.assertIn("conditions.monitor", codes(v2("complete", charging_samples)))
        def reversed_samples(record):
            record["coldRuns"][0]["conditions"]["during"]["samples"].reverse()
        self.assertIn("conditions.monitor", codes(v2("complete", reversed_samples)))
        decimated = APP["decimatedSamples"]["coldRuns"][0]["conditions"]["during"]
        self.assertEqual((len(decimated["samples"]), decimated["summary"]["samples"]), (30, 45))
        self.assertIn("conditions.monitor", codes(v2("decimatedSamples", lambda r: r["coldRuns"][0]["conditions"]
                                                     ["during"]["summary"]["chargingStates"].update(notCharging=10))))
        # A sparse interval cannot claim coverage.
        self.assertIn("conditions.recomputed", codes(v2("complete", lambda r: r["coldRuns"][0]["conditions"]["during"]
                                                        .update(intervalMs=1e9))))

    def test_contradictions_and_crashes_are_reasons_not_exceptions(self):
        self.assertCodes(v2("orderedLaterRun", lambda r: r["runOrder"].update(seed=1.2345e4)),
                         {"record.unprocessable"})
        self.assertCodes(v2("orderedLaterRun", lambda r: r["runOrder"].update(position=1.0)), {"record.unprocessable"})
        self.assertCodes(v2("inferenceFailedThenProbePending", lambda r: r.update(stopReason="repetition-failed")),
                         {"status.attempts"})
        self.assertCodes(v2("beforeProbeBusy", lambda r: r["coldRuns"][0]["conditions"]["before"].update(
            probeStatus="complete")), {"readiness.inconsistent"})
        self.assertIn("readiness.inconsistent", codes(v2("readinessTimeout", lambda r: r["coldRuns"][-1]["readiness"]
                                                         .update(reasons=[]))))
        self.assertIn("instance.inconsistent", codes(v2("failed", lambda r: r["coldRuns"][-1].update(instance=None))))
        self.assertCodes(v2("monitorIncomplete", lambda r: r.update(guardNotes=r["guardNotes"][:1])),
                         {"guards.inconsistent"})
        self.assertCodes(v2("complete", lambda r: r["guardNotes"].append("Run conditions not fully verified")),
                         {"guards.inconsistent"})
        # Booleans never equal numbers in the published-policy comparison.
        self.assertCodes(v2("complete", lambda r: r["conditionPolicy"]["run"].update(maxThermalStatus=False)),
                         {"conditions.policy"})
        self.assertCodes(v2("setupFailed", lambda r: r["guardNotes"].__setitem__(-1, "Not run: /home/user/x")),
                         {"guards.inconsistent"})

    def test_run_order_cooldown_and_instances_are_consistent(self):
        order = APP["orderedLaterRun"]["runOrder"]
        self.assertEqual(raw_session.seeded_order(["a", "b", "c", "d", "e"], 42), list("aecbd"))  # order.test.ts
        self.assertEqual(raw_session.seeded_order([run["id"] for run in APP["orderedLaterRun"]["configuration"]["runs"]],
                                                  order["seed"]), order["order"])
        self.assertCodes(v2("orderedLaterRun", lambda r: r["runOrder"].update(seed=order["seed"] + 1)),
                         {"order.recomputed", "order.inconsistent"})
        self.assertCodes(v2("orderedLaterRun", lambda r: r["runOrder"].update(position=1 - order["position"])),
                         {"order.inconsistent"})
        self.assertCodes(v2("orderedLaterRun", lambda r: r["runOrder"].update(seedSource="generated")),
                         {"order.inconsistent"})
        self.assertCodes(v2("complete", lambda r: r["coldRuns"][1].update(cooldownMs=0)), {"attempts.cooldown"})
        self.assertCodes(v2("complete", lambda r: r["coldRuns"][1]["instance"].update(modelLoadsBeforeInProcess=0)),
                         {"instance.inconsistent"})
        self.assertCodes(v2("complete", lambda r: r["coldRuns"][1]["instance"].update(processInstanceId="other-proc")),
                         {"instance.inconsistent"})
        self.assertCodes(v2("complete", lambda r: r["coldRuns"][1]["readiness"].update(baselineBatteryTempC=20)),
                         {"readiness.inconsistent", "readiness.recomputed"})
        self.assertCodes(v2("complete", lambda r: r.update(repetitionsPlanned=3)),
                         {"protocol.configuration-mismatch", "status.attempts"})
        self.assertCodes(v2("complete", lambda r: r["protocol"].update(readinessTimeoutSec=60)),
                         {"protocol.configuration-mismatch", "readiness.inconsistent"})


class SchemaAndCliTests(unittest.TestCase):
    def test_schema_uses_only_supported_keywords(self):
        raw_session.load_schema()
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "schema.json"
            for bad in ({"type": "object", "properties": {"a": {"format": "date-time"}}},
                        {"properties": {"properties": {"format": "date-time"}}},
                        {"type": "decimal"},
                        {"type": "array", "items": True},
                        {"$defs": {"a": {"type": "string"}}, "$ref": "#/$defs/a", "minLength": 1},
                        {"properties": {"a": {"$ref": "#/$defs/missing"}}},
                        {"properties": {"a": {"$ref": "https://example.com/x.json"}}},
                        {"properties": {"a": {"type": "string", "pattern": "("}}}):
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

    def test_integers_beyond_double_range_are_non_finite(self):
        # Below Python's digit limit but Infinity to the app's JSON.parse; summary
        # recomputation must not raise OverflowError on them.
        huge = "1" + "0" * 400
        for name, needle, path in (("partial", '"median": 17', "/summary/loadMs/median"),
                                   ("complete", '"loadMs": 17', "/coldRuns/0/loadMs"),
                                   ("complete", '"sizeBytes": 770000000', "/artifact/sizeBytes")):
            with self.subTest(path=path):
                text = json.dumps(APP[name]).replace(needle, needle.split(":")[0] + ": -" + huge, 1)
                self.assertEqual([(r["code"], r["path"]) for r in raw_session.validate_text(text)],
                                 [("json.non-finite", path)])
        record = mutate("complete", lambda r: r["coldRuns"][0].update(loadMs=10 ** 400))
        self.assertEqual(codes(record), {"json.non-finite"})
        # The largest exact doubles stay valid numbers.
        self.assertEqual(raw_session.parse(str(2 ** 1023)), 2 ** 1023)

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
        # No records at all must not pass vacuously.
        with tempfile.TemporaryDirectory() as root:
            for name, text in (("empty.json", "[]"), ("mapping.json", '{"records": {}}'), ("lines.jsonl", "")):
                with self.subTest(name=name), redirect_stdout(io.StringIO()):
                    path = Path(root) / name
                    path.write_text(text)
                    self.assertEqual(raw_session.main([str(path)]), 1)


if __name__ == "__main__":
    unittest.main()
