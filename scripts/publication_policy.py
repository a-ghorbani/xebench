#!/usr/bin/env python3
"""Publication policy v1: decide a board row's validation from a verified evidence bundle.

  publication_policy.py BUNDLE_DIR [--sha256 HEX]

Reference data comes from this repository: the reviewed run configurations
(xebenchapp/configs/*.json, experiment-* excluded), the model manifests
(configs/models/*-models.json) and the device table (configs/devices.json).

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
          "build", "app-identity", "device")
ROOT = Path(__file__).resolve().parents[1]
# A configured run's model identity; a reviewed run must agree on every one of these.
RUN_IDENTITY = ("engine", "backend", "model", "quant", "modelFile", "modelSha256")
# raw_session.TOLERANCE: engine-reported and published speeds are the same number.
TIMING_TOLERANCE = 1e-9


class ReferenceError_(ValueError):
    """The repository's reference data is missing or malformed: nothing can be decided."""


def load_reference(root=None):
    """Reviewed runs, model manifest hashes by file name, and SoC by device model."""
    root = Path(ROOT if root is None else root)
    try:
        runs, files = [], {}
        for path in sorted((root / "xebenchapp/configs").glob("*.json")):
            data = path.read_bytes()
            config = json.loads(data.decode("utf-8"))
            if str(config.get("id", "")).startswith("experiment-"):
                continue
            # The app records the SHA-256 of the configuration bytes it read (bundled or staged).
            files[export_evidence.sha256(data)] = {"name": path.name, "configuration": config}
            for run in config["runs"]:
                runs.append({key: run.get(key) for key in RUN_IDENTITY} | {"backend": run.get("backend", "cpu"),
                                                                          "config": path.name})
        manifests = {}
        for path in sorted((root / "configs/models").glob("*-models.json")):
            manifest = json.loads(path.read_text(encoding="utf-8"))
            # models[] are downloadable pins (stage_model.py); pinnedArtifacts[] pin bytes whose
            # source revision is unknown (the LiteRT-LM files, MODELS.md).
            for entry in manifest["models"] + manifest.get("pinnedArtifacts", []):
                manifests.setdefault(entry["file"], set()).add(entry["sha256"])
        devices = {}
        for entry in json.loads((root / "configs/devices.json").read_text(encoding="utf-8"))["devices"]:
            devices[entry["model"]] = entry["soc"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ReferenceError_(f"reference data unreadable ({type(error).__name__})")
    if not runs or not manifests or not devices:
        raise ReferenceError_("reference data is empty")
    return {"runs": runs, "files": files, "manifests": {key: sorted(value) for key, value in manifests.items()},
            "devices": devices}


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
        errors += schema_value_errors(record) + timing_errors(record)
    except (TypeError, ValueError, KeyError, IndexError, AttributeError, OverflowError):
        return ["record.unprocessable /"]
    return [f"{item['code']} {item['path'] or '/'}" for item in errors]


def timing_errors(record):
    """engine-timings speeds are the engine's own counters: where the record keeps them
    (engineInternal), the published speed must equal them. LiteRT-LM keeps none."""
    errors = []
    for index, attempt in enumerate(record["coldRuns"]):
        internal = attempt.get("engineInternal")
        if attempt.get("status") != "complete" or not isinstance(internal, dict):
            continue
        for metric, method, counter in (("decodeTps", "decodeMethod", "predictedPerSecond"),
                                        ("prefillTps", "prefillMethod", "promptPerSecond")):
            if attempt.get(method) != "engine-timings" or counter not in internal:
                continue
            value, engine = attempt[metric], internal[counter]
            if abs(value - engine) > TIMING_TOLERANCE * max(1.0, abs(value), abs(engine)):
                errors.append({"code": "timing.engine-mismatch", "path": f"/coldRuns/{index}/{metric}"})
    return errors


def schema_value_errors(record):
    """Values schemas/raw-session-v1.json fixes but the export allowlist does not check.

    Every session is an Android run, and the cpu-reference protocols measure the CPU
    only. Without this, a re-hashed bundle could relabel a CPU reference run as a GPU or
    NPU row (its execution report then reads as confirmed) or move it to another platform.
    """
    cpu_only = record["protocol"]["version"] not in raw_session.ACCELERATOR_PROTOCOLS
    allowed = {"platform": ("android",), "backend": ("cpu",) if cpu_only else ("cpu", "gpu", "npu")}
    return [{"code": "schema.value", "path": f"/{key}"} for key, values in allowed.items()
            if record.get(key) not in values]


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


def configuration_file(record, reference):
    """The reviewed configuration file the session ran: its configurationSource hash names the
    file and the session carries exactly that file's content (so its id too). None otherwise.

    A row names its configuration (configurationId, configurationSha256) only from this file,
    so a re-hashed session cannot claim a configuration the board displays."""
    source = record.get("configurationSource")
    entry = reference["files"].get(source.get("sha256")) if isinstance(source, dict) else None
    if entry is None or entry["configuration"] != record.get("configuration"):
        return None
    return {"name": entry["name"], "id": entry["configuration"].get("id"), "sha256": source["sha256"]}


def _reviewed(record, run, reference):
    """The reviewed configuration file this session ran, when its run has exactly this run's model identity.

    The session's configurationSource hash must name a reviewed file, and the configuration it
    carries must be that file's content. Matching a run of any reviewed file is not enough: a
    re-hashed bundle could relabel a run as another reviewed run with that run's hash.
    """
    source = record.get("configurationSource")
    entry = reference["files"].get(source.get("sha256")) if isinstance(source, dict) else None
    if run is None or entry is None or entry["configuration"] != record["configuration"]:
        return []
    identity = {key: run.get(key) for key in RUN_IDENTITY} | {"backend": run.get("backend", record["backend"])}
    return sorted(item["config"] for item in reference["runs"] if item["config"] == entry["name"]
                  and all(item[key] == identity[key] for key in RUN_IDENTITY))


def device_soc(doc, reference):
    """(the table's SoC for this device model or None, error when the bundle's SoC contradicts it)."""
    model, soc = doc["device"].get("model"), doc["device"].get("soc")
    expected = reference["devices"].get(model)
    if expected is not None and soc is not None and str(soc).casefold() != str(expected).casefold():
        return expected, f"SoC {soc} contradicts configs/devices.json ({model} ships with {expected})"
    return expected, None


def session_facts(doc, capture, reference=None):
    """The facts publication-policy-v1 needs, for one session document of a verified bundle."""
    reference = load_reference() if reference is None else reference
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
        "device": {"model": doc["device"].get("model"), "soc": doc["device"].get("soc"),
                   "tableSoc": device_soc(doc, reference)[0]},
        "configurationFile": configuration_file(record, reference),
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
        model_file = None if run is None else run.get("modelFile")
        facts["artifact"] = {"sha256": artifact["sha256"], "recordedExpected": artifact.get("expectedSha256"),
                             "configuredSha256": None if run is None else run.get("modelSha256"),
                             "reviewedConfigs": _reviewed(record, run, reference),
                             "manifestSha256": reference["manifests"].get(model_file, [])}
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
    manifest, reviewed = artifact.get("manifestSha256") or [], artifact.get("reviewedConfigs") or []
    if manifest and artifact["sha256"] not in manifest:
        return _check("artifact", FAIL, "artifact SHA-256 differs from the model manifest entry for its file")
    if not reviewed:
        return _check("artifact", UNKNOWN, "the configured run (engine, backend, model, quant, file, hash) is not "
                                           "a run of the reviewed configuration the session names")
    if not manifest:
        return _check("artifact", UNKNOWN, "no model manifest entry for its file")
    return _check("artifact", PASS, f"reviewed run ({', '.join(reviewed)}); hash matches the model manifest")


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


def _device(facts):
    device = facts.get("device") or {}
    expected, soc = device.get("tableSoc"), device.get("soc")
    if expected is None:
        return _check("device", UNKNOWN, f"device model {device.get('model')} is not in configs/devices.json")
    if soc is None:
        return _check("device", UNKNOWN, "the capture reports no SoC")
    if str(soc).casefold() != str(expected).casefold():
        return _check("device", FAIL, f"SoC {soc} contradicts the device table ({expected})")
    return _check("device", PASS, f"{device.get('model')} ships with {expected}")


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
              _build(facts), _app_identity(facts), _device(facts)]
    if tuple(item["check"] for item in checks) != CHECKS:
        raise AssertionError("publication-policy-v1 checks are out of order")
    outcome = _worst([item["outcome"] for item in checks])
    validation = {PASS: "verified", UNKNOWN: "unverified", FAIL: "failed"}[outcome]
    return {"policy": POLICY_VERSION, "publishable": refusal is None, "refusal": refusal, "validation": validation,
            "guardsPassed": {PASS: True, UNKNOWN: None, FAIL: False}[outcome], "checks": checks}


# ── Bundle report ────────────────────────────────────────────────────────────

def bundle_report(bundle, expected_sha=None, root=None):
    bundle = Path(bundle)
    verified = export_evidence.verify(bundle, expected_sha)
    report = {"policy": POLICY_VERSION, "valid": False, "bundleSha256": verified["bundleSha256"],
              "errors": list(verified["errors"]), "sessions": []}
    if not verified["valid"]:
        return report
    try:
        reference = load_reference(root)
    except ReferenceError_ as error:
        report["errors"].append(f"repository: {error}")
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
            # The row's device comes from the session; the app's own report of the model must
            # agree with the host's (verify already binds manufacturer and SoC to the capture).
            if doc["device"]["model"] != captures[doc["captureId"]]["device"]["model"]:
                report["errors"].append(f"sessions/{session_id}.json/device/model: disagrees with its capture's "
                                        "host device")
            contradiction = device_soc(doc, reference)[1]
            if contradiction:
                report["errors"].append(f"sessions/{session_id}.json/device/soc: {contradiction}")
            if not report["errors"]:
                facts = session_facts(doc, captures[doc["captureId"]], reference)
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
