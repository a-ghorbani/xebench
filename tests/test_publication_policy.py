"""publication-policy-v1 (scripts/publication_policy.py).

The policy is a pure function of recomputed facts. These tests cover every check alone,
every pair of checks together (the decision is always the worst outcome; unknown is
never a pass), the refusals, and the facts recomputed from real-shaped bundles built
from the app-emitted fixtures.
"""
import copy
import hashlib
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
REFERENCE = tempfile.TemporaryDirectory()


def setUpModule():
    # Reviewed configs, model manifests and the device table, plus the fixture devices.
    policy.ROOT = fx.reference_root(Path(REFERENCE.name) / "repo")


def tearDownModule():
    REFERENCE.cleanup()


def facts(**changes):
    """Facts of a session that passes every check (cpu-reference-v3, llama.cpp CPU)."""
    base = {
        "sessionId": "s-1", "protocolVersion": "cpu-reference-v3", "engine": "llama.cpp", "backend": "cpu",
        "purpose": None, "status": "complete", "stopReason": None, "repetitionsPlanned": 3, "attempts": 3,
        "measured": 3, "experiment": [], "chargingPolicy": "charging-policy-v3",
        "conditions": {"session": "pass", "repetitions": [
            {"runIndex": i, "verdict": "pass", "charging": "satisfied", "covered": True} for i in range(3)]},
        "artifact": {"sha256": HASH, "recordedExpected": HASH, "configuredSha256": HASH,
                     "reviewedConfigs": ["cpu-reference-v1.json"], "manifestSha256": [HASH]},
        "backendEvidence": "confirmed", "correctness": None, "comparison": None,
        "build": {"status": "reported", "gitCommit": "c" * 40, "gitDirty": False, "flavor": "reference",
                  "buildType": "release"},
        "appIdentity": "matched",
        "device": {"model": "SM-S911B", "soc": "SM8550", "tableSoc": "SM8550"},
        "decode": {"decodeMode": None, "protocolDecodeMode": None, "maxDecodeTokens": 128, "counts": [127, 127, 127],
                   "budgetSlack": 1},
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


def decode(**changes):
    def mutate(f):
        f["decode"] = None if changes.get("none") else {**f["decode"], **changes}
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
        ("not a reviewed run", artifact(reviewedConfigs=[]), "artifact", UNKNOWN),
        ("no manifest entry", artifact(manifestSha256=[]), "artifact", UNKNOWN),
        ("manifest hash differs", artifact(manifestSha256=["b" * 64]), "artifact", FAIL),
        ("manifest differs, run not reviewed", artifact(manifestSha256=["b" * 64], reviewedConfigs=[]), "artifact",
         FAIL),
    ],
    "device": [
        ("in the table", setter(), "device", PASS),
        ("case differs only", setter(device={"model": "ONEPLUS A6003", "soc": "sdm845", "tableSoc": "SDM845"}),
         "device", PASS),
        ("unknown device", setter(device={"model": "Other", "soc": "X1", "tableSoc": None}), "device", UNKNOWN),
        ("no SoC", setter(device={"model": "SM-S911B", "soc": None, "tableSoc": "SM8550"}), "device", UNKNOWN),
        ("contradicts the table", setter(device={"model": "SM-S911B", "soc": "SM8850", "tableSoc": "SM8550"}),
         "device", FAIL),
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
    "decode": [
        ("reached the budget (llama.cpp counts one less)", decode(), "decode", PASS),
        ("one length short of the budget, the same in every repetition", decode(counts=[85, 85, 85]), "decode", PASS),
        ("one repetition stopped short", decode(counts=[127, 16, 127]), "decode", UNKNOWN),
        ("different short lengths", decode(counts=[79, 83, 85]), "decode", UNKNOWN),
        ("fixed, every repetition at the budget",
         decode(decodeMode="fixed", protocolDecodeMode="fixed", counts=[128, 128, 128], budgetSlack=0), "decode", PASS),
        ("fixed, one repetition short",
         decode(decodeMode="fixed", protocolDecodeMode="fixed", counts=[128, 127, 128], budgetSlack=0), "decode", FAIL),
        ("fixed in the protocol only", decode(protocolDecodeMode="fixed", counts=[128, 128, 128]), "decode", FAIL),
        ("fixed in the configuration only",
         decode(decodeMode="fixed", counts=[128, 128, 128], budgetSlack=0), "decode", FAIL),
        ("more than the budget", decode(counts=[129, 129, 129]), "decode", FAIL),
        ("unknown mode", decode(decodeMode="sampled", protocolDecodeMode="sampled"), "decode", UNKNOWN),
        ("a count missing", decode(counts=[127, None, 127]), "decode", UNKNOWN),
        ("a count is not a number", decode(counts=[127, True, 127]), "decode", UNKNOWN),
        ("no counts", decode(counts=[]), "decode", UNKNOWN),
        ("no decode facts", decode(none=True), "decode", UNKNOWN),
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
            "spike": (accel(experiment=["spike-* configuration id"]), "spike-* configuration id"),
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

    def test_the_decode_check_states_the_count(self):
        """A session that stops at the end of turn states its length; it never reads as the budget."""
        cases = {
            "llama.cpp at the budget": (decode(), "every repetition reached the 128-token budget"),
            "llama.cpp stopped after 16 tokens":
                (decode(counts=[16]), "every repetition decoded 16 of 128 tokens, stopped at the end of turn"),
            "one of three stopped short": (decode(counts=[127, 16, 127]),
                                           "repetitions decoded different lengths (127, 16, 127 of 128 tokens)"),
            "GENIE-X one token short": (decode(counts=[127, 127, 127], budgetSlack=0),
                                        "every repetition decoded 127 of 128 tokens, stopped at the end of turn"),
            "fixed": (decode(decodeMode="fixed", protocolDecodeMode="fixed", counts=[128, 128, 128], budgetSlack=0),
                      "fixed: every repetition decoded 128 tokens"),
            "fixed but short": (decode(decodeMode="fixed", protocolDecodeMode="fixed", counts=[128, 16, 128]),
                                "fixed, but repetitions decoded 128, 16, 128 tokens, not 128"),
        }
        for name, (mutate, detail) in cases.items():
            with self.subTest(name):
                value = facts()
                mutate(value)
                check = next(item for item in policy.evaluate(value)["checks"] if item["check"] == "decode")
                self.assertEqual(check["detail"], detail)

    def test_spike_and_experiment_markers_come_from_the_configuration_id(self):
        record = {"configuration": {"id": "spike-refresh-llamacpp-gemma3-v1"}}
        self.assertEqual(policy._experiment_markers(record), ["spike-* configuration id"])
        record = {"configuration": {"id": "experiment-charging-ab-v1"}}
        self.assertEqual(policy._experiment_markers(record), ["experiment-* configuration id"])
        # Only the prefix counts.
        for other in ("llamacpp-spike-v1", "f8-gemma3-accelerators-v1", "cpu-reference-v3"):
            self.assertEqual(policy._experiment_markers({"configuration": {"id": other}}), [])

    def test_evaluate_is_pure(self):
        value = facts()
        rep(charging="unknown")(value)
        before = json.dumps(value, sort_keys=True)
        first = policy.evaluate(value)
        self.assertEqual(json.dumps(value, sort_keys=True), before, "evaluate must not change its input")
        self.assertEqual(policy.evaluate(json.loads(before)), first)


class ExecutorchTokenizerTests(unittest.TestCase):
    """ExecuTorch runs pin their tokenizer like the model: the artifact check covers both."""

    TOKENIZER = "d" * 64

    def executorch(self, **tokenizer):
        base = {"sha256": self.TOKENIZER, "recordedExpected": self.TOKENIZER, "configuredSha256": self.TOKENIZER,
                "manifestSha256": [self.TOKENIZER]}
        return accel(engine="executorch", backend="cpu", backendEvidence="confirmed",
                     tokenizer=None if tokenizer.get("none") else {**base, **tokenizer})

    def test_tokenizer_outcomes(self):
        cases = (
            ("pinned and in the manifest", {}, PASS),
            ("no tokenizer hash", {"none": True}, UNKNOWN),
            # The reviewer's probe: no configured hash and an arbitrary recorded one.
            ("config pins no tokenizerSha256", {"configuredSha256": None, "recordedExpected": None,
                                                 "sha256": "e" * 64}, FAIL),
            ("differs from the configured hash", {"sha256": "e" * 64}, FAIL),
            ("recorded expectation differs", {"recordedExpected": "e" * 64}, FAIL),
            ("differs from the manifest", {"manifestSha256": ["e" * 64]}, FAIL),
            ("no manifest entry", {"manifestSha256": []}, UNKNOWN),
        )
        for label, change, outcome in cases:
            with self.subTest(label):
                decision = policy.evaluate(self.executorch(**change))
                self.assertEqual(outcome_of(decision, "artifact"), outcome)
                self.assertEqual(decision["validation"] == "verified", outcome == PASS)

    def test_other_engines_need_no_tokenizer(self):
        self.assertEqual(outcome_of(policy.evaluate(accel()), "artifact"), PASS)


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
            # No configuration hash without a build identity, so no reviewed configuration either.
            "buildUnavailable": ("unverified", True, ["artifact", "build"]),
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
            # The .litertlm files are pinned in experimental-models.json (pinnedArtifacts).
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

    def test_relabelled_device_backend_or_platform_is_refused(self):
        """One re-hashed field must not move a verified CPU reference run to another row."""
        def gpu(document):
            document["record"]["backend"] = "gpu"
            for report in document["record"]["executionReports"]:
                report.update(gpu=True, devices=["GPUOpenCL"])
        cases = {
            "device model": (lambda document: document["device"].update(model="25102PCBEG"), "device/model"),
            "cpu reference as gpu": (gpu, "schema.value /backend"),
            "platform": (lambda document: document["record"].update(platform="ios"), "schema.value /platform"),
        }
        for name, (change, expected) in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as root:
                record = fx.cpu_record("complete", "1767225600000-0-move")
                directory, sha = fx.bundle(root, [record], "00000000-0000-4000-8000-000000000008")
                self.assertEqual(policy.bundle_report(directory, sha)["sessions"][0]["decision"]["validation"],
                                 "verified")
                forged = fx.rehash(directory, "sessions/1767225600000-0-move.json", change)
                report = policy.bundle_report(forged, forged.name)
                self.assertFalse(report["valid"])
                self.assertTrue(any(expected in error for error in report["errors"]), report["errors"])
                self.assertEqual(report["sessions"], [])

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

    def forge(self, record, *edits):
        """Export `record`, then apply (path, change) edits, each re-hashed consistently; returns the report."""
        with tempfile.TemporaryDirectory() as root:
            directory, _ = fx.bundle(root, [record], "00000000-0000-4000-8000-000000000008")
            for path, change in edits:
                if path == "capture":
                    path = next(directory.glob("captures/*.json")).relative_to(directory).as_posix()
                directory = fx.rehash(directory, path, change)
            return policy.bundle_report(directory, directory.name)

    @staticmethod
    def relabel_run(**fields):
        """Change the record and its configured run together, as a consistent forger would."""
        def change(document):
            record = document["record"]
            run = next(item for item in record["configuration"]["runs"] if item["id"] == record["configurationRunId"])
            for key, value in fields.items():
                if key == "sha256":
                    record["artifact"]["sha256"] = record["artifact"]["expectedSha256"] = run["modelSha256"] = value
                else:
                    record[key] = run[key] = value
        return change

    def test_consistent_model_label_forgeries_are_not_verified(self):
        """The model label is anchored to a reviewed configuration and the model manifest."""
        path = "sessions/1767225600000-0-label.json"
        q4_0 = "fa0390e7c043f89ae1847bd6682d748041a99d4ef3de0e0b27d33b6af97a8be8"  # experimental-models.json
        cases = {
            "model renamed": ({"model": "Llama 3.2 3B"}, "unverified", UNKNOWN),
            "quant renamed": ({"quant": "Q8_0"}, "unverified", UNKNOWN),
            "hash swapped to an unlisted value": ({"sha256": "b" * 64}, "failed", FAIL),
            "file and hash swapped to another manifest model": (
                {"modelFile": "Llama-3.2-1B-Instruct-Q4_0.gguf", "sha256": q4_0}, "unverified", UNKNOWN),
            "hash swapped to another manifest model's hash": ({"sha256": q4_0}, "failed", FAIL),
            # Exactly another reviewed run (f8-llama32-hexagon-check-v1.json llama32-q40-cpu) with its hash:
            # the configuration no longer is the reviewed file its configurationSource hash names.
            "relabelled as another reviewed run": (
                {"model": "Llama 3.2 1B", "quant": "Q4_0 (GGUF)", "modelFile": "Llama-3.2-1B-Instruct-Q4_0.gguf",
                 "sha256": q4_0}, "unverified", UNKNOWN),
        }
        for name, (fields, validation, outcome) in cases.items():
            with self.subTest(name):
                report = self.forge(fx.cpu_record("complete", "1767225600000-0-label"), (path, self.relabel_run(**fields)))
                self.assertTrue(report["valid"], report["errors"])
                decision = report["sessions"][0]["decision"]
                self.assertEqual((decision["validation"], outcome_of(decision, "artifact")), (validation, outcome),
                                 decision["checks"])
        unforged = self.forge(fx.cpu_record("complete", "1767225600000-0-label"))["sessions"][0]["decision"]
        self.assertEqual(unforged["validation"], "verified")
        self.assertIn("reviewed run (fixture-", next(c["detail"] for c in unforged["checks"] if c["check"] == "artifact"))

    def test_the_reviewed_configuration_is_the_one_the_session_names(self):
        """A run identical to a reviewed run counts only inside the reviewed file the session's
        configurationSource hash names, with that file's content."""
        reference = policy.load_reference()
        record = fx.cpu_record("complete", "1767225600000-0-source")
        run = next(item for item in record["configuration"]["runs"] if item["id"] == record["configurationRunId"])
        self.assertTrue(policy._reviewed(record, run, reference))
        other = copy.deepcopy(record)
        other["configurationSource"]["sha256"] = "c" * 64
        self.assertEqual(policy._reviewed(other, run, reference), [])
        edited = copy.deepcopy(record)
        edited["configuration"]["cooldownSec"] += 1
        self.assertEqual(policy._reviewed(edited, run, reference), [])
        unhashed = copy.deepcopy(record)
        unhashed["configurationSource"] = {"kind": "bundled", "sha256": None}
        self.assertEqual(policy._reviewed(unhashed, run, reference), [])

    def test_committed_cpu_reference_configurations_are_reviewed_anchors(self):
        """A session that ran the committed cpu-reference-v2 (bundled) or a staged copy of
        cpu-reference-v1, named by the SHA-256 of that file's bytes, is a reviewed run. Its effective
        load settings must be the ones the file selects."""
        cases = (("referenceV2", "cpu-reference-v2.json", {"use_mmap": True, "flash_attn_type": "auto"}),
                 ("referenceV1Staged", "cpu-reference-v1.json", {"use_mmap": False, "flash_attn_type": "off"}),
                 # Each of cpu-reference-v3's three runs, from one staged invocation.
                 ("referenceV3Staged-Q4_K_M", "cpu-reference-v3.json", {"flash_attn_type": "off"}),
                 ("referenceV3Staged-Q4_0", "cpu-reference-v3.json", {"use_mmap": True}),
                 ("referenceV3Staged-Q8_0", "cpu-reference-v3.json", {"flash_attn_type": "off"}),
                 # The single-run board configurations for Qwen2.5 1.5B and Gemma 3 1B.
                 ("referenceV3Qwen25Staged", "cpu-reference-v3-qwen25.json", {"use_mmap": True}),
                 ("referenceV3Gemma3Staged", "cpu-reference-v3-gemma3.json", {"flash_attn_type": "off"}))
        for index, (name, file, other_settings) in enumerate(cases):
            data = (ROOT / "xebenchapp/configs" / file).read_bytes()
            record = fx.cpu_record(name, f"1767225600000-{index}-anchor")
            self.assertEqual(record["configuration"], json.loads(data))
            record["configurationSource"]["sha256"] = hashlib.sha256(data).hexdigest()
            with self.subTest(name), tempfile.TemporaryDirectory() as root:
                directory, sha = fx.bundle(root, [record], f"00000000-0000-4000-8000-00000000010{index}")
                # The export keeps the configured load settings and the effective ones.
                exported = json.loads((directory / f"sessions/1767225600000-{index}-anchor.json").read_text())
                self.assertEqual(exported["record"]["configuration"], json.loads(data))
                self.assertEqual(exported["record"]["effectiveSettings"]["init"], record["effectiveSettings"]["init"])
                report = policy.bundle_report(directory, sha)
                self.assertTrue(report["valid"], report["errors"])
                decision = report["sessions"][0]["decision"]
                self.assertEqual(decision["validation"], "verified", decision["checks"])
                self.assertEqual(outcome_of(decision, "artifact"), PASS)
                # In the repository's own reference data, the hash names exactly that committed file.
                self.assertEqual(policy.load_reference(ROOT)["files"][hashlib.sha256(data).hexdigest()]["name"], file)
            with self.subTest(name, settings="the other file's"), tempfile.TemporaryDirectory() as root:
                directory, _ = fx.bundle(root, [record], f"00000000-0000-4000-8000-00000000011{index}")
                path = f"sessions/1767225600000-{index}-anchor.json"
                forged = fx.rehash(directory, path,
                                   lambda document: document["record"]["effectiveSettings"]["init"].update(other_settings))
                report = policy.bundle_report(forged, forged.name)
                self.assertFalse(report["valid"])
                self.assertTrue(any("settings.configuration-mismatch" in error for error in report["errors"]),
                                report["errors"])

    def test_committed_llamacpp_accelerator_configurations_are_reviewed_anchors(self):
        """Staged sessions of the committed llama.cpp accelerator configurations (OpenCL and Hexagon
        candidates and their CPU gate references, with v3 load settings) export and come out verified;
        a session whose engine was given other load settings is refused."""
        cases = (("qwen25-qwen25-1.5b-q80-opencl", "llamacpp-qwen25-opencl-v1.json"),
                 ("qwen25-qwen25-1.5b-q80-cpu", "llamacpp-qwen25-opencl-v1.json"),
                 ("board-hexagon-llama32-q4km-hexagon", "llamacpp-llama32-q4km-hexagon-v1.json"))
        for index, (name, file) in enumerate(cases):
            data = (ROOT / "xebenchapp/configs" / file).read_bytes()
            record = copy.deepcopy(fx.ACCEL[name])
            record["runId"] = f"1767225600000-{index}-accanchor"
            self.assertEqual(record["configuration"], json.loads(data))
            record["configurationSource"]["sha256"] = hashlib.sha256(data).hexdigest()
            with self.subTest(name), tempfile.TemporaryDirectory() as root:
                directory, sha = fx.bundle(root, [record], f"00000000-0000-4000-8000-00000000020{index}")
                report = policy.bundle_report(directory, sha)
                self.assertTrue(report["valid"], report["errors"])
                decision = report["sessions"][0]["decision"]
                self.assertEqual(decision["validation"], "verified", decision["checks"])
                self.assertEqual(outcome_of(decision, "artifact"), PASS)
            with self.subTest(name, settings="harness defaults"), tempfile.TemporaryDirectory() as root:
                directory, _ = fx.bundle(root, [record], f"00000000-0000-4000-8000-00000000021{index}")
                path = f"sessions/1767225600000-{index}-accanchor.json"
                forged = fx.rehash(directory, path, lambda document: document["record"]["effectiveSettings"]["init"]
                                   .update({"use_mmap": True}))
                report = policy.bundle_report(forged, forged.name)
                self.assertFalse(report["valid"])
                self.assertTrue(any("settings.configuration-mismatch" in error for error in report["errors"]),
                                report["errors"])

    def test_engine_timings_must_equal_the_engine_counters(self):
        path = "sessions/1767225600000-0-timing.json"
        for counter in ("predictedPerSecond", "promptPerSecond"):
            with self.subTest(counter):
                def change(document, counter=counter):
                    document["record"]["coldRuns"][0]["engineInternal"][counter] *= 1.25
                report = self.forge(fx.cpu_record("complete", "1767225600000-0-timing"), (path, change))
                self.assertFalse(report["valid"])
                self.assertTrue(any("timing.engine-mismatch" in error for error in report["errors"]), report["errors"])

    def test_device_and_soc_are_anchored_to_the_device_table(self):
        path = "sessions/1767225600000-0-soc.json"

        def soc(value):
            def change(document):
                document["device"]["soc"] = value
            return change

        def model(value):
            def change(document):
                document["device"]["model"] = value
            return change
        contradicted = self.forge(fx.cpu_record("complete", "1767225600000-0-soc"),
                                  ("capture", soc("SM8850")), (path, soc("SM8850")))
        self.assertFalse(contradicted["valid"])
        self.assertTrue(any("contradicts configs/devices.json" in error for error in contradicted["errors"]),
                        contradicted["errors"])
        unlisted = self.forge(fx.cpu_record("complete", "1767225600000-0-soc"),
                              ("capture", model("Unlisted Phone")), (path, model("Unlisted Phone")))
        self.assertTrue(unlisted["valid"], unlisted["errors"])
        decision = unlisted["sessions"][0]["decision"]
        self.assertEqual((decision["validation"], outcome_of(decision, "device")), ("unverified", UNKNOWN))

    def test_the_reference_data_is_required(self):
        with tempfile.TemporaryDirectory() as root:
            directory, sha = fx.bundle(root, [fx.cpu_record("complete", "1767225600000-0-ref")],
                                       "00000000-0000-4000-8000-000000000009")
            report = policy.bundle_report(directory, sha, root=Path(root) / "no-such-repo")
        self.assertFalse(report["valid"])
        self.assertTrue(any("reference data" in error for error in report["errors"]), report["errors"])

    def test_litert_artifacts_are_pinned_with_a_source_revision(self):
        manifest = json.loads((ROOT / "configs/models/experimental-models.json").read_text())
        pinned = {entry["file"]: entry for entry in manifest["pinnedArtifacts"] if entry["file"].endswith(".litertlm")}
        self.assertEqual(sorted(pinned), ["Gemma3-1B-IT_q4_ekv1280_sm8850.litertlm", "Gemma3-1B-IT_sm8550.litertlm",
                                          "Qwen2.5-1.5B-Instruct_multi-prefill-seq_q8_ekv4096.litertlm",
                                          "Qwen3-1.7B_dynamic_wi4b32_afp32.litertlm", "gemma-4-E2B-it.litertlm",
                                          "gemma3-1b-it-int4.litertlm"])
        models_md = (ROOT / "MODELS.md").read_text()
        configured = {run["modelFile"]: run["modelSha256"] for path in (ROOT / "xebenchapp/configs").glob("*.json")
                      for run in json.loads(path.read_text())["runs"] if run["modelFile"].endswith(".litertlm")}
        for name, entry in pinned.items():
            self.assertRegex(entry["repo"], r"^litert-community/")
            self.assertRegex(entry["revision"], r"^[0-9a-f]{40}$")
            self.assertIsInstance(entry["size"], int)
            self.assertIn(entry["revision"], models_md)
            self.assertIn(entry["sha256"], models_md)
            if name in configured:
                self.assertEqual(configured[name], entry["sha256"], "the configs pin the same bytes")
        # Staging reads only models[]: the downloadable pins still load.
        spec_ = importlib.util.spec_from_file_location("stage_model", ROOT / "scripts/stage_model.py")
        stage_model = importlib.util.module_from_spec(spec_)
        spec_.loader.exec_module(stage_model)
        self.assertEqual(len(stage_model.load_manifest(ROOT / "configs/models/experimental-models.json")), 2)

    def test_every_pinned_artifact_is_documented_and_agrees_with_the_configurations(self):
        """policy.load_reference reads pinnedArtifacts from every *-models.json, so a pin added in any
        manifest must be in MODELS.md, and every committed configuration that names the file pins
        this hash. A pin may precede the configurations that use it."""
        configured = {}
        for path in (ROOT / "xebenchapp/configs").glob("*.json"):
            for run in json.loads(path.read_text())["runs"]:
                configured.setdefault(run["modelFile"], set()).add(run.get("modelSha256"))
        models_md = (ROOT / "MODELS.md").read_text()
        manifests = sorted((ROOT / "configs/models").glob("*-models.json"))
        self.assertEqual([path.name for path in manifests], ["experimental-models.json", "reference-models.json"])
        for path in manifests:
            for entry in json.loads(path.read_text()).get("pinnedArtifacts", []):
                with self.subTest(f"{path.name} {entry['file']}"):
                    self.assertIn(entry["sha256"], models_md)
                    self.assertTrue(configured.get(entry["file"], {entry["sha256"]}) <= {entry["sha256"]},
                                    configured.get(entry["file"]))

    def test_committed_device_table_names_the_lab_phones(self):
        table = {entry["model"]: entry["soc"] for entry in json.loads((ROOT / "configs/devices.json").read_text())["devices"]}
        self.assertEqual(table, {"SM-S911B": "SM8550", "25102PCBEG": "SM8850", "Pixel 9": "Tensor G4",
                                 "ONEPLUS A6003": "SDM845", "2511FPC34G": "MT6899"})

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
