#!/usr/bin/env python3
"""Publication policy v1: decide a board row's validation from a verified evidence bundle.

  publication_policy.py BUNDLE_DIR [--sha256 HEX]

The bundle is checked with export_evidence.verify first (hashes, allowlist, privacy
screen, summaries recomputed from repetitions). Each sanitized session record is then
re-checked with raw_session's cross-field rules (readiness, condition assessments,
monitor summaries, charging, verdict, run order and v3 identity), so a re-hashed bundle
whose evidence disagrees with its own claims is refused. Every fact the policy uses is
recomputed from that evidence; recorded verdicts, guardsPassed and summaries are never
trusted.

Prints one JSON object. Exit 0 when the bundle is valid (each session then carries its
facts and decision); exit 1, with errors and no decisions, when it is not.

docs/RUN-CONDITIONS.md "Publication policy v1" is the human-readable rule set;
evaluate() is its single implementation.
"""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import export_evidence  # noqa: E402
import raw_session  # noqa: E402

POLICY_VERSION = "publication-policy-v1"
PASS, UNKNOWN, FAIL = "pass", "unknown", "fail"
KNOWN_FLAVORS = ("reference", "experimental", "geniex")
CHARGING_POLICY = "charging-policy-v3"
# Protocols whose repetitions carry run-condition evidence (run-conditions-v2).
CONDITION_PROTOCOLS = ("cpu-reference-v2", "cpu-reference-v3") + raw_session.ACCELERATOR_PROTOCOLS
CHECKS = ("complete", "conditions", "charging", "coverage", "artifact", "backend", "correctness", "comparison",
          "build", "app-identity")


# ── Facts: recomputed from the sanitized evidence ────────────────────────────

def recheck(record):
    """raw_session's cross-field rules on a sanitized record; a list of "code path" strings.

    accelerator records withhold prompt and output text, so their gate (probe set,
    correctness, comparison and backend evidence) is re-checked by export_evidence.verify
    instead of raw_session._semantic_accelerator; every other rule runs here.
    """
    try:
        version = record["protocol"]["version"]
        if version in raw_session.ACCELERATOR_PROTOCOLS:
            # The shared rules (configured run, repetition numbering, summary) and every
            # condition and identity rule; only the text-dependent gate rules are left out.
            errors = raw_session.identity_errors(record) + raw_session.summary_errors(record)
            errors += raw_session._semantic_v2(record)
            if version == raw_session.ACCELERATOR_PROTOCOL:
                errors += raw_session._semantic_v3(record)
        else:
            errors = raw_session._semantic(record)
    except (TypeError, ValueError, KeyError, IndexError, AttributeError, OverflowError):
        return ["record.unprocessable /"]
    return [f"{item['code']} {item['path'] or '/'}" for item in errors]


def _configured_run(record):
    runs = [run for run in record["configuration"]["runs"] if run.get("id") == record["configurationRunId"]]
    return runs[0] if len(runs) == 1 else None


def _experiment_markers(record):
    config = record["configuration"]
    markers = []
    if (record.get("chargingPolicy") or {}).get("readiness") == "record-only-experiment":
        markers.append("record-only-experiment charging readiness")
    if "experimentChargingReadiness" in config:
        markers.append("experimentChargingReadiness configuration")
    if str(config.get("id", "")).startswith("experiment-"):
        markers.append("experiment-* configuration id")
    return markers


def _cpu_offload(record):
    """llama.cpp CPU: None when no layer is offloaded, else "mismatch" (offloaded) or "unknown".

    The engine's gpu flag says only that GPU devices are attached, so a CPU run counts as
    CPU only when its effective settings offload no layer and select no device.
    """
    settings, run = record.get("effectiveSettings"), _configured_run(record)
    if not isinstance(settings, dict) or not isinstance(settings.get("init"), dict):
        return "unknown"
    init = settings["init"]
    layers = init.get("n_gpu_layers")
    if not isinstance(layers, int) or isinstance(layers, bool):
        return "unknown"
    if layers != 0 or init.get("devices") or (run or {}).get("nGpuLayers", 0) != 0 or (run or {}).get("devices"):
        return "mismatch"
    return None


