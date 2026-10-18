"""publication-policy-v1 (scripts/publication_policy.py).

The policy is a pure function of recomputed facts. These tests cover every check alone,
every pair of checks together (the decision is always the worst outcome; unknown is
never a pass), the refusals, and the facts recomputed from real-shaped bundles built
from the app-emitted fixtures.
"""
import copy
import importlib.util
import itertools
import json
from pathlib import Path
import tempfile
import unittest

import bundle_fixtures as fx

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("publication_policy", ROOT / "scripts/publication_policy.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)

PASS, UNKNOWN, FAIL = "pass", "unknown", "fail"
HASH = "a" * 64


def facts(**changes):
    """Facts of a session that passes every check (cpu-reference-v3, llama.cpp CPU)."""
    base = {
        "sessionId": "s-1", "protocolVersion": "cpu-reference-v3", "engine": "llama.cpp", "backend": "cpu",
        "purpose": None, "status": "complete", "stopReason": None, "repetitionsPlanned": 3, "attempts": 3,
        "measured": 3, "experiment": [], "chargingPolicy": "charging-policy-v3",
        "conditions": {"session": "pass", "repetitions": [
            {"runIndex": i, "verdict": "pass", "charging": "satisfied", "covered": True} for i in range(3)]},
        "artifact": {"sha256": HASH, "recordedExpected": HASH, "configuredSha256": HASH},
        "backendEvidence": "confirmed", "correctness": None, "comparison": None,
        "build": {"status": "reported", "gitCommit": "c" * 40, "gitDirty": False, "flavor": "reference",
                  "buildType": "release"},
        "appIdentity": "matched",
    }
    base.update(copy.deepcopy(changes))
    return base


def accel(**changes):
    """An accelerator-v2 llama.cpp NPU session that passes every check."""
    return facts(**{"protocolVersion": "accelerator-v2", "backend": "npu", "purpose": "measurement",
                    "correctness": "pass", "comparison": "valid",
                    "build": {"status": "reported", "gitCommit": "c" * 40, "gitDirty": False,
                              "flavor": "experimental", "buildType": "release"}, **changes})


def rep(index=1, **changes):
    def mutate(f):
        f["conditions"]["repetitions"][index].update(changes)
    return mutate


def setter(**changes):
    def mutate(f):
        f.update(copy.deepcopy(changes))
    return mutate


def build(**changes):
    def mutate(f):
        f["build"] = None if changes.get("none") else {**f["build"], **changes}
    return mutate


def artifact(**changes):
    def mutate(f):
        f["artifact"] = None if changes.get("none") else {**f["artifact"], **changes}
    return mutate


def session_verdict(value):
    def mutate(f):
        f["conditions"]["session"] = value
    return mutate


# Each dimension: (label, mutation, check, outcome). The first option of each passes.
DIMENSIONS = {
    "complete": [
        ("complete", setter(), "complete", PASS),
        ("stopped", setter(status="failed", stopReason="readiness-timeout", measured=2), "complete", FAIL),
        ("running", setter(status="running", measured=2, attempts=2), "complete", UNKNOWN),
        ("fewer attempts than planned", setter(attempts=2, measured=2), "complete", UNKNOWN),
    ],
    "session-verdict": [
        ("pass", session_verdict("pass"), "conditions", PASS),
        ("fail", session_verdict("fail"), "conditions", FAIL),
        ("unverified", session_verdict("unverified"), "conditions", UNKNOWN),
        ("garbage", session_verdict("passed"), "conditions", UNKNOWN),
    ],
    "repetition-verdict": [
        ("pass", rep(verdict="pass"), "conditions", PASS),
        ("fail", rep(verdict="fail"), "conditions", FAIL),
        ("unverified", rep(verdict="unverified"), "conditions", UNKNOWN),
        ("missing", rep(verdict=None), "conditions", UNKNOWN),
    ],
    "charging": [
        ("satisfied", rep(charging="satisfied"), "charging", PASS),
        ("violated", rep(charging="violated"), "charging", FAIL),
        ("on-battery", rep(charging="on-battery"), "charging", FAIL),
        ("unknown", rep(charging="unknown"), "charging", UNKNOWN),
        ("garbage", rep(charging="SATISFIED"), "charging", UNKNOWN),
    ],
    "charging-policy": [
        ("v3", setter(chargingPolicy="charging-policy-v3"), "charging", PASS),
        ("v2", setter(chargingPolicy="charging-policy-v2"), "charging", UNKNOWN),
        ("missing", setter(chargingPolicy=None), "charging", UNKNOWN),
    ],
    "coverage": [
        ("covered", rep(covered=True), "coverage", PASS),
        ("gap", rep(covered=False), "coverage", UNKNOWN),
        ("missing", rep(covered=None), "coverage", UNKNOWN),
    ],
    "artifact": [
        ("match", artifact(), "artifact", PASS),
        ("record states no expectation", artifact(recordedExpected=None), "artifact", PASS),
        ("mismatch", artifact(sha256="b" * 64), "artifact", FAIL),
        ("recorded expectation differs", artifact(recordedExpected="b" * 64), "artifact", FAIL),
        ("unpinned", artifact(configuredSha256=None, recordedExpected=None), "artifact", UNKNOWN),
        ("no artifact", artifact(none=True), "artifact", UNKNOWN),
    ],
    "backend": [
        ("confirmed", setter(backendEvidence="confirmed"), "backend", PASS),
        ("consistent (CPU)", setter(backendEvidence="consistent"), "backend", PASS),
        ("unknown", setter(backendEvidence="unknown"), "backend", UNKNOWN),
        ("no evidence", setter(backendEvidence=None), "backend", UNKNOWN),
        ("mismatch", setter(backendEvidence="mismatch"), "backend", FAIL),
        ("garbage", setter(backendEvidence="confirmed!"), "backend", UNKNOWN),
    ],
    "build": [
        ("clean release", build(), "build", PASS),
        ("dirty", build(gitDirty=True), "build", FAIL),
        ("debug (code that ran is not identified)", build(buildType="debug"), "build", UNKNOWN),
        ("dirty flag unknown", build(gitDirty=None), "build", UNKNOWN),
        ("no commit", build(gitCommit=None), "build", UNKNOWN),
        ("unknown flavor", build(flavor="unrecognized"), "build", UNKNOWN),
        ("unknown build type", build(buildType=None), "build", UNKNOWN),
        ("unavailable", build(status="unavailable"), "build", UNKNOWN),
        ("not recorded", build(none=True), "build", UNKNOWN),
    ],
    "app-identity": [
        ("matched", setter(appIdentity="matched"), "app-identity", PASS),
        ("changed", setter(appIdentity="changed"), "app-identity", FAIL),
        ("unverified", setter(appIdentity="unverified"), "app-identity", UNKNOWN),
        ("missing", setter(appIdentity=None), "app-identity", UNKNOWN),
    ],
}
ACCEL_DIMENSIONS = {
    "correctness": [
        ("pass", setter(correctness="pass"), "correctness", PASS),
        ("fail", setter(correctness="fail"), "correctness", FAIL),
        ("unverified", setter(correctness="unverified"), "correctness", UNKNOWN),
        ("missing", setter(correctness=None), "correctness", UNKNOWN),
    ],
    "comparison": [
        ("valid", setter(comparison="valid"), "comparison", PASS),
        ("invalid", setter(comparison="invalid"), "comparison", FAIL),
        ("unverified", setter(comparison="unverified"), "comparison", UNKNOWN),
        ("missing", setter(comparison=None), "comparison", UNKNOWN),
    ],
    "accelerator-backend": [
        ("confirmed", setter(backendEvidence="confirmed"), "backend", PASS),
        ("consistent is not confirmed", setter(backendEvidence="consistent"), "backend", UNKNOWN),
        ("unknown", setter(backendEvidence="unknown"), "backend", UNKNOWN),
        ("mismatch", setter(backendEvidence="mismatch"), "backend", FAIL),
    ],
}
VALIDATION = {PASS: "verified", UNKNOWN: "unverified", FAIL: "failed"}
GUARDS = {PASS: True, UNKNOWN: None, FAIL: False}


def worst(outcomes):
    return FAIL if FAIL in outcomes else UNKNOWN if UNKNOWN in outcomes else PASS


def outcome_of(decision, check):
    return next(item["outcome"] for item in decision["checks"] if item["check"] == check)


class PolicyTests(unittest.TestCase):
    def assert_decision(self, decision, outcome):
        self.assertEqual(decision["validation"], VALIDATION[outcome], decision["checks"])
        self.assertIs(decision["guardsPassed"], GUARDS[outcome])
        self.assertEqual(decision["policy"], "publication-policy-v1")
        self.assertEqual([item["check"] for item in decision["checks"]], list(policy.CHECKS))

    def test_a_session_passing_every_check_is_verified(self):
        for name, base in (("cpu", facts()), ("accelerator", accel())):
            with self.subTest(name):
                decision = policy.evaluate(base)
                self.assert_decision(decision, PASS)
                self.assertTrue(decision["publishable"])
                self.assertIsNone(decision["refusal"])

    def test_every_check_alone(self):
        for base_name, base, dimensions in (("cpu", facts, DIMENSIONS), ("accelerator", accel, ACCEL_DIMENSIONS)):
            for dimension, options in dimensions.items():
                for label, mutate, check, outcome in options:
                    with self.subTest(base=base_name, dimension=dimension, option=label):
                        value = base()
                        mutate(value)
                        decision = policy.evaluate(value)
                        self.assertEqual(outcome_of(decision, check), outcome)
                        self.assert_decision(decision, outcome)

    def test_every_pair_of_checks(self):
        """Exhaustive over pairs: the decision is the worst outcome, whatever the combination."""
        for base, dimensions in ((facts, DIMENSIONS), (accel, {**ACCEL_DIMENSIONS, **{
                key: value for key, value in DIMENSIONS.items() if key != "backend"}})):
            for (_, first), (_, second) in itertools.combinations(dimensions.items(), 2):
                for a, b in itertools.product(first, second):
                    value = base()
                    a[1](value)
                    b[1](value)
                    decision = policy.evaluate(value)
                    expected = worst([a[3], b[3]])
                    self.assertEqual(decision["validation"], VALIDATION[expected], (a[0], b[0], decision["checks"]))
                    self.assertIs(decision["guardsPassed"], GUARDS[expected])

    def test_unknown_is_never_a_pass(self):
        for dimension, options in {**DIMENSIONS, **ACCEL_DIMENSIONS}.items():
            for label, mutate, _, outcome in options:
                if outcome == UNKNOWN:
                    value = accel() if dimension in ACCEL_DIMENSIONS else facts()
                    mutate(value)
                    self.assertNotEqual(policy.evaluate(value)["validation"], "verified", (dimension, label))

    def test_conditions_are_required_for_every_measured_repetition(self):
        value = facts()
        value["conditions"]["repetitions"] = []
        decision = policy.evaluate(value)
        self.assertEqual(outcome_of(decision, "coverage"), UNKNOWN)
        self.assertEqual(outcome_of(decision, "charging"), UNKNOWN)
        decision = policy.evaluate(facts(conditions=None))
        for check in ("conditions", "charging", "coverage"):
            self.assertEqual(outcome_of(decision, check), UNKNOWN)
        self.assert_decision(decision, UNKNOWN)

    def test_charging_failure_is_not_hidden_by_an_older_policy(self):
        value = facts(chargingPolicy="charging-policy-v2")
        rep(charging="violated")(value)
        self.assertEqual(outcome_of(policy.evaluate(value), "charging"), FAIL)

    def test_non_accelerator_protocols_have_no_gate(self):
        decision = policy.evaluate(facts(correctness="fail", comparison="invalid"))
        self.assertEqual(outcome_of(decision, "correctness"), PASS)
        self.assertEqual(outcome_of(decision, "comparison"), PASS)

    def test_gpu_and_npu_need_confirmed_backend_evidence(self):
        for backend in ("gpu", "npu"):
            for status, outcome in (("confirmed", PASS), ("consistent", UNKNOWN), ("unknown", UNKNOWN),
                                    (None, UNKNOWN), ("mismatch", FAIL)):
                with self.subTest(backend=backend, status=status):
                    decision = policy.evaluate(accel(backend=backend, backendEvidence=status))
                    self.assertEqual(outcome_of(decision, "backend"), outcome)

    def test_refusals(self):
        cases = {
            "experiment": (facts(experiment=["experiment-* configuration id"]), "experiment session"),
            "bug-repro": (accel(purpose="bug-repro"), "bug-repro session"),
            "no measured repetition": (facts(status="failed", measured=0, attempts=0, stopReason="setup-failed"),
                                       "no measured repetition"),
        }
        for name, (value, reason) in cases.items():
            with self.subTest(name):
                decision = policy.evaluate(value)
                self.assertFalse(decision["publishable"])
                self.assertIn(reason, decision["refusal"])
        # A refusal holds even when every check passes.
        self.assertFalse(policy.evaluate(accel(purpose="bug-repro"))["publishable"])
        self.assertEqual(policy.evaluate(accel(purpose="bug-repro"))["validation"], "verified")

    def test_evaluate_is_pure(self):
        value = facts()
        rep(charging="unknown")(value)
        before = json.dumps(value, sort_keys=True)
        first = policy.evaluate(value)
        self.assertEqual(json.dumps(value, sort_keys=True), before, "evaluate must not change its input")
        self.assertEqual(policy.evaluate(json.loads(before)), first)


class FactsFromBundlesTests(unittest.TestCase):
    """Facts are recomputed from real-shaped bundles, never read from the record's own claims."""

    def report(self, records, **options):
        with tempfile.TemporaryDirectory() as root:
            directory, sha = fx.bundle(root, records, "00000000-0000-4000-8000-000000000001", **options)
            report = policy.bundle_report(directory, sha)
        self.assertTrue(report["valid"], report["errors"])
        return {item["sessionId"]: item for item in report["sessions"]}

    def test_app_fixtures_give_the_expected_decision(self):
        cpu = {
            "complete": ("verified", True, []),
            "flagged": ("failed", True, ["conditions"]),
            "interrupted": ("failed", True, ["conditions"]),
            "chargingDuringRun": ("failed", True, ["charging"]),
            "onBattery": ("failed", True, ["charging"]),
            "chargingUnknown": ("unverified", True, ["charging"]),
            "monitorIncomplete": ("unverified", True, ["conditions", "charging", "coverage"]),
            "noExpectedHash": ("unverified", True, ["artifact"]),
            "buildUnavailable": ("unverified", True, ["build"]),
            "executionUnreported": ("unverified", True, ["backend"]),
            "failed": ("failed", True, ["complete", "conditions"]),
            "chargingRecordOnly": (None, False, None),
            "configurationAborted": (None, False, None),
        }
        records = [fx.cpu_record(name, f"1767225600000-{index}-policy") for index, name in enumerate(cpu)]
        sessions = self.report(records)
        for index, (name, (validation, publishable, open_checks)) in enumerate(cpu.items()):
            with self.subTest(name):
                decision = sessions[f"1767225600000-{index}-policy"]["decision"]
                self.assertEqual(decision["publishable"], publishable, decision)
                if publishable:
                    self.assertEqual(decision["validation"], validation, decision["checks"])
                    self.assertEqual([c["check"] for c in decision["checks"] if c["outcome"] != PASS], open_checks)

    def test_accelerator_fixtures_give_the_expected_decision(self):
        accelerator = {
            "f8-llama-hexagon": ("verified", True, []),
            "f8-llama-cpu": ("verified", True, []),
            "f8-litert-cpu": ("verified", True, []),
            "f8-litert-gpu": ("unverified", True, ["backend"]),
            "f8-litert-npu": ("failed", True, ["backend", "correctness", "comparison"]),
            "f8-llama-opencl": ("failed", True, ["backend", "comparison"]),
            "bugRepro-npu": (None, False, None),
            "engineUnavailable-cpu": (None, False, None),
        }
        records = [fx.accel_record(name, f"1767225600000-{index}-accel") for index, name in enumerate(accelerator)]
        sessions = self.report(records)
        for index, (name, (validation, publishable, open_checks)) in enumerate(accelerator.items()):
            with self.subTest(name):
                decision = sessions[f"1767225600000-{index}-accel"]["decision"]
                self.assertEqual(decision["publishable"], publishable, decision)
                if publishable:
                    self.assertEqual(decision["validation"], validation, decision["checks"])
                    self.assertEqual([c["check"] for c in decision["checks"] if c["outcome"] != PASS], open_checks)

    def test_dirty_build_and_changed_app_identity_fail(self):
        dirty = fx.cpu_record("complete", "1767225600000-0-dirty")
        dirty["build"]["gitDirty"] = True
        decision = self.report([dirty])["1767225600000-0-dirty"]["decision"]
        self.assertEqual((decision["validation"], outcome_of(decision, "build")), ("failed", FAIL))
        changed = self.report([fx.cpu_record("complete", "1767225600000-0-changed")], consistency="changed")
        decision = changed["1767225600000-0-changed"]["decision"]
        self.assertEqual((decision["validation"], outcome_of(decision, "app-identity")), ("failed", FAIL))

    def test_relabelled_accelerator_identity_is_refused(self):
        """Model, quant or model file relabelled consistently (re-hashed) disagree with the configured run."""
        for field, value in (("model", "Llama 3.2 3B"), ("quant", "Q8_0"), ("modelFile", "other.gguf")):
            with self.subTest(field), tempfile.TemporaryDirectory() as root:
                record = fx.accel_record("f8-llama-hexagon", "1767225600000-0-relabel")
                directory, _ = fx.bundle(root, [record], "00000000-0000-4000-8000-000000000004")
                forged = fx.rehash(directory, "sessions/1767225600000-0-relabel.json",
                                   lambda document: document["record"].update({field: value}))
                report = policy.bundle_report(forged, forged.name)
                self.assertFalse(report["valid"])
                self.assertTrue(any("configuration.run-mismatch" in error for error in report["errors"]),
                                report["errors"])

    def test_app_identity_is_recomputed_from_the_snapshots(self):
        record = fx.cpu_record("complete", "1767225600000-0-apk")
        cases = {
            "labelled matched, snapshots differ": ("matched", {"before": "a", "after": "b"}, None),
            "labelled matched, no snapshots": ("matched", {"before": None, "after": None}, None),
            "labelled unverified, identical": ("unverified", {"before": "a", "after": "a"}, UNKNOWN),
            "labelled changed": ("changed", {"before": "a", "after": "a"}, FAIL),
            "matched": ("matched", {"before": "a", "after": "a"}, PASS),
        }
        for name, (label, snapshots, outcome) in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as root:
                directory, _ = fx.bundle(root, [copy.deepcopy(record)], "00000000-0000-4000-8000-000000000005")
                capture = next(directory.glob("captures/*.json")).relative_to(directory).as_posix()

                def relabel(document):
                    identity = document["appIdentity"]
                    identity["consistency"] = label
                    for side, apk in snapshots.items():
                        identity[side] = None if apk is None else {
                            "status": "recorded", "apks": [{"name": "base.apk", "sha256": apk * 64}]}
                    if label == "changed":
                        document["status"] = "app-changed"
                forged = fx.rehash(directory, capture, relabel)
                report = policy.bundle_report(forged, forged.name)
                if outcome is None:
                    self.assertFalse(report["valid"])
                    self.assertTrue(any("appIdentity says matched" in error for error in report["errors"]))
                else:
                    self.assertTrue(report["valid"], report["errors"])
                    self.assertEqual(outcome_of(report["sessions"][0]["decision"], "app-identity"), outcome)

    def test_a_cpu_run_that_offloads_layers_is_not_a_cpu_row(self):
        with tempfile.TemporaryDirectory() as root:
            record = fx.cpu_record("complete", "1767225600000-0-offload")
            directory, _ = fx.bundle(root, [record], "00000000-0000-4000-8000-000000000006")

            def offload(document):
                document["record"]["effectiveSettings"]["init"]["n_gpu_layers"] = 99
                for report in document["record"]["executionReports"]:
                    report.update(gpu=True, devices=["GPUOpenCL"])
            forged = fx.rehash(directory, "sessions/1767225600000-0-offload.json", offload)
            report = policy.bundle_report(forged, forged.name)
            self.assertTrue(report["valid"], report["errors"])
            decision = report["sessions"][0]["decision"]
            self.assertEqual((outcome_of(decision, "backend"), decision["validation"]), (FAIL, "failed"))

            def unknown(document):
                document["record"]["effectiveSettings"] = None
            forged = fx.rehash(forged, "sessions/1767225600000-0-offload.json", unknown)
            report = policy.bundle_report(forged, forged.name)
            self.assertTrue(report["valid"], report["errors"])
            self.assertEqual(outcome_of(report["sessions"][0]["decision"], "backend"), UNKNOWN)
        facts_ = {"engine": "llama.cpp", "backend": "cpu", "protocol": {"version": "cpu-reference-v3"},
                  "configuration": {"runs": [{"id": "r"}]}, "configurationRunId": "r", "executionReports": []}
        self.assertEqual(policy._backend({**facts_, "effectiveSettings": {"init": {"n_gpu_layers": 1}}}, []), "mismatch")
        self.assertEqual(policy._backend({**facts_, "effectiveSettings": None}, []), "unknown")
        self.assertEqual(policy._backend({**facts_, "effectiveSettings": {"init": {"n_gpu_layers": 0, "devices": ["HTP0"]}}}, []),
                         "mismatch")

    def test_bytes_changed_after_verification_are_refused(self):
        with tempfile.TemporaryDirectory() as root:
            directory, sha = fx.bundle(root, [fx.cpu_record("complete", "1767225600000-0-swap")],
                                       "00000000-0000-4000-8000-000000000007")
            real_verify = policy.export_evidence.verify

            def verify_then_swap(bundle, expected=None, sessions=()):
                result = real_verify(bundle, expected, sessions)
                path = Path(bundle) / "sessions/1767225600000-0-swap.json"
                path.write_bytes(path.read_bytes().replace(b"Llama 3.2 1B", b"Llama 3.2 3B"))
                return result
            policy.export_evidence.verify = verify_then_swap
            try:
                report = policy.bundle_report(directory, sha)
            finally:
                policy.export_evidence.verify = real_verify
            self.assertFalse(report["valid"])
            self.assertTrue(any("changed after verification" in error for error in report["errors"]), report["errors"])

    def test_recorded_claims_are_not_trusted(self):
        """A re-hashed bundle whose verdict or charging claims disagree with its evidence is refused."""
        record = fx.cpu_record("chargingDuringRun", "1767225600000-0-forged")
        with tempfile.TemporaryDirectory() as root:
            directory, _ = fx.bundle(root, [record], "00000000-0000-4000-8000-000000000002")
            path = "sessions/1767225600000-0-forged.json"

            def claim_satisfied(document):
                document["record"]["conditionVerdict"]["charging"] = "satisfied"
                for attempt in document["record"]["coldRuns"]:
                    attempt["conditions"]["charging"] = "satisfied"
            forged = fx.rehash(directory, path, claim_satisfied)
            report = policy.bundle_report(forged, forged.name)
            self.assertFalse(report["valid"])
            self.assertTrue(any("recomputed evidence disagrees" in error and "conditions.recomputed" in error
                                for error in report["errors"]), report["errors"])
            self.assertEqual(report["sessions"], [])

    def test_invalid_bundle_gives_no_decisions(self):
        with tempfile.TemporaryDirectory() as root:
            directory, sha = fx.bundle(root, [fx.cpu_record("complete", "1767225600000-0-bad")],
                                       "00000000-0000-4000-8000-000000000003")
            path = directory / "sessions/1767225600000-0-bad.json"
            path.write_bytes(path.read_bytes().replace(b'"Llama 3.2 1B"', b'"Llama 3.2 3B"'))
            report = policy.bundle_report(directory, sha)
            self.assertFalse(report["valid"])
            self.assertEqual(report["sessions"], [])
            self.assertTrue(any("checksum" in error for error in report["errors"]), report["errors"])
            self.assertFalse(policy.bundle_report(directory, "f" * 64)["valid"])


if __name__ == "__main__":
    unittest.main()