def _apk_set(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get("status") != "recorded":
        return None
    apks = snapshot.get("apks")
    if not isinstance(apks, list) or not apks:
        return None
    return sorted((item.get("name"), item.get("sha256")) for item in apks)


def app_identity(capture):
    """(consistency recomputed from the capture's APK snapshots, error when its label disagrees).

    matched needs two recorded, non-empty, identical snapshots; changed is a label of
    changed or two recorded snapshots that differ; anything else is unverified.
    """
    identity = capture.get("appIdentity") or {}
    label = identity.get("consistency")
    before, after = _apk_set(identity.get("before")), _apk_set(identity.get("after"))
    snapshots = "unverified" if before is None or after is None else "matched" if before == after else "changed"
    if label == "matched" and snapshots != "matched":
        return "unverified", "appIdentity says matched, but its APK snapshots are not two identical recorded sets"
    if label == "changed" or snapshots == "changed":
        return "changed", None
    return ("matched" if label == "matched" else "unverified"), None


def _backend(record, measured):
    """Backend evidence status over the measured repetitions, recomputed with backend.ts rules.

    accelerator records carry per-repetition observedBackend evidence; cpu-reference-v3
    llama.cpp records carry one execution report per load, assessed with the same rule.
    Anything without evidence for every measured repetition is None (unknown).
    """
    engine, backend = record["engine"], record["backend"]
    if engine == "llama.cpp" and backend == "cpu":
        offload = _cpu_offload(record)
        if offload is not None:
            return offload
    evidence = []
    for attempt in measured:
        if record["protocol"]["version"] in raw_session.ACCELERATOR_PROTOCOLS:
            observed = attempt.get("observedBackend")
        elif engine == "llama.cpp" and record["protocol"]["version"] == "cpu-reference-v3":
            reports = [item for item in record.get("executionReports", []) if item["runIndex"] == attempt["runIndex"]]
            observed = None if len(reports) != 1 else {
                "source": "engine-report", "report": {"gpu": reports[0]["gpu"], "devices": reports[0]["devices"]}}
        else:
            observed = None
        if observed is None:
            return None
        evidence.append({"status": raw_session.assess_backend(engine, backend, observed),
                         "source": observed.get("source")})
    summary = raw_session.summarize_backend(evidence)
    return None if summary is None else summary["status"]


def _gate(record, backend_status):
    """accelerator-v1/v2: correctness and comparison status, recomputed from the probe verdicts."""
    correctness = record["correctness"]
    reference = correctness["reference"]
    status, _ = raw_session.correctness_status(correctness["role"], correctness["error"], correctness["probes"],
                                               reference["status"] if reference else None)
    mismatch = "mismatch" in (backend_status, (record.get("observedBackend") or {}).get("status"),
                              (record.get("gateBackend") or {}).get("status"))
    comparison = raw_session.comparison_status(record["purpose"], {"status": status}, mismatch)
    return status, comparison["status"]


def session_facts(doc, capture):
    """The facts publication-policy-v1 needs, for one session document of a verified bundle."""
    record = doc["record"]
    version = record["protocol"]["version"]
    attempts = record["coldRuns"]
    measured = [attempt for attempt in attempts if attempt.get("status") == "complete"]
    facts = {
        "sessionId": doc["sessionId"], "protocolVersion": version, "engine": record["engine"],
        "backend": record["backend"], "purpose": record.get("purpose"), "status": record["status"],
        "stopReason": record.get("stopReason"), "repetitionsPlanned": record["protocol"]["nColdRuns"],
        "attempts": len(attempts), "measured": len(measured), "experiment": _experiment_markers(record),
        "chargingPolicy": (record.get("chargingPolicy") or {}).get("version"),
        "conditions": None, "artifact": None, "backendEvidence": None, "correctness": None, "comparison": None,
        "build": None, "appIdentity": app_identity(capture)[0],
    }
    if version in CONDITION_PROTOCOLS:
        repetitions = []
        for attempt in measured:
            # One repetition on its own: its readiness, before/during/after assessments and charging.
            single = raw_session.condition_verdict({**record, "coldRuns": [attempt], "repetitionsPlanned": 1})
            during = attempt["conditions"]["during"]
            repetitions.append({"runIndex": attempt["runIndex"], "verdict": single["status"],
                                "charging": single["charging"],
                                "covered": during is not None and raw_session._monitor_covered(during)})
        facts["conditions"] = {"session": raw_session.condition_verdict(record)["status"],
                               "repetitions": repetitions}
    artifact, run = record.get("artifact"), _configured_run(record)
    if artifact is not None:
        facts["artifact"] = {"sha256": artifact["sha256"], "recordedExpected": artifact.get("expectedSha256"),
                             "configuredSha256": None if run is None else run.get("modelSha256")}
    facts["backendEvidence"] = _backend(record, measured)
    if version in raw_session.ACCELERATOR_PROTOCOLS:
        facts["correctness"], facts["comparison"] = _gate(record, facts["backendEvidence"])
    build = record.get("build")
    if isinstance(build, dict):
        facts["build"] = {key: build.get(key) for key in ("status", "gitCommit", "gitDirty", "flavor", "buildType")}
    return facts


# ── Policy: a pure function of the facts ─────────────────────────────────────

def _check(name, outcome, detail):
    return {"check": name, "outcome": outcome, "detail": detail}


def _complete(facts):
    planned, measured = facts["repetitionsPlanned"], facts["measured"]
    if facts["status"] == "complete" and measured == planned == facts["attempts"]:
        return _check("complete", PASS, f"{measured} of {planned} repetitions")
    if facts["status"] == "failed":
        return _check("complete", FAIL, f"stopped after {measured} of {planned} repetitions ({facts['stopReason']})")
    return _check("complete", UNKNOWN, f"status {facts['status']!r} with {measured} of {planned} repetitions")


def _worst(outcomes):
    return FAIL if FAIL in outcomes else UNKNOWN if UNKNOWN in outcomes or not outcomes else PASS


def _conditions(facts):
    conditions = facts["conditions"]
    if conditions is None:
        return [_check(name, UNKNOWN, "no run-condition evidence") for name in ("conditions", "charging", "coverage")]
    repetitions = conditions["repetitions"]
    verdicts = {PASS: PASS, "fail": FAIL, "unverified": UNKNOWN}
    outcomes = [verdicts.get(item["verdict"], UNKNOWN) for item in repetitions]
    outcomes.append(verdicts.get(conditions["session"], UNKNOWN))
    bad = sorted({str(item["verdict"]) for item in repetitions if item["verdict"] != PASS})
    result = [_check("conditions", _worst(outcomes),
                     f"session verdict {conditions['session']}" + (f"; repetitions {', '.join(bad)}" if bad else ""))]
    charging = {"satisfied": PASS, "violated": FAIL, "on-battery": FAIL, "unknown": UNKNOWN}
    states = sorted({str(item["charging"]) for item in repetitions})
    outcome = _worst([charging.get(item["charging"], UNKNOWN) for item in repetitions])
    if facts["chargingPolicy"] != CHARGING_POLICY and outcome != FAIL:
        result.append(_check("charging", UNKNOWN, f"{facts['chargingPolicy']} is not {CHARGING_POLICY}"))
    else:
        result.append(_check("charging", outcome, ", ".join(states) or "no measured repetition"))
    covered = [item["covered"] is True for item in repetitions]
    result.append(_check("coverage", PASS if covered and all(covered) else UNKNOWN,
                         f"{sum(covered)} of {len(covered)} repetitions sampled throughout"))
    return result


def _artifact(facts):
    artifact = facts["artifact"]
    if artifact is None:
        return _check("artifact", UNKNOWN, "no artifact hash")
    expected, recorded = artifact["configuredSha256"], artifact["recordedExpected"]
    if expected is None:
        return _check("artifact", UNKNOWN, "the configured run pins no modelSha256")
    if artifact["sha256"] != expected or recorded not in (None, expected):
        return _check("artifact", FAIL, "artifact SHA-256 differs from the configured modelSha256")
    return _check("artifact", PASS, "artifact SHA-256 equals the configured modelSha256")


def _backend_check(facts):
    status, backend = facts["backendEvidence"], facts["backend"]
    accepted = ("confirmed", "consistent") if backend == "cpu" else ("confirmed",)
    if status == "mismatch":
        return _check("backend", FAIL, "backend evidence contradicts the requested backend")
    if status in accepted:
        return _check("backend", PASS, f"{status} for {backend}")
    needed = " or ".join(accepted)
    return _check("backend", UNKNOWN, f"{status or 'no evidence'} for {backend}; needs {needed}")


def _gate_checks(facts):
    if facts["protocolVersion"] not in raw_session.ACCELERATOR_PROTOCOLS:
        return [_check("correctness", PASS, "not an accelerator protocol"),
                _check("comparison", PASS, "not an accelerator protocol")]
    correctness = {PASS: PASS, "fail": FAIL, "unverified": UNKNOWN}.get(facts["correctness"], UNKNOWN)
    comparison = {"valid": PASS, "invalid": FAIL, "unverified": UNKNOWN}.get(facts["comparison"], UNKNOWN)
    return [_check("correctness", correctness, f"output gate {facts['correctness']}"),
            _check("comparison", comparison, f"comparison {facts['comparison']}")]


def _build(facts):
    build = facts["build"]
    if build is None or build.get("status") != "reported":
        return _check("build", UNKNOWN, "build identity not reported")
    if build.get("gitDirty") is True:
        return _check("build", FAIL, "built from a dirty tree")
    if build.get("buildType") == "debug":
        # A debug build loads its JavaScript from Metro: the commit does not identify the code that ran.
        return _check("build", UNKNOWN, "debug build: its JavaScript is not part of the binary")
    if not (isinstance(build.get("gitCommit"), str) and build.get("gitDirty") is False
            and build.get("flavor") in KNOWN_FLAVORS and build.get("buildType") == "release"):
        return _check("build", UNKNOWN, "commit, clean tree, known flavor or release build not established")
    return _check("build", PASS, f"{build['flavor']} release, clean commit")


def _app_identity(facts):
    outcome = {"matched": PASS, "changed": FAIL}.get(facts["appIdentity"], UNKNOWN)
    return _check("app-identity", outcome, f"capture APK identity {facts['appIdentity']}")


def evaluate(facts):
    """publication-policy-v1. Pure: the same facts always give the same decision.

    verified needs every check to pass; any failed check gives failed, otherwise any
    unknown gives unverified. Unknown is never a pass. Experiments, bug reproductions and
    sessions without a measured repetition are never publishable at all.
    """
    refusal = None
    if facts["experiment"]:
        refusal = "experiment session (" + ", ".join(facts["experiment"]) + ")"
    elif facts["purpose"] == "bug-repro":
        refusal = "bug-repro session"
    elif not facts["measured"]:
        refusal = "no measured repetition"
    checks = [_complete(facts), *_conditions(facts), _artifact(facts), _backend_check(facts), *_gate_checks(facts),
              _build(facts), _app_identity(facts)]
    if tuple(item["check"] for item in checks) != CHECKS:
        raise AssertionError("publication-policy-v1 checks are out of order")
    outcome = _worst([item["outcome"] for item in checks])
    validation = {PASS: "verified", UNKNOWN: "unverified", FAIL: "failed"}[outcome]
    return {"policy": POLICY_VERSION, "publishable": refusal is None, "refusal": refusal, "validation": validation,
            "guardsPassed": {PASS: True, UNKNOWN: None, FAIL: False}[outcome], "checks": checks}


# ── Bundle report ────────────────────────────────────────────────────────────

def bundle_report(bundle, expected_sha=None):
    bundle = Path(bundle)
    verified = export_evidence.verify(bundle, expected_sha)
    report = {"policy": POLICY_VERSION, "valid": False, "bundleSha256": verified["bundleSha256"],
              "errors": list(verified["errors"]), "sessions": []}
    if not verified["valid"]:
        return report
    try:
        # Re-read only bytes that still hash to what verify() checked.
        manifest_bytes = (bundle / "manifest.json").read_bytes()
        if export_evidence.sha256(manifest_bytes) != verified["bundleSha256"]:
            raise ValueError("manifest changed after verification")
        manifest = json.loads(manifest_bytes)
        listed = {item["path"]: item["sha256"] for item in manifest["files"]}

        def read(path):
            data = (bundle / path).read_bytes()
            if export_evidence.sha256(data) != listed[path]:
                raise ValueError(f"{path} changed after verification")
            return json.loads(data)
        captures = {capture_id: read(f"captures/{capture_id}.json") for capture_id in manifest["captureIds"]}
        for capture_id, capture in sorted(captures.items()):
            error = app_identity(capture)[1]
            if error:
                report["errors"].append(f"captures/{capture_id}.json/appIdentity: {error}")
        sessions = []
        for session_id in manifest["sessionIds"]:
            doc = read(f"sessions/{session_id}.json")
            errors = recheck(doc["record"])
            report["errors"] += [f"sessions/{session_id}.json/record: recomputed evidence disagrees: {item}"
                                 for item in errors]
            if not errors:
                facts = session_facts(doc, captures[doc["captureId"]])
                sessions.append({"sessionId": session_id, "captureId": doc["captureId"], "facts": facts,
                                 "decision": evaluate(facts)})
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError) as error:
        # OS messages name host paths; only the policy's own messages are shown.
        detail = str(error) if isinstance(error, ValueError) and "after verification" in str(error) \
            else type(error).__name__
        report["errors"].append(f"bundle: unreadable or changed after verification ({detail})")
    if not report["errors"]:
        report["valid"], report["sessions"] = True, sessions
    return report


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bundle")
    parser.add_argument("--sha256", help="Expected bundleSha256")
    args = parser.parse_args(argv)
    report = bundle_report(args.bundle, args.sha256)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
