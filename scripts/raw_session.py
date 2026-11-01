#!/usr/bin/env python3
"""Validate xebench raw reference sessions (schemaVersion 1) without dependencies.

Structure comes from schemas/raw-session-v1.json, interpreted by the small JSON
Schema subset below; unsupported schema keywords are an error rather than being
silently ignored. Cross-field rules that JSON Schema cannot express are checked
afterwards. Every problem is a machine-readable reason:
``{"code": ..., "path": <JSON Pointer>, "message": ...}``.

Usage: raw_session.py FILE... prints one JSON result per record and exits 1 if
any record is invalid. A file holds one JSON record, a JSON array of records, a
JSON object with a ``records`` mapping (test fixtures), or JSON Lines (.jsonl).
"""
import hashlib
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "raw-session-v1.json"
SUPPORTED_VERSIONS = (1,)
METRICS = ("loadMs", "ttftMs", "prefillTps", "decodeTps")
MAX_REASONS = 100
VALIDATION = "raw-session-v1"
# Summaries are recomputed with the app's exact float operations; the tolerance
# only absorbs representation noise, not a different statistic.
TOLERANCE = 1e-12

_ANNOTATIONS = {"$schema", "$id", "title", "description", "$comment", "$defs", "examples", "default"}
_KEYWORDS = _ANNOTATIONS | {
    "$ref", "type", "const", "enum", "properties", "required", "additionalProperties", "items",
    "minItems", "maxItems", "minLength", "maxLength", "pattern", "minimum", "maximum",
    "exclusiveMinimum", "oneOf",
}


class Invalid(ValueError):
    """Raised by callers that need to abort with machine-readable reasons."""

    def __init__(self, reasons, payload=None):
        super().__init__("; ".join(f"{r['code']} at {r['path'] or '/'}" for r in reasons))
        self.reasons = reasons
        self.payload = payload


def reason(code, path, message):
    return {"code": code, "path": path, "message": message}


def _pointer(path, key):
    return f"{path}/{str(key).replace('~', '~0').replace('/', '~1')}"


def parse(text):
    """Strict JSON: no NaN/Infinity, no overflowing exponents, no duplicate keys."""
    def constant(value):
        raise Invalid([reason("json.non-finite", "", f"Non-finite JSON constant {value}")])

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise Invalid([reason("json.duplicate-key", "", f"Duplicate key {key!r}")])
            result[key] = value
        return result

    try:
        value = json.loads(text, parse_constant=constant, object_pairs_hook=pairs)
    except json.JSONDecodeError as error:
        raise Invalid([reason("json.parse", "", f"Malformed or truncated JSON: {error.msg} at char {error.pos}")])
    except RecursionError:
        raise Invalid([reason("json.parse", "", "JSON nesting too deep")])
    except Invalid:
        raise
    except ValueError as error:
        # e.g. integers beyond Python's digit limit.
        raise Invalid([reason("json.parse", "", f"Unsupported JSON value: {error}")])
    found = _non_finite(value, "")
    if found is not None:
        raise Invalid([reason("json.non-finite", found, "Number overflows to a non-finite value")])
    return value


def _non_finite(value, path):
    """First non-finite number's JSON Pointer; iterative so deep input cannot overflow."""
    stack = [(value, path)]
    while stack:
        node, where = stack.pop()
        if isinstance(node, float) and not math.isfinite(node):
            return where
        if isinstance(node, int) and not isinstance(node, bool):
            # Integer literals beyond the double range are Infinity to the app's
            # JSON.parse and would overflow the host's float arithmetic.
            try:
                float(node)
            except OverflowError:
                return where
        children = node.items() if isinstance(node, dict) else enumerate(node) if isinstance(node, list) else ()
        stack.extend((child, _pointer(where, key)) for key, child in reversed(list(children)))
    return None


def _pattern(pattern):
    """ECMA-262 '$' never matches before a trailing newline; Python's does."""
    if pattern.endswith("$") and not pattern.endswith("\\$"):
        pattern = pattern[:-1] + r"\Z"
    return re.compile(pattern)


def load_schema(path=SCHEMA_PATH):
    schema = json.loads(Path(path).read_text(encoding="utf-8"))
    _check_keywords(schema, "#")
    return schema


_TYPES = {"null", "boolean", "object", "array", "string", "number", "integer"}


def _check_keywords(node, where, root=None):
    """Fail loudly on schema features this interpreter would otherwise ignore."""
    if not isinstance(node, dict):
        raise ValueError(f"Schema must be an object at {where}")
    root = node if root is None else root
    unknown = set(node) - _KEYWORDS
    if unknown:
        raise ValueError(f"Unsupported JSON Schema keyword(s) {sorted(unknown)} at {where}")
    if "$ref" in node and set(node) - _ANNOTATIONS - {"$ref"}:
        raise ValueError(f"Keywords beside $ref are not supported at {where}")
    # Broken references or patterns must fail at load time, not on the first
    # record that reaches that branch (where they would escape as KeyError/re.error).
    if "$ref" in node:
        try:
            target = _Validator(root).resolve(node["$ref"])
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError(f"Unresolvable $ref at {where}") from error
        if not isinstance(target, dict):
            raise ValueError(f"$ref does not name a schema at {where}")
    if "pattern" in node:
        try:
            _pattern(node["pattern"])
        except (re.error, TypeError, AttributeError) as error:
            raise ValueError(f"Invalid pattern at {where}") from error
    if "type" in node:
        names = node["type"] if isinstance(node["type"], list) else [node["type"]]
        if not names or set(names) - _TYPES:
            raise ValueError(f"Unsupported type {node['type']!r} at {where}")
    for key in ("properties", "$defs"):
        for name, child in node.get(key, {}).items():
            _check_keywords(child, f"{where}/{key}/{name}", root)
    if "items" in node:
        _check_keywords(node["items"], f"{where}/items", root)
    if isinstance(node.get("additionalProperties"), dict):
        _check_keywords(node["additionalProperties"], f"{where}/additionalProperties", root)
    elif not isinstance(node.get("additionalProperties", True), bool):
        raise ValueError(f"additionalProperties must be a boolean or schema at {where}")
    for index, child in enumerate(node.get("oneOf", [])):
        _check_keywords(child, f"{where}/oneOf/{index}", root)


def _is_type(value, name):
    if name == "null":
        return value is None
    if name == "boolean":
        return isinstance(value, bool)
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list)
    if name == "string":
        return isinstance(value, str)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return name == "number" or isinstance(value, int) or value.is_integer()


def _equal(a, b):
    # JSON equality: booleans are not numbers, and 1 == 1.0.
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    return a == b


class _Validator:
    def __init__(self, root):
        self.root = root

    def resolve(self, ref):
        if not ref.startswith("#/"):
            raise ValueError(f"Only local $ref is supported: {ref}")
        node = self.root
        for part in ref[2:].split("/"):
            node = node[part.replace("~1", "/").replace("~0", "~")]
        return node

    def validate(self, value, schema, path):
        if "$ref" in schema:
            return self.validate(value, self.resolve(schema["$ref"]), path)
        errors = []
        if "type" in schema:
            names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
            if not any(_is_type(value, name) for name in names):
                return [reason("schema.type", path, f"Expected {' or '.join(names)}")]
        if "const" in schema and not _equal(value, schema["const"]):
            errors.append(reason("schema.const", path, f"Expected {json.dumps(schema['const'])}"))
        if "enum" in schema and not any(_equal(value, option) for option in schema["enum"]):
            errors.append(reason("schema.enum", path, f"Expected one of {json.dumps(schema['enum'])}"))
        if "oneOf" in schema:
            errors += self.one_of(value, schema["oneOf"], path)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]:
                errors.append(reason("schema.minimum", path, f"Must be >= {schema['minimum']}"))
            if "maximum" in schema and value > schema["maximum"]:
                errors.append(reason("schema.maximum", path, f"Must be <= {schema['maximum']}"))
            if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
                errors.append(reason("schema.exclusive-minimum", path, f"Must be > {schema['exclusiveMinimum']}"))
        if isinstance(value, str):
            if "minLength" in schema and len(value) < schema["minLength"]:
                errors.append(reason("schema.min-length", path, f"Shorter than {schema['minLength']}"))
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                errors.append(reason("schema.max-length", path, f"Longer than {schema['maxLength']}"))
            if "pattern" in schema and not _pattern(schema["pattern"]).search(value):
                errors.append(reason("schema.pattern", path, "Does not match the required pattern"))
        if isinstance(value, list):
            if "minItems" in schema and len(value) < schema["minItems"]:
                errors.append(reason("schema.min-items", path, f"Fewer than {schema['minItems']} items"))
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                errors.append(reason("schema.max-items", path, f"More than {schema['maxItems']} items"))
            if "items" in schema:
                for index, item in enumerate(value):
                    errors += self.validate(item, schema["items"], _pointer(path, index))
        if isinstance(value, dict):
            properties = schema.get("properties", {})
            for key in schema.get("required", []):
                if key not in value:
                    errors.append(reason("schema.required", _pointer(path, key), "Missing required field"))
            extra = schema.get("additionalProperties", True)
            for key, child in value.items():
                if key in properties:
                    errors += self.validate(child, properties[key], _pointer(path, key))
                elif extra is False:
                    errors.append(reason("schema.additional-property", _pointer(path, key), "Unknown field"))
                elif isinstance(extra, dict):
                    errors += self.validate(child, extra, _pointer(path, key))
        return errors

    def one_of(self, value, options, path):
        results = [self.validate(value, option, path) for option in options]
        matches = sum(1 for result in results if not result)
        if matches == 1:
            return []
        if matches > 1:
            return [reason("schema.one-of", path, "Matches more than one alternative")]
        # Report the alternative selected by its const discriminators (attempt
        # status, harness and protocol version) or by type, so reasons name the
        # real defect instead of every branch. Options are ranked by fewest
        # contradicted consts, then most matched ones; a tie stays generic.
        ranked = []
        for option, result in zip(options, results):
            target = self.resolve(option["$ref"]) if "$ref" in option else option
            consts = self._discriminators(target)
            if consts and isinstance(value, dict):
                matched = contradicted = 0
                for keys, const in consts.items():
                    node = value
                    for key in keys:
                        node = node.get(key, _ABSENT) if isinstance(node, dict) else _ABSENT
                    if node is _ABSENT:
                        continue
                    if _equal(node, const):
                        matched += 1
                    else:
                        contradicted += 1
                ranked.append(((contradicted, -matched), result))
        if ranked:
            ranked.sort(key=lambda item: item[0])
            if len(ranked) == 1 or ranked[0][0] != ranked[1][0]:
                return ranked[0][1]
        for option, result in zip(options, results):
            target = self.resolve(option["$ref"]) if "$ref" in option else option
            if self._discriminators(target):
                continue
            if isinstance(target.get("type"), str) and target["type"] != "null" and _is_type(value, target["type"]):
                return result
        return [reason("schema.one-of", path, "Matches no allowed alternative")]

    def _discriminators(self, target):
        """Const properties of an object schema, plus one nested level (e.g. protocol.version)."""
        found = {}
        for key, child in target.get("properties", {}).items():
            if isinstance(child, dict) and "$ref" in child:
                child = self.resolve(child["$ref"])
            if not isinstance(child, dict):
                continue
            if "const" in child:
                found[(key,)] = child["const"]
            for sub, grand in child.get("properties", {}).items():
                if isinstance(grand, dict) and "const" in grand:
                    found[(key, sub)] = grand["const"]
        return found


_ABSENT = object()
_SCHEMA = None


def schema():
    global _SCHEMA
    if _SCHEMA is None:
        _SCHEMA = load_schema()
    return _SCHEMA


def aggregate(values):
    """Mirror of session.ts aggregate(): linear interpolation at (n - 1) * p."""
    if not values:
        return None
    ordered = sorted(values)

    def percentile(p):
        index = (len(ordered) - 1) * p
        lo, hi = math.floor(index), math.ceil(index)
        return ordered[lo] + (ordered[hi] - ordered[lo]) * (index - lo)

    return {"median": percentile(0.5), "iqr": percentile(0.75) - percentile(0.25),
            "min": ordered[0], "max": ordered[-1], "n": len(ordered)}


def _close(a, b):
    return math.isclose(a, b, rel_tol=TOLERANCE, abs_tol=TOLERANCE)


def identity_errors(record):
    """Rules every protocol shares: protocol/configuration agreement, unique run IDs, the
    configured run behind the record, repetition numbering and count."""
    errors = []
    protocol, config = record["protocol"], record["configuration"]
    for key in ("nColdRuns", "cooldownSec", "maxDecodeTokens"):
        if protocol[key] != config[key]:
            errors.append(reason("protocol.configuration-mismatch", f"/protocol/{key}",
                                 f"Protocol {key} disagrees with the configuration"))
    run_ids = [run["id"] for run in config["runs"]]
    if len(set(run_ids)) != len(run_ids):
        errors.append(reason("configuration.duplicate-run-id", "/configuration/runs", "Run IDs must be unique"))
    runs = [run for run in config["runs"] if run["id"] == record["configurationRunId"]]
    if not runs:
        errors.append(reason("configuration.unknown-run", "/configurationRunId", "Run is not in the configuration"))
    else:
        run = runs[0]
        for key, actual in (("engine", record["engine"]), ("model", record["model"]), ("quant", record["quant"]),
                            ("modelFile", record["modelFile"]), ("nThreads", protocol["nThreads"]),
                            ("nCtx", protocol["nCtx"])):
            if run[key] != actual:
                errors.append(reason("configuration.run-mismatch", "/configurationRunId",
                                     f"Record {key} disagrees with the configured run"))

    attempts, expected = record["coldRuns"], protocol["nColdRuns"]
    for index, attempt in enumerate(attempts):
        if attempt["runIndex"] != index:
            errors.append(reason("attempts.run-index", f"/coldRuns/{index}/runIndex",
                                 "Repetitions must be numbered consecutively from 0"))
    if len(attempts) > expected:
        errors.append(reason("attempts.count", "/coldRuns",
                             f"{len(attempts)} attempts exceed the configured {expected}"))
    return errors


def summary_errors(record):
    """The summary must be recomputable from the successful repetitions."""
    errors = []
    complete = [attempt for attempt in record["coldRuns"] if attempt["status"] == "complete"]
    for metric in METRICS:
        actual, recomputed = record["summary"][metric], aggregate([a[metric] for a in complete])
        where = f"/summary/{metric}"
        if recomputed is None or actual is None:
            if recomputed is not actual:
                errors.append(reason("summary.mismatch", where,
                                     "Summary must be null exactly when no repetition succeeded"))
            continue
        if actual["n"] != recomputed["n"]:
            errors.append(reason("summary.count", f"{where}/n",
                                 f"Reported n={actual['n']} but {recomputed['n']} repetitions succeeded"))
            continue
        for key in ("median", "iqr", "min", "max"):
            if not _close(actual[key], recomputed[key]):
                errors.append(reason("summary.mismatch", f"{where}/{key}",
                                     f"Reported {actual[key]!r}; repetitions give {recomputed[key]!r}"))
    return errors


def _semantic(record):
    errors = identity_errors(record)
    protocol = record["protocol"]
    attempts, expected, status = record["coldRuns"], protocol["nColdRuns"], record["status"]
    # cpu-reference-v3 keeps the v2 measurement procedure and adds identity evidence;
    # accelerator-v1 shares every v2/v3 rule and adds its own (_semantic_accelerator).
    v2 = protocol["version"] in ("cpu-reference-v2", "cpu-reference-v3") + ACCELERATOR_PROTOCOLS
    failed = [index for index, attempt in enumerate(attempts) if attempt["status"] == "failed"]
    if v2:
        pass  # Status, stop reason and skipped repetitions: see _semantic_v2.
    elif status == "complete" and (failed or len(attempts) != expected):
        errors.append(reason("status.attempts", "/status",
                             f"A complete session needs {expected} successful attempts; "
                             f"found {len(attempts) - len(failed)} of {len(attempts)}"))
    elif status == "running" and (failed or len(attempts) >= expected):
        errors.append(reason("status.attempts", "/status",
                             "A running checkpoint must have only successful attempts and fewer than configured"))
    elif status == "failed" and failed != [len(attempts) - 1]:
        errors.append(reason("status.attempts", "/status",
                             "A failed session must end at its only failed attempt"))

    errors += summary_errors(record)

    if v2:
        errors += _semantic_v2(record)
        if protocol["version"] == "cpu-reference-v3":
            errors += _semantic_v3(record)
        elif protocol["version"] == ACCELERATOR_PROTOCOL_V1:
            # Frozen: records of the app built before the build/execution identity work.
            errors += _semantic_accelerator(record, identity=False)
        elif protocol["version"] == ACCELERATOR_PROTOCOL:
            errors += _semantic_v3(record) + _semantic_accelerator(record, identity=True)
        return errors
    # Apps built before condition snapshots (PR #11) omit all condition evidence;
    # a mix of present and missing evidence is never valid.
    observed = ["conditions" in attempt for attempt in attempts] + ["conditionPolicy" in record]
    if any(observed) and not all(observed):
        errors.append(reason("conditions.inconsistent", "/conditionPolicy",
                             "conditionPolicy and every attempt's conditions must be present together"))
        return errors
    if not any(observed):
        if record["guardsPassed"] is not None:
            errors.append(reason("guards.inconsistent", "/guardsPassed",
                                 "Without condition evidence guardsPassed must stay null"))
        for index, attempt in enumerate(attempts):
            if attempt["status"] == "failed" and attempt["error"]["phase"] == "conditions":
                errors.append(reason("conditions.inconsistent", f"/coldRuns/{index}/error/phase",
                                     "A conditions-phase failure requires condition evidence"))
    flagged = [assessment for attempt in attempts if "conditions" in attempt for assessment in
               (attempt["conditions"]["beforeAssessment"], attempt["conditions"]["afterAssessment"])
               if assessment["status"] == "fail"]
    if flagged and record["guardsPassed"] is not False:
        errors.append(reason("guards.inconsistent", "/guardsPassed",
                             "A failed condition assessment requires guardsPassed=false"))
    missing = sorted({note for item in flagged for note in item["reasons"]} - set(record["guardNotes"]))
    if missing:
        errors.append(reason("guards.inconsistent", "/guardNotes",
                             f"Failed condition reasons missing from guardNotes: {missing}"))
    return errors


# --- cpu-reference-v2 / run-conditions-v2 -------------------------------------
# These mirror xebenchapp/src/harness/conditions.ts, readiness.ts, session.ts and
# order.ts. Assessments, readiness decisions, the condition verdict and the run
# order are recomputed from the recorded evidence, so a record cannot claim a
# pass (or hide a failure) that its own snapshots do not support.

CONDITION_POLICY_V2 = {
    "version": "run-conditions-v2",
    "required": ["batteryPct", "batteryTempC", "powerSaveMode", "thermalStatus", "screenOn", "foreground",
                 "keyguardLocked", "focusLossCount"],
    "readiness": {"mode": "wait-then-skip", "pollSec": 5, "maxThermalStatus": 0, "maxBatteryTempC": 40,
                  "maxBatteryTempRiseC": 2, "maxThermalHeadroom": 0.8, "headroom": "applied-when-measured",
                  "headroomConfirmMs": 1500},
    "run": {"mode": "record-and-flag", "minBatteryPct": 50, "maxThermalStatus": 2,
            "sampling": {"intervalMs": 1000, "maxDurationMs": 900000, "persistedSamples": 30, "maxGapFactor": 2,
                         "signals": "cheap-native-reads-no-pss"},
            "interruptions": "focus-loss-count"},
    "memory": "endpoint-pss-and-sampled-rss-not-peak",
}
CHARGING_POLICY_V3 = {
    "version": "charging-policy-v3", "name": "tethered-not-charging", "externalPower": "allowed",
    "notChargingStatuses": [4, 5], "chargingStatus": 2, "dischargingStatus": 3, "onBattery": "not-eligible",
    "currentNow": "diagnostic-only",
    "readiness": "wait-while-charging", "unknown": "never-eligible", "effect": "publication-eligibility-only",
}
# Earlier status-only policy (same rules, but DISCHARGING counted as unknown); kept so records
# from builds that wrote it keep validating with their own semantics.
CHARGING_POLICY_V2 = {key: value for key, value in CHARGING_POLICY_V3.items()
                      if key not in ("dischargingStatus", "onBattery")}
CHARGING_POLICY_V2["version"] = "charging-policy-v2"
CHARGING_POLICIES = {policy["version"]: policy for policy in (CHARGING_POLICY_V2, CHARGING_POLICY_V3)}
# EXPERIMENT ONLY (docs/experiments/charging-ab.md): configuration experimentChargingReadiness
# "record-only" records charging without waiting for it. Such records carry this policy (v3
# classification, record-only readiness) and are never publication evidence.
EXPERIMENT_CHARGING_POLICY_V3 = {**CHARGING_POLICY_V3, "readiness": "record-only-experiment"}
STOP_REASONS_UNMEASURED = ("configuration-aborted", "setup-failed", "model-hash-mismatch")
# Unmeasured runs recorded before the model was hashed; a hash mismatch keeps its artifact.
STOP_REASONS_UNHASHED = ("configuration-aborted", "setup-failed")
BACKEND_NOTE = "Actual backend execution has not been verified"
UNVERIFIED_NOTE = "Run conditions not fully verified"
UNMEASURED_NOTES = {"configuration-aborted": "Not run: an earlier run aborted the configuration",
                    "setup-failed": "Not run: the model artifact could not be read",
                    "model-hash-mismatch": "Not run: the model artifact does not match the configured SHA-256"}
# accelerator-v1 (xebenchapp/src/harness/accelerator.ts) adds two ways a run is recorded unmeasured.
# accelerator-v2 adds the cpu-reference-v3 build and execution identity to accelerator-v1.
ACCELERATOR_PROTOCOL = "accelerator-v2"
ACCELERATOR_PROTOCOL_V1 = "accelerator-v1"
ACCELERATOR_PROTOCOLS = (ACCELERATOR_PROTOCOL_V1, ACCELERATOR_PROTOCOL)
ACCELERATOR_UNMEASURED_NOTES = {**UNMEASURED_NOTES,
                                "engine-unavailable": "Not run: the engine is not linked in this build",
                                "gate-cleanup-failed": "Not run: the correctness gate could not release the engine",
                                # ExecuTorch runs (accelerator-v2): the tokenizer is pinned like the model.
                                "tokenizer-hash-mismatch": "Not run: the tokenizer does not match the configured SHA-256"}
_M32 = 0xFFFFFFFF


def _missing_required(snapshot):
    return [key for key in CONDITION_POLICY_V2["required"] if snapshot.get(key) is None]


def condition_failures(snapshot):
    run, reasons = CONDITION_POLICY_V2["run"], []
    battery, thermal = snapshot.get("batteryPct"), snapshot.get("thermalStatus")
    if battery is not None and battery < run["minBatteryPct"]:
        reasons.append("low-battery")
    if thermal is not None and thermal > run["maxThermalStatus"]:
        reasons.append("severe-thermal-state")
    for key, bad, name in (("powerSaveMode", True, "power-saving"), ("screenOn", False, "screen-off"),
                           ("foreground", False, "not-foreground"), ("keyguardLocked", True, "device-locked")):
        if snapshot.get(key) is bad:
            reasons.append(name)
    return reasons


def assess_conditions(snapshot):
    reasons = condition_failures(snapshot)
    status = "fail" if reasons else (
        "unverified" if _missing_required(snapshot) or snapshot.get("probeStatus") != "complete" else "pass")
    return {"status": status, "reasons": reasons}


def assess_readiness(snapshot, baseline, max_rise=None, wait_while_charging=True):
    policy = CONDITION_POLICY_V2["readiness"]
    reasons = [] if snapshot.get("probeStatus") == "complete" else ["probe-incomplete"]
    reasons += [f"unknown-{key}" for key in _missing_required(snapshot)] + condition_failures(snapshot)
    thermal, temp, headroom = snapshot.get("thermalStatus"), snapshot.get("batteryTempC"), snapshot.get("thermalHeadroom")
    if thermal is not None and thermal > policy["maxThermalStatus"]:
        reasons.append("thermal-not-ready")
    if temp is not None and temp > policy["maxBatteryTempC"]:
        reasons.append("battery-too-hot")
    rise = policy["maxBatteryTempRiseC"] if max_rise is None else max_rise
    if temp is not None and baseline is not None and temp > baseline + rise:
        reasons.append("battery-not-cooled")
    if headroom is not None and headroom > policy["maxThermalHeadroom"]:
        reasons.append("thermal-headroom-low")
    if wait_while_charging and charging_state(snapshot) == "charging":
        reasons.append("charging")
    return list(dict.fromkeys(reasons))


def _deep_equal(a, b):
    """JSON equality that, unlike Python's ==, never equates booleans and numbers."""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_deep_equal(a[key], b[key]) for key in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_deep_equal(x, y) for x, y in zip(a, b))
    return _equal(a, b) and isinstance(a, (dict, list)) == isinstance(b, (dict, list))


def summarize_samples(samples, started, stopped, policy=CHARGING_POLICY_V3):
    """Mirror of conditions.ts summarizeSamples over persisted samples."""
    def extreme(key, pick):
        known = [sample[key] for sample in samples if sample.get(key) is not None]
        return pick(known) if known else None

    times = [started, *(sample.get("elapsedMs") for sample in samples), stopped]
    gap = None
    if None not in times:
        steps = [later - earlier for earlier, later in zip(times, times[1:])]
        gap = None if any(step < 0 for step in steps) else max([0, *steps])
    states = _empty_states(policy)
    for sample in samples:
        states[charging_state(sample, policy)] += 1
    return {
        "samples": len(samples),
        "incompleteSamples": sum(1 for sample in samples
                                 if sample.get("probeStatus") != "complete" or _missing_required(sample)),
        "failures": list(dict.fromkeys(item for sample in samples for item in condition_failures(sample))),
        "maxThermalStatus": extreme("thermalStatus", max), "maxThermalHeadroom": extreme("thermalHeadroom", max),
        "maxBatteryTempC": extreme("batteryTempC", max), "minBatteryPct": extreme("batteryPct", min),
        "maxRssMb": extreme("rssMb", max), "maxGapMs": gap,
        "externalPower": {"connected": sum(1 for sample in samples if sample.get("charging") is True),
                          "disconnected": sum(1 for sample in samples if sample.get("charging") is False),
                          "unknown": sum(1 for sample in samples if sample.get("charging") is None)},
        "chargingStates": states,
    }


def _check_monitor(monitor, where, policy=CHARGING_POLICY_V3):
    """Persisted samples must support the summary: all of it when every sample is kept."""
    errors, summary, samples = [], monitor["summary"], monitor["samples"]
    limit = CONDITION_POLICY_V2["run"]["sampling"]["persistedSamples"]
    started, stopped = monitor["startedElapsedMs"], monitor["stoppedElapsedMs"]
    times = [sample.get("elapsedMs") for sample in samples]
    if None not in times and None not in (started, stopped) and (
            times != sorted(times) or (times and (times[0] < started or times[-1] > stopped))):
        errors.append(reason("conditions.monitor", f"{where}/samples",
                             "Sample times must increase within the monitor's start and stop"))
    if summary["samples"] <= limit:
        if len(samples) != summary["samples"]:
            errors.append(reason("conditions.monitor", f"{where}/samples",
                                 "Every sample is persisted when there are at most the policy's limit"))
        elif not _deep_equal(summarize_samples(samples, started, stopped, policy), summary):
            errors.append(reason("conditions.monitor", f"{where}/summary",
                                 "Summary differs from the one recomputed from the persisted samples"))
    elif len(samples) != limit:
        errors.append(reason("conditions.monitor", f"{where}/samples",
                             "A decimated monitor persists exactly the policy's sample limit"))
    else:
        partial = summarize_samples(samples, started, stopped, policy)
        bounds = [partial["incompleteSamples"] <= summary["incompleteSamples"],
                  all(partial["chargingStates"][key] <= summary["chargingStates"][key] for key in partial["chargingStates"]),
                  all(partial["externalPower"][key] <= summary["externalPower"][key] for key in partial["externalPower"])]
        if summary["maxGapMs"] is not None and None not in times and None not in (started, stopped) and times:
            bounds.append(summary["maxGapMs"] >= max(times[0] - started, stopped - times[-1]))
        if not all(bounds):
            errors.append(reason("conditions.monitor", f"{where}/summary",
                                 "Summary is smaller than the persisted samples show"))
    return errors


def _empty_states(policy):
    return {"notCharging": 0, "charging": 0, **({"onBattery": 0} if "dischargingStatus" in policy else {}),
            "unknown": 0}


def charging_state(snapshot, policy=CHARGING_POLICY_V3):
    """charging-policy-v2: decided by batteryStatus alone; current_now is diagnostic only."""
    status = snapshot.get("batteryStatus")
    if status in policy["notChargingStatuses"]:
        return "notCharging"
    if status == policy["chargingStatus"]:
        return "charging"
    return "onBattery" if status == policy.get("dischargingStatus") else "unknown"


def charging_counts(conditions, measured, policy=CHARGING_POLICY_V3):
    counts = _empty_states(policy)
    for snapshot in (conditions["before"], conditions["after"]):
        if snapshot is not None:
            counts[charging_state(snapshot, policy)] += 1
    if conditions["during"] is not None:
        for key in counts:
            counts[key] += conditions["during"]["summary"]["chargingStates"].get(key, 0)
    # An unsampled stretch (no monitor, expiry, gaps, dropped ticks) is an unknown observation.
    # Both policy versions require this ("every measured repetition was sampled"), so relabelling
    # a record between v2 and v3 cannot make an unsampled run eligible.
    if measured and not (conditions["during"] is not None and _monitor_covered(conditions["during"])):
        counts["unknown"] += 1
    return counts


def assess_charging(counts):
    if counts["charging"]:
        return "violated"
    if counts.get("onBattery"):
        return "on-battery"
    return "unknown" if counts["unknown"] or not counts["notCharging"] else "satisfied"


def _pending_probe(snapshot):
    return snapshot is not None and snapshot.get("probeStatus") in ("timeout", "busy")


def _pending_monitor(monitor):
    return monitor is not None and monitor["status"] in ("busy", "timeout", "stuck", "error")


def _sampled_throughout(monitor):
    interval, start, stop = monitor["intervalMs"], monitor["startedElapsedMs"], monitor["stoppedElapsedMs"]
    ticks, summary = monitor["ticks"], monitor["summary"]
    sampling = CONDITION_POLICY_V2["run"]["sampling"]
    if None in (interval, start, stop, ticks, summary["maxGapMs"]) or summary["samples"] < 1 or stop < start \
            or interval != sampling["intervalMs"] or stop - start > sampling["maxDurationMs"]:
        return False
    factor = sampling["maxGapFactor"]
    return summary["maxGapMs"] <= factor * interval and ticks >= math.floor((stop - start) / interval)


def _monitor_covered(monitor):
    """Mirror of conditions.ts monitorCovered: the whole run was sampled at the policy rate."""
    return (monitor["status"] == "complete" and monitor["expired"] is False and monitor["dropped"] == 0
            and monitor["ticks"] == monitor["summary"]["samples"] and _sampled_throughout(monitor))


def assess_during(monitor, before, after):
    reasons = list(monitor["summary"]["failures"])
    lost_before, lost_after = before.get("focusLossCount"), after.get("focusLossCount")
    if lost_before is not None and lost_after is not None and lost_after > lost_before:
        reasons.append("interrupted")
    summary = monitor["summary"]
    complete = (_monitor_covered(monitor) and summary["incompleteSamples"] == 0
                and lost_before is not None and lost_after is not None)
    reasons = list(dict.fromkeys(reasons))
    return {"status": "fail" if reasons else "pass" if complete else "unverified", "reasons": reasons}


def condition_verdict(record):
    policy = CHARGING_POLICIES.get(record.get("chargingPolicy", {}).get("version"), CHARGING_POLICY_V3)
    reasons, power = {}, _empty_states(policy)
    attempts = record["coldRuns"]
    unverified = len(attempts) < record["repetitionsPlanned"]
    for attempt in attempts:
        readiness, conditions = attempt["readiness"], attempt["conditions"]
        unknown_only = all(item in ("probe-incomplete", "charging") or item.startswith("unknown-")
                           for item in readiness["reasons"])
        if readiness["status"] == "timeout" and not unknown_only:
            reasons["readiness-timeout"] = None
        if readiness["status"] == "probe-pending" or _pending_probe(conditions["after"]) \
                or _pending_monitor(conditions["during"]):
            reasons["condition-probe-pending"] = None
        if attempt["status"] != "complete":
            unverified = True
        for assessment in (conditions["beforeAssessment"], conditions["duringAssessment"], conditions["afterAssessment"]):
            if assessment is None or assessment["status"] == "unverified":
                unverified = True
            elif assessment["status"] == "fail":
                reasons.update(dict.fromkeys(assessment["reasons"]))
        for key, value in charging_counts(conditions, attempt["status"] != "skipped", policy).items():
            power[key] += value
    status = "fail" if reasons else "unverified" if unverified or not attempts else "pass"
    return {"status": status, "reasons": list(reasons), "charging": assess_charging(power)}


def seeded_order(items, seed):
    """mulberry32 + Fisher-Yates, bit-exact with xebenchapp/src/harness/order.ts."""
    state = seed & _M32

    def imul(a, b):
        return (a * b) & _M32

    def random():
        nonlocal state
        state = (state + 0x6D2B79F5) & _M32
        value = state
        value = imul(value ^ (value >> 15), value | 1)
        value ^= (value + imul(value ^ (value >> 7), value | 61)) & _M32
        return ((value ^ (value >> 14)) & _M32) / 4294967296

    result = list(items)
    for index in range(len(result) - 1, 0, -1):
        swap = math.floor(random() * (index + 1))
        result[index], result[swap] = result[swap], result[index]
    return result


def _semantic_v2(record):
    errors = []
    protocol, config, attempts = record["protocol"], record["configuration"], record["coldRuns"]
    planned, status, stop = record["repetitionsPlanned"], record["status"], record["stopReason"]
    accelerator = protocol["version"] in ACCELERATOR_PROTOCOLS
    unmeasured_notes = ACCELERATOR_UNMEASURED_NOTES if accelerator else UNMEASURED_NOTES
    if planned != protocol["nColdRuns"]:
        errors.append(reason("protocol.configuration-mismatch", "/repetitionsPlanned",
                             "repetitionsPlanned must equal protocol.nColdRuns"))
    if protocol.get("maxBatteryTempRiseC") != config.get("maxBatteryTempRiseC"):
        errors.append(reason("protocol.configuration-mismatch", "/protocol/maxBatteryTempRiseC",
                             "Protocol maxBatteryTempRiseC disagrees with the configuration"))
    if protocol["readinessTimeoutSec"] != config.get("readinessTimeoutSec"):
        errors.append(reason("protocol.configuration-mismatch", "/protocol/readinessTimeoutSec",
                             "Protocol readinessTimeoutSec disagrees with the configuration"))
    charging_policy = CHARGING_POLICIES.get(record["chargingPolicy"]["version"], CHARGING_POLICY_V3)
    experiment = config.get("experimentChargingReadiness")
    if experiment is not None and experiment != "record-only":
        errors.append(reason("protocol.configuration-mismatch", "/configuration/experimentChargingReadiness",
                             "experimentChargingReadiness only supports record-only"))
    if experiment is not None:
        # Accepted only with the flag, and only with the current (v3) classification.
        charging_policy = EXPERIMENT_CHARGING_POLICY_V3
    if not _deep_equal(record["chargingPolicy"], charging_policy):
        errors.append(reason("conditions.policy", "/chargingPolicy",
                             f"chargingPolicy must equal the published {record['chargingPolicy']['version']} values"
                             + (" with record-only-experiment readiness" if experiment is not None else "")))
    wait_while_charging = record["chargingPolicy"].get("readiness") != "record-only-experiment"
    if not _deep_equal(record["conditionPolicy"], CONDITION_POLICY_V2):
        errors.append(reason("conditions.policy", "/conditionPolicy",
                             "conditionPolicy must equal the published run-conditions-v2 values"))

    # Session status, stop reason and the shape of the attempt list.
    kinds = [attempt["status"] for attempt in attempts]
    earlier_ok = all(kind == "complete" for kind in kinds[:-1])
    last = attempts[-1] if attempts else None
    if status in ("running", "complete"):
        ok = (stop is None and all(kind == "complete" for kind in kinds) and
              (len(attempts) == planned if status == "complete" else 0 < len(attempts) < planned))
    elif stop in unmeasured_notes:
        ok = not attempts
    elif stop == "readiness-timeout":
        ok = bool(attempts) and earlier_ok and last["status"] == "skipped"
    elif stop == "cleanup-failed":
        ok = bool(attempts) and earlier_ok and last["status"] == "failed" and "cleanupError" in last
    elif stop == "condition-probe-pending":
        ok = bool(attempts) and earlier_ok and last["status"] == "failed" and "cleanupError" not in last and (
            last["readiness"]["status"] == "probe-pending" or _pending_probe(last["conditions"]["after"])
            or _pending_monitor(last["conditions"]["during"]))
    elif stop == "repetition-failed":
        ok = bool(attempts) and earlier_ok and last["status"] == "failed" and "cleanupError" not in last \
            and last["error"]["phase"] in ("load", "inference") and last["readiness"]["status"] == "ready" \
            and not _pending_probe(last["conditions"]["after"]) and not _pending_monitor(last["conditions"]["during"])
    else:
        ok = False
    if not ok or (status == "failed") != (stop is not None):
        errors.append(reason("status.attempts", "/status",
                             f"Status {status!r} with stopReason {stop!r} disagrees with the attempts {kinds}"))
    unhashed = STOP_REASONS_UNHASHED + (("engine-unavailable", "gate-cleanup-failed") if accelerator else ())
    if (record["artifact"] is None) != (stop in unhashed):
        errors.append(reason("status.artifact", "/artifact",
                             "artifact is null exactly when the run was recorded before its model was hashed"))

    # Per-attempt evidence, recomputed under the published policy.
    loads, process_ids = [], set()
    baseline = None
    for index, attempt in enumerate(attempts):
        where, readiness, conditions = f"/coldRuns/{index}", attempt["readiness"], attempt["conditions"]
        cooldown = protocol["cooldownSec"] * 1000
        if attempt["cooldownMs"] != cooldown:
            errors.append(reason("attempts.cooldown", f"{where}/cooldownMs",
                                 "Every repetition, the first included, follows the configured cooldown"))
        if readiness["timeoutSec"] != protocol["readinessTimeoutSec"]:
            errors.append(reason("readiness.inconsistent", f"{where}/readiness/timeoutSec",
                                 "Readiness timeout disagrees with the protocol"))
        expected_readiness = {"skipped": ("timeout",), "complete": ("ready",), "failed": ("ready", "probe-pending")}
        if readiness["status"] not in expected_readiness[attempt["status"]]:
            errors.append(reason("readiness.inconsistent", f"{where}/readiness/status",
                                 f"A {attempt['status']} repetition cannot follow readiness {readiness['status']!r}"))
        # The readiness baseline is the first ready temperature of the invocation; within
        # one session it may only be set once and never change afterwards.
        if baseline is not None and readiness["baselineBatteryTempC"] != baseline:
            errors.append(reason("readiness.inconsistent", f"{where}/readiness/baselineBatteryTempC",
                                 "The readiness baseline changed within the session"))
        before = conditions["before"]
        if readiness["status"] == "probe-pending" and (not _pending_probe(before) or attempt["instance"] is not None):
            errors.append(reason("readiness.inconsistent", f"{where}/readiness/status",
                                 "A pending readiness probe needs a timed-out or busy snapshot and no model load"))
        if readiness["status"] == "timeout" and not readiness["reasons"]:
            errors.append(reason("readiness.inconsistent", f"{where}/readiness/reasons",
                                 "A readiness timeout must name why the device was not ready"))
        if attempt["status"] == "failed" and attempt["error"]["phase"] in ("load", "inference") and \
                attempt["instance"] is None:
            errors.append(reason("instance.inconsistent", f"{where}/instance",
                                 "A load or inference failure follows a model load and records its instance"))
        if readiness["status"] != "probe-pending":
            # The configuration may override the rise bound; records before the override use the default.
            recomputed = assess_readiness(before, readiness["baselineBatteryTempC"],
                                          protocol.get("maxBatteryTempRiseC"), wait_while_charging)
            if readiness["status"] == "ready" and recomputed:
                errors.append(reason("readiness.recomputed", f"{where}/readiness/status",
                                     f"Recorded ready, but the before snapshot is not ready: {recomputed}"))
            if readiness["status"] == "timeout" and recomputed != readiness["reasons"]:
                errors.append(reason("readiness.recomputed", f"{where}/readiness/reasons",
                                     f"Timeout reasons {readiness['reasons']} differ from the snapshot's {recomputed}"))
            if readiness["status"] == "ready" and readiness["headroomApplied"] != (before.get("thermalHeadroom") is not None):
                errors.append(reason("readiness.recomputed", f"{where}/readiness/headroomApplied",
                                     "headroomApplied must reflect whether headroom was measured"))
        if readiness["status"] == "ready":
            baseline = readiness["baselineBatteryTempC"] if readiness["baselineBatteryTempC"] is not None \
                else before.get("batteryTempC")
        expected = {"beforeAssessment": assess_conditions(before),
                    "afterAssessment": None if conditions["after"] is None else assess_conditions(conditions["after"]),
                    "duringAssessment": None if conditions["during"] is None or conditions["after"] is None
                    else assess_during(conditions["during"], before, conditions["after"])}
        for key, value in expected.items():
            if conditions[key] != value:
                errors.append(reason("conditions.recomputed", f"{where}/conditions/{key}",
                                     f"Recorded {conditions[key]} but the evidence gives {value}"))
        monitor = conditions["during"]
        if monitor is not None:
            errors += _check_monitor(monitor, f"{where}/conditions/during", charging_policy)
            if set(monitor["summary"]["chargingStates"]) != set(_empty_states(charging_policy)):
                errors.append(reason("conditions.monitor", f"{where}/conditions/during/summary/chargingStates",
                                     "Charging state counts do not match the record's charging policy"))
            summary = monitor["summary"]
            if len(monitor["samples"]) > summary["samples"] or summary["incompleteSamples"] > summary["samples"] or \
                    sum(summary["externalPower"].values()) != summary["samples"] or \
                    sum(summary["chargingStates"].values()) != summary["samples"]:
                errors.append(reason("conditions.monitor", f"{where}/conditions/during/summary",
                                     "Monitor summary disagrees with its sample counts"))
            persisted = [charging_state(sample, charging_policy) for sample in monitor["samples"]]
            if any(persisted.count(state) > summary["chargingStates"].get(state, 0) for state in set(persisted)):
                errors.append(reason("conditions.monitor", f"{where}/conditions/during/summary/chargingStates",
                                     "Persisted samples show charging states missing from the summary"))
            missed = {item for sample in monitor["samples"] for item in condition_failures(sample)} - set(summary["failures"])
            if missed:
                errors.append(reason("conditions.monitor", f"{where}/conditions/during/summary/failures",
                                     f"Persisted samples show failures missing from the summary: {sorted(missed)}"))
        charging = assess_charging(charging_counts(conditions, attempt["status"] != "skipped", charging_policy))
        if conditions["charging"] != charging:
            errors.append(reason("conditions.recomputed", f"{where}/conditions/charging",
                                 f"Recorded charging {conditions['charging']!r} but the evidence gives {charging!r}"))
        if attempt["status"] == "skipped" and attempt["reason"] != (
                "charging" if "charging" in readiness["reasons"] else "readiness-timeout"):
            errors.append(reason("readiness.inconsistent", f"{where}/reason",
                                 "A skip is 'charging' exactly when charging was a final not-ready reason"))
        if attempt["status"] == "skipped" and (monitor is not None or conditions["after"] is not None):
            errors.append(reason("conditions.inconsistent", f"{where}/conditions",
                                 "A skipped repetition has no run-time or after evidence"))
        if attempt["instance"] is not None:
            loads.append((index, attempt["instance"]["modelLoadsBeforeInProcess"]))
            process_ids.add(attempt["instance"]["processInstanceId"])
    if len(process_ids) > 1:
        errors.append(reason("instance.inconsistent", "/coldRuns",
                             "All repetitions of a session run in one process"))
    if any(later <= earlier for (_, earlier), (_, later) in zip(loads, loads[1:])):
        errors.append(reason("instance.inconsistent", "/coldRuns",
                             "Model loads within a process must increase between repetitions"))

    verdict = condition_verdict(record)
    if record["conditionVerdict"] != verdict:
        errors.append(reason("guards.recomputed", "/conditionVerdict",
                             f"Recorded verdict {record['conditionVerdict']} but the evidence gives {verdict}"))
    if record["guardsPassed"] is not (False if verdict["status"] == "fail" else None):
        errors.append(reason("guards.inconsistent", "/guardsPassed",
                             "guardsPassed must be false exactly when the condition verdict fails"))
    # accelerator-v1 drops the backend note only when every repetition's engine confirmed the backend.
    confirmed = accelerator and (record["observedBackend"] or {}).get("status") == "confirmed"
    expected_notes = [*([] if confirmed else [BACKEND_NOTE]),
                      *(verdict["reasons"] if verdict["status"] == "fail" else
                        [UNVERIFIED_NOTE] if verdict["status"] == "unverified" else [])]
    if stop in unmeasured_notes:
        expected_notes.append(unmeasured_notes[stop])
    if record["guardNotes"] != expected_notes:
        errors.append(reason("guards.inconsistent", "/guardNotes",
                             f"guardNotes must be exactly {expected_notes}"))

    order = record["runOrder"]
    if order is not None:
        ids = [run["id"] for run in config["runs"]]
        if sorted(order["order"]) != sorted(ids) or order["position"] >= len(order["order"]) or \
                order["order"][order["position"]] != record["configurationRunId"]:
            errors.append(reason("order.inconsistent", "/runOrder",
                                 "Run order must be a permutation of the configured runs with this run at position"))
        elif seeded_order(ids, order["seed"]) != order["order"]:
            errors.append(reason("order.recomputed", "/runOrder/order",
                                 "Recorded order is not the seeded shuffle of the configured runs"))
        if (order["seedSource"] == "configured") != ("orderSeed" in config) or \
                ("orderSeed" in config and config["orderSeed"] != order["seed"]):
            errors.append(reason("order.inconsistent", "/runOrder/seedSource",
                                 "seedSource/seed must match the configuration's orderSeed"))
    return errors


# --- cpu-reference-v3: build, configuration and execution identity ----------

# runtimeVersions keys each engine may report (runtime.ts); every other key must be null.
ENGINE_RUNTIMES = {"llama.cpp": ("llamaCpp", "llamaRn"), "litert-lm": ("litertLm", "litertNpuRuntimeQualcomm", "qairt"),
                   "genie-x": ("genie", "qairt"), "executorch": ("executorch", "reactNativeExecutorch")}


def runtime_versions_errors(record):
    """runtimeVersions names only the engine's own runtimes; the Qualcomm NPU runtimes only on NPU runs."""
    runtimes = record.get("runtimeVersions")
    if runtimes is None:
        return []
    errors = []
    allowed = ENGINE_RUNTIMES.get(record["engine"], ())
    stray = sorted(key for key, value in runtimes.items() if value is not None and key not in allowed)
    if stray:
        errors.append(reason("runtime.inconsistent", "/runtimeVersions",
                             f"{record['engine']} does not run on {stray}"))
    if record["engine"] in ("litert-lm", "genie-x") and record["backend"] != "npu" and (
            runtimes["litertNpuRuntimeQualcomm"] is not None or runtimes["qairt"] is not None):
        errors.append(reason("runtime.inconsistent", "/runtimeVersions",
                             "The Qualcomm NPU runtime and QAIRT take part only in NPU runs"))
    if record["engine"] == "genie-x" and record.get("engineVersion") != geniex_engine_version(record["backend"], runtimes):
        errors.append(reason("runtime.inconsistent", "/engineVersion",
                             "GENIE-X engineVersion disagrees with runtimeVersions"))
    settings = record.get("effectiveSettings") or {}
    distribution = settings.get("distribution")
    # The binding's own runtime key: llama.rn, or react-native-executorch.
    key = "reactNativeExecutorch" if settings.get("binding") == "react-native-executorch" else "llamaRn"
    if distribution is not None and runtimes.get(key) is not None and runtimes[key] != distribution["version"]:
        errors.append(reason("runtime.inconsistent", f"/runtimeVersions/{key}",
                             "The binding version disagrees with effectiveSettings.distribution"))
    if record["engine"] == "litert-lm" and record.get("engineVersion") != litert_engine_version(record["backend"], runtimes):
        errors.append(reason("runtime.inconsistent", "/engineVersion",
                             "LiteRT-LM engineVersion disagrees with runtimeVersions"))
    if record["engine"] == "executorch" and (runtimes.get("executorch") is None or
                                             runtimes.get("reactNativeExecutorch") is None):
        errors.append(reason("runtime.inconsistent", "/runtimeVersions",
                             "ExecuTorch records name the ExecuTorch and react-native-executorch versions"))
    if record["engine"] == "executorch" and record.get("engineVersion") != executorch_engine_version(runtimes):
        errors.append(reason("runtime.inconsistent", "/engineVersion",
                             "ExecuTorch engineVersion disagrees with runtimeVersions"))
    return errors


def litert_engine_version(backend, runtimes):
    """The engineVersion LiteRtAdapter derives from runtimeVersions (liteRtEngineVersion)."""
    litert = f"litertlm-android {runtimes['litertLm'] or 'unknown'}"
    if backend == "npu":
        return (f"LiteRT-LM {litert} + litert-npu-runtime-qualcomm "
                f"{runtimes['litertNpuRuntimeQualcomm'] or 'unknown'} (npu)")
    return f"LiteRT-LM {litert} ({backend})"


def executorch_prompt_prefix(chat_template):
    """executorch.ts executorchPromptPrefix: Gemma prompts start with a literal <bos> (llama.cpp adds BOS)."""
    return "<bos>" if chat_template.startswith("gemma") else ""


def executorch_engine_version(runtimes):
    """The engineVersion ExecutorchAdapter derives from runtimeVersions (executorch.ts executorchEngineVersion)."""
    return (f"ExecuTorch {runtimes.get('executorch') or 'unknown'} + react-native-executorch "
            f"{runtimes.get('reactNativeExecutorch') or 'unknown'} (xnnpack)")


def geniex_engine_version(backend, runtimes):
    """The engineVersion GenieXAdapter derives from runtimeVersions (genieXEngineVersion)."""
    geniex = f"geniex-android {runtimes['genie'] or 'unknown'}"
    if backend == "npu":
        return f"GENIE-X {geniex} + QAIRT {runtimes['qairt'] or 'unknown'} (npu)"
    return f"GENIE-X {geniex} ({backend})"


# config.ts LOAD_SETTINGS: optional run fields, the llama.rn parameter each sets, and the harness
# default (LlamaRnAdapter LLAMA_RN_DEFAULTS) when a run sets none.
LOAD_SETTINGS = (("useMmap", "use_mmap", True), ("useMlock", "use_mlock", False),
                 ("noExtraBufts", "no_extra_bufts", False), ("flashAttnType", "flash_attn_type", "auto"))


def _semantic_v3(record):
    """Identity evidence: configured model hash, effective settings and per-load engine reports.

    accelerator-v1 records carry the same evidence, except that LiteRT-LM runs have no
    effectiveSettings (null) and no executionReports (LiteRT-LM reports neither)."""
    errors = runtime_versions_errors(record)
    protocol, config, attempts = record["protocol"], record["configuration"], record["coldRuns"]
    runs = [run for run in config["runs"] if run["id"] == record["configurationRunId"]]
    expected = runs[0].get("modelSha256") if runs else None
    artifact, stop = record["artifact"], record["stopReason"]
    if artifact is not None:
        if artifact["expectedSha256"] != expected:
            errors.append(reason("artifact.expected", "/artifact/expectedSha256",
                                 "expectedSha256 must be the configured run's modelSha256 (or null if none)"))
        mismatch = expected is not None and artifact["sha256"] != expected
        if mismatch != (stop == "model-hash-mismatch"):
            errors.append(reason("artifact.expected", "/stopReason",
                                 "stopReason is model-hash-mismatch exactly when the hashed model differs from "
                                 "the configured modelSha256"))

    settings = record["effectiveSettings"]
    # llama.rn states its settings and reports every load; ExecuTorch states its settings only.
    llama = settings is not None and settings["binding"] == "llama.rn"
    if not llama:
        settings = None
    reporting = llama
    for key, actual, wanted in () if settings is None else (("init/n_ctx", settings["init"]["n_ctx"], protocol["nCtx"]),
                                ("init/n_threads", settings["init"]["n_threads"], protocol["nThreads"]),
                                ("completion/n_predict", settings["completion"]["n_predict"],
                                 protocol["maxDecodeTokens"])):
        if actual != wanted:
            errors.append(reason("settings.protocol-mismatch", f"/effectiveSettings/{key}",
                                 "Effective engine setting disagrees with the protocol"))
    for field, key, default in () if settings is None or not runs else LOAD_SETTINGS:
        # Builds that did not pass no_extra_bufts (binding default false) could not run a
        # configuration that sets noExtraBufts, so only those records may leave it out.
        if key == "no_extra_bufts" and key not in settings["init"] and field not in runs[0]:
            continue
        if settings["init"].get(key) != runs[0].get(field, default):
            errors.append(reason("settings.configuration-mismatch", f"/effectiveSettings/init/{key}",
                                 "Effective engine setting disagrees with the configured run"))
    distribution = None if settings is None else settings.get("distribution")
    if distribution is not None and not record["engineVersion"].startswith(f"llama.rn {distribution['version']}"):
        errors.append(reason("settings.distribution", "/effectiveSettings/distribution/version",
                             "The installed llama.rn version disagrees with engineVersion"))

    source = record["configurationSource"]
    if source["kind"] == "staged" and source["sha256"] is None:
        errors.append(reason("configuration.source", "/configurationSource/sha256",
                             "A staged configuration is always hashed as read"))
    build = record["build"]
    if build["status"] == "unavailable" and any(value is not None for key, value in build.items() if key != "status"):
        errors.append(reason("build.inconsistent", "/build", "An unavailable build reports no other field"))
    if build["status"] == "reported" and build["gitCommit"] is None and build["gitDirty"] is not None:
        errors.append(reason("build.inconsistent", "/build/gitDirty", "gitDirty requires a known commit"))
    if source["kind"] == "bundled" and source["sha256"] is not None and (
            build["status"] == "unavailable" or build["buildType"] == "debug"):
        # The bundled hash is a build constant, and a debug build's JS comes from Metro.
        errors.append(reason("configuration.source", "/configurationSource/sha256",
                             "A bundled configuration hash requires a reported release build"))
    suffix = build["versionName"].rpartition("-")[2] if build["versionName"] and "-" in build["versionName"] else None
    if suffix in ("reference", "experimental", "geniex") and build["flavor"] is not None and suffix != build["flavor"]:
        errors.append(reason("build.inconsistent", "/build/versionName",
                             "The versionName flavor suffix disagrees with the flavor"))

    # One report per successful model load, in load order, never for a load that did not happen.
    reports = record["executionReports"]
    indices = [item["runIndex"] for item in reports]
    # A load-phase failure has an instance (the load was attempted) but no loaded context.
    loaded = {index for index, attempt in enumerate(attempts) if attempt["instance"] is not None and not (
        attempt["status"] == "failed" and attempt["error"]["phase"] == "load")}
    succeeded = {index for index, attempt in enumerate(attempts) if attempt["status"] == "complete" or (
        attempt["status"] == "failed" and attempt["error"]["phase"] in ("inference", "release"))}
    if indices != sorted(set(indices)) or not set(indices) <= loaded or (reporting and not succeeded <= set(indices)):
        errors.append(reason("execution.inconsistent", "/executionReports",
                             "Each successful model load has exactly one report, in repetition order"))
    # A report of GPU devices (e.g. a reference run in the experimental flavor, where llama.rn
    # registers OpenCL even with n_gpu_layers 0) is kept as evidence, not rejected: it is what
    # the engine said, and execution itself stays unverified (observedBackend null).
    return errors


# --- accelerator-v1 -------------------------------------------------------------
# Mirrors xebenchapp/src/harness/config.ts (parseAcceleratorConfig), backend.ts and
# gate.ts. Backend evidence status, every probe verdict, the correctness status and
# the comparison status are recomputed from the recorded evidence.

# output-gate-v1 probe set (gate.ts PROBES), pinned by ID, kind, expected keyword, budget and the
# SHA-256 of the prompt text; maxOutputTokens None means the measured decode budget.
GATE_PROBES = (
    ("short-capital", "answer", "paris", 32, "06c008d3ea3ad613476854fc1073d46d7f3b20a38505bb9118021706cac2303a"),
    ("long-fact-last", "answer", "pelican", 32, "e38bd86ecaf2c3a69a2816fb4d5f78016eacaefcf0e017c3ced6e6fe78714845"),
    ("long-fact-first", "answer", "pelican", 32, "3cb9fa6e036defb76bc7e529893d2f5e936073d040360359701d6fb39cd6a970"),
    ("long-fact-middle", "answer", "417", 32, "c7a0fe2d5ad6a5a33f7f56a9705d8a038713b549c2c971dcacf97d14564f6d55"),
    ("measured-prompt", "agreement", None, None, "97b05f26c77fa84504cfa238dad74591d882e5e0a6264a47f05dd03605bdb4f8"),
)
# prompts.ts ACCELERATOR_PROMPT / ACCELERATOR_PROMPT_LABEL.
ACCELERATOR_PROMPT_LABEL = "std-chat-v1"
ACCELERATOR_PROMPT_SHA256 = "97b05f26c77fa84504cfa238dad74591d882e5e0a6264a47f05dd03605bdb4f8"
# prompts.ts STANDARD_PROMPT / STANDARD_PROMPT_LABEL: the CPU reference prompt.
STANDARD_PROMPT_LABEL = "std-pp512-v1"
STANDARD_PROMPT_SHA256 = "d4af02ae1c169637720a824ed879956385446ffd8823e287b30c8b11756c90f4"
# What an accelerator configuration's repetitions measure (configuration measuredPrompt; absent
# means std-chat-v1): label -> (SHA-256 of the recorded prompt, promptFormat).
MEASURED_PROMPTS = {ACCELERATOR_PROMPT_LABEL: (ACCELERATOR_PROMPT_SHA256, "user-turn-chat-template"),
                    STANDARD_PROMPT_LABEL: (STANDARD_PROMPT_SHA256, "raw-pretemplated")}


def measured_prompt(config):
    """The measured-prompt label an accelerator configuration selects."""
    return config.get("measuredPrompt", ACCELERATOR_PROMPT_LABEL)


def text_sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def probe_set_errors(probes, max_decode_tokens, prompt_digest, where):
    """Reasons when probes are not exactly the output-gate-v1 set (prompt_digest(probe) gives its SHA-256)."""
    shape = [(probe.get("id"), probe.get("kind"), probe.get("expected"), probe.get("maxOutputTokens"),
              prompt_digest(probe)) for probe in probes]
    expected = [(id_, kind, keyword, max_decode_tokens if budget is None else budget, digest)
                for id_, kind, keyword, budget, digest in GATE_PROBES]
    if probes and shape != expected:
        return [reason("correctness.probe-set", f"{where}/probes",
                       "Probes must be exactly the output-gate-v1 set (IDs, prompts, keywords, budgets, order)")]
    return []


NPU_DISPATCH_LIB = "libLiteRtDispatch_Qualcomm.so"
OPENCL_LIB = "libOpenCL.so"
QNN_HTP_LIB = "libQnnHtp.so"
_BACKEND_RANK = {"unknown": 0, "consistent": 1, "confirmed": 2, "mismatch": 3}
XNNPACK_BACKEND = "XnnpackBackend"
# ECMAScript String.prototype.trim() whitespace, so text comparisons match the app exactly.
_JS_SPACE = "\t\n\u000b\f\r              " \
            "    　﻿"


def js_trim(text):
    return text.strip(_JS_SPACE)


def answer_passed(output, expected):
    """gate.ts answerPassed: word-boundary keyword match after lowercasing and collapsing non [a-z0-9]."""
    return f" {expected} " in f" {re.sub(r'[^a-z0-9]+', ' ', output.lower()).strip(' ')} "


def common_prefix(a, b):
    """gate.ts commonPrefix: shared leading code points of the trimmed strings."""
    count = 0
    for x, y in zip(js_trim(a), js_trim(b)):
        if x != y:
            break
        count += 1
    return count


def assess_backend(engine, requested, evidence):
    """backend.ts assessBackend."""
    if engine == "llama.cpp":
        report = evidence["report"]
        if report is None or report["gpu"] is None:
            return "unknown"
        if requested == "cpu":
            # gpu means GPU devices are attached, not that layers run there (backend.ts).
            return "consistent" if report["gpu"] else "confirmed"
        if not report["gpu"]:
            return "mismatch"
        devices = report["devices"]
        if not devices:
            return "consistent"
        htp = sum(device.upper().startswith("HTP") for device in devices)
        if requested == "gpu":
            return "confirmed" if htp == 0 else "mismatch"
        return "confirmed" if htp == len(devices) else "mismatch"
    if engine == "litert-lm":
        # Only a runtime newly loaded by this engine creation counts.
        libraries = evidence["libraries"]
        after = libraries["after"] if libraries else None
        before = libraries["before"] if libraries else None
        if after is None or before is None:
            return "unknown"
        loaded = [name for name in after if name not in before]
        if requested == "npu":
            return "consistent" if NPU_DISPATCH_LIB in loaded else "unknown"
        if requested == "gpu":
            return "consistent" if OPENCL_LIB in loaded else "unknown"
        return "unknown" if NPU_DISPATCH_LIB in loaded or OPENCL_LIB in loaded else "consistent"
    if engine == "executorch":
        # Every delegate of the loaded program is XNNPACK (CPU only), registered, and no other
        # ExecuTorch backend library is mapped.
        program = evidence.get("program") or {}
        methods, registered = program.get("methods"), program.get("registeredBackends")
        if methods is None:
            return "unknown"
        delegates = [name for method in methods for name in method["delegates"]]
        if requested != "cpu" or any(name != XNNPACK_BACKEND for name in delegates):
            return "mismatch"
        forward = next((method for method in methods if method["name"] == "forward"), None)
        libraries = evidence["libraries"]
        after = libraries["after"] if libraries else None
        confirmed = forward is not None and XNNPACK_BACKEND in forward["delegates"] and \
            registered is not None and XNNPACK_BACKEND in registered and \
            after is not None and not any(foreign_executorch_library(name) for name in after)
        return "confirmed" if confirmed else "unknown"
    if engine == "genie-x":
        # The QAIRT plugin loads the QNN HTP backend at its first model load in the process.
        libraries = evidence["libraries"]
        after = libraries["after"] if libraries else None
        before = libraries["before"] if libraries else None
        if after is None or before is None:
            return "unknown"
        loaded = [name for name in after if name not in before]
        if requested == "npu":
            return "consistent" if QNN_HTP_LIB in loaded else "unknown"
        if requested == "cpu":
            return "unknown" if QNN_HTP_LIB in loaded else "consistent"
        return "unknown"
    return "unknown"


def foreign_executorch_library(name):
    """backend.ts isForeignExecutorchLibrary: an ExecuTorch backend library other than XNNPACK (Vulkan, QNN)."""
    return (re.fullmatch(r"lib.*_executorch_backend\.so", name) is not None and
            name != "libxnnpack_executorch_backend.so") or name.startswith("libQnn")


def summarize_backend(evidence):
    """backend.ts summarizeBackend."""
    if not evidence:
        return None
    statuses = [item["status"] for item in evidence]
    if "mismatch" in statuses:
        status = "mismatch"
    else:
        status = statuses[0]
        for item in statuses:
            if _BACKEND_RANK[item] < _BACKEND_RANK[status]:
                status = item
    sources = list(dict.fromkeys(item["source"] for item in evidence))
    return {"status": status, "source": sources[0] if len(sources) == 1 else "none", "repetitions": len(evidence)}


def correctness_status(role, error, probes, reference_status):
    """gate.ts correctnessStatus."""
    if not probes:
        return "unverified", ["gate-not-run"]
    answers = [probe for probe in probes if probe["kind"] == "answer"]
    # A wrong answer is decisive even when a later probe errored.
    if any(probe["passed"] is False and (role == "reference" or (probe["reference"] or {}).get("passed") is True)
           for probe in answers):
        return "fail", ["answer-mismatch"]
    if error is not None:
        return "unverified", ["gate-error"]
    if role == "reference":
        return ("fail", ["answer-mismatch"]) if any(probe["passed"] is not True for probe in answers) else ("pass", [])
    if reference_status is None:
        return "unverified", ["reference-unavailable"]
    if reference_status != "pass":
        return "unverified", ["reference-failed"]
    if any(probe["passed"] is not True or (probe["reference"] or {}).get("passed") is not True for probe in answers):
        return "fail", ["answer-mismatch"]
    return "pass", []


def comparison_status(purpose, correctness, backend_mismatch=False):
    """gate.ts comparisonStatus."""
    reasons = (["bug-repro"] if purpose == "bug-repro" else []) + (
        ["correctness-fail"] if correctness["status"] == "fail" else
        ["correctness-unverified"] if correctness["status"] == "unverified" else []) + (
        ["backend-mismatch"] if backend_mismatch else [])
    invalid = purpose == "bug-repro" or correctness["status"] == "fail" or backend_mismatch
    return {"status": "invalid" if invalid else "unverified" if correctness["status"] == "unverified" else "valid",
            "reasons": reasons}


# config.ts MODEL_EXTENSIONS.
MODEL_EXTENSIONS = {"llama.cpp": ".gguf", "litert-lm": ".litertlm", "executorch": ".pte"}
# config.ts FIXED_DECODE_ENGINES: the engines that can decode a fixed length past the end of turn.
FIXED_DECODE_ENGINES = ("litert-lm", "executorch", "llama.cpp")


def _configuration_accelerator(config):
    """config.ts parseAcceleratorConfig rules beyond the schema."""
    errors, runs = [], config["runs"]
    for index, run in enumerate(runs):
        where = f"/configuration/runs/{index}"
        llama, geniex = run["engine"] == "llama.cpp", run["engine"] == "genie-x"
        if geniex:
            # GENIE-X: a GGUF through its llama_cpp plugin on CPU, a QAIRT bundle directory on the NPU.
            bundle = "." not in run["modelFile"]
            if run["backend"] == "gpu" or (bundle if run["backend"] == "cpu" else not bundle):
                errors.append(reason("configuration.run-invalid", f"{where}/modelFile",
                                     "GENIE-X runs a GGUF on CPU and a QAIRT bundle directory on the NPU"))
        elif not run["modelFile"].endswith(MODEL_EXTENSIONS.get(run["engine"], "\0")):
            errors.append(reason("configuration.run-invalid", f"{where}/modelFile",
                                 "modelFile extension does not match the engine"))
        fixed = config.get("decodeMode") == "fixed"
        if ("eogTokenIds" in run) != (llama and fixed):
            # config.ts: llama.cpp runs list their end-of-generation ids exactly under decodeMode fixed.
            errors.append(reason("configuration.run-invalid", f"{where}/eogTokenIds",
                                 "llama.cpp runs list eogTokenIds exactly under decodeMode fixed; other runs never"))
        elif "eogTokenIds" in run and any(b <= a for a, b in zip(run["eogTokenIds"], run["eogTokenIds"][1:])):
            errors.append(reason("configuration.run-invalid", f"{where}/eogTokenIds",
                                 "eogTokenIds are distinct and ascending"))
        if run["engine"] != "executorch" and ("tokenizerFile" in run or "tokenizerSha256" in run):
            errors.append(reason("configuration.run-invalid", where,
                                 "tokenizerFile/tokenizerSha256 apply to ExecuTorch runs only"))
        if run["engine"] == "executorch":
            if "tokenizerFile" not in run or "tokenizerSha256" not in run or run["backend"] != "cpu" or \
                    run["nThreads"] is not None or \
                    run["nCtx"] is not None or "nGpuLayers" in run or "devices" in run or any(
                    field in run for field, _, _ in LOAD_SETTINGS):
                errors.append(reason("configuration.run-invalid", where,
                                     "ExecuTorch runs are CPU only, with a tokenizerFile and its tokenizerSha256, "
                                     "nThreads null, nCtx null and no llama.cpp settings"))
        elif geniex:
            cpu = run["backend"] == "cpu"
            if "nGpuLayers" in run or "devices" in run or any(field in run for field, _, _ in LOAD_SETTINGS) or (
                    (run["nThreads"] is None or run["nCtx"] is None) if cpu else
                    (run["nThreads"] is not None or run["nCtx"] is not None)):
                errors.append(reason("configuration.run-invalid", where,
                                     "GENIE-X runs take nThreads and nCtx on CPU, null on the NPU, and no "
                                     "nGpuLayers/devices or llama.cpp load settings"))
        elif llama:
            devices = run.get("devices")
            htp = sum(device.upper().startswith("HTP") for device in devices or [])
            layers = run.get("nGpuLayers")
            backend = run["backend"]
            consistent = layers is not None and (
                layers == 0 and devices is None if backend == "cpu" else
                layers > 0 and htp == 0 if backend == "gpu" else
                layers > 0 and htp > 0 and htp == len(devices))
            if run["nThreads"] is None or run["nCtx"] is None or not consistent:
                errors.append(reason("configuration.run-invalid", where,
                                     "llama.cpp runs need nThreads, nCtx and nGpuLayers/devices matching the backend"))
        elif "nGpuLayers" in run or "devices" in run or run["nCtx"] is not None or (
                run["backend"] != "cpu" and run["nThreads"] is not None) or any(
                field in run for field, _, _ in LOAD_SETTINGS):
            errors.append(reason("configuration.run-invalid", where,
                                 "LiteRT-LM runs take nCtx null, no nGpuLayers/devices or llama.cpp load settings, "
                                 "and nThreads only on CPU"))
        reference = next((item for item in runs if item["id"] == run["referenceRunId"]), None)
        if (run["referenceRunId"] is not None) if run["backend"] == "cpu" else (
                reference is None or reference["backend"] != "cpu" or reference["engine"] != run["engine"]):
            errors.append(reason("configuration.run-invalid", f"{where}/referenceRunId",
                                 "GPU/NPU runs name a CPU run of the same engine; CPU runs take null"))
        if config.get("decodeMode") == "fixed" and (run["engine"] not in FIXED_DECODE_ENGINES or (
                run["engine"] == "litert-lm" and run["backend"] == "cpu" and run["nThreads"] not in (None, 4))):
            # LiteRT-LM's fixed-length engine (LiteRtFixedDecode.kt, nativeCreateBenchmark) takes no thread
            # count; ExecuTorch decodes a fixed length natively (ignoreEos with maxNewTokens); llama.cpp with a
            # logit bias on every end-of-generation token (eogTokenIds).
            errors.append(reason("configuration.run-invalid", where,
                                 "decodeMode fixed applies to LiteRT-LM runs (nThreads 4 or null on CPU), "
                                 "ExecuTorch runs and llama.cpp runs"))
    return errors


def _correctness_accelerator(record, run, runs):
    errors, correctness = [], record["correctness"]
    protocol, stop = record["protocol"], record["stopReason"]
    probes, role, error = correctness["probes"], correctness["role"], correctness["error"]
    where = "/correctness"
    if role != ("reference" if record["backend"] == "cpu" else "candidate"):
        errors.append(reason("correctness.inconsistent", f"{where}/role",
                             "CPU runs are gate references; GPU/NPU runs are candidates"))
    identity = correctness["reference"]
    if role == "reference" and identity is not None:
        errors.append(reason("correctness.inconsistent", f"{where}/reference", "A reference has no reference"))
    if role == "candidate" and run is not None:
        target = next((item for item in runs if item["id"] == run["referenceRunId"]), None)
        if identity is None or target is None or identity["configurationRunId"] != target["id"] \
                or identity["modelFile"] != target["modelFile"]:
            errors.append(reason("correctness.inconsistent", f"{where}/reference",
                                 "The reference must be the configured CPU run and its model file"))
        elif identity["sameArtifact"] is not (None if identity["artifactSha256"] is None or record["artifact"] is None
                                              else identity["artifactSha256"] == record["artifact"]["sha256"]):
            errors.append(reason("correctness.inconsistent", f"{where}/reference/sameArtifact",
                                 "sameArtifact must compare the reference and session artifact hashes"))
    if not probes and stop not in ("configuration-aborted", "setup-failed", "engine-unavailable",
                                   "model-hash-mismatch", "tokenizer-hash-mismatch"):
        errors.append(reason("correctness.inconsistent", f"{where}/probes",
                             "Only a run recorded without measurement may lack a correctness gate"))
    errors += probe_set_errors(probes, protocol["maxDecodeTokens"], lambda probe: text_sha256(probe["prompt"]), where)
    for index, probe in enumerate(probes):
        at = f"{where}/probes/{index}"
        answer = probe["kind"] == "answer"
        if (probe["expected"] is None) == answer or \
                (not answer and probe["maxOutputTokens"] != protocol["maxDecodeTokens"]):
            errors.append(reason("correctness.inconsistent", at,
                                 "Answer probes need an expected keyword; agreement probes use the measured budget"))
        if error is None and probe["output"] is None:
            errors.append(reason("correctness.inconsistent", f"{at}/output",
                                 "A gate without error has an output for every probe"))
        output, ref = probe["output"], probe["reference"]
        reference_ran = role == "candidate" and identity is not None and identity["status"] is not None
        if (ref is not None) != reference_ran:
            errors.append(reason("correctness.inconsistent", f"{at}/reference",
                                 "Candidate probes carry the reference output exactly when the reference gate ran"))
        passed = None if output is None or not answer else answer_passed(output, probe["expected"])
        compared = output is not None and ref is not None and ref["output"] is not None
        expected = {"passed": passed,
                    "exactMatch": js_trim(output) == js_trim(ref["output"]) if compared else None,
                    "commonPrefixChars": common_prefix(output, ref["output"]) if compared else None}
        if ref is not None and (ref["output"] is not None or ref["passed"] is not None):
            expected_ref = answer_passed(ref["output"], probe["expected"]) \
                if ref["output"] is not None and answer else None
            if ref["passed"] is not expected_ref:
                errors.append(reason("correctness.recomputed", f"{at}/reference/passed",
                                     f"Recorded {ref['passed']!r} but the reference output gives {expected_ref!r}"))
        for key, value in expected.items():
            if probe[key] != value or type(probe[key]) is not type(value):
                errors.append(reason("correctness.recomputed", f"{at}/{key}",
                                     f"Recorded {probe[key]!r} but the outputs give {value!r}"))
    reference_status = identity["status"] if identity else None
    if role == "candidate" and identity is not None and reference_status == "pass" and probes and any(
            probe["kind"] == "answer" and (probe["reference"] or {}).get("passed") is not True for probe in probes):
        errors.append(reason("correctness.inconsistent", f"{where}/reference/status",
                             "A passing reference passed every answer probe"))
    status, reasons = correctness_status(role, error, probes, reference_status)
    if correctness["status"] != status or correctness["reasons"] != reasons:
        errors.append(reason("correctness.recomputed", f"{where}/status",
                             f"Recorded {correctness['status']!r} {correctness['reasons']} but the probes give "
                             f"{status!r} {reasons}"))
    mismatch = "mismatch" in ((record["observedBackend"] or {}).get("status"), (record["gateBackend"] or {}).get("status"))
    comparison = comparison_status(record["purpose"], {"status": status}, mismatch)
    if record["comparison"] != comparison:
        errors.append(reason("comparison.recomputed", "/comparison",
                             f"Recorded {record['comparison']} but correctness and purpose give {comparison}"))
    return errors


def backend_evidence_errors(engine, backend, observed, where):
    """Source matches the engine and the status follows from the recorded evidence."""
    if observed is None:
        return []
    errors = []
    source = "program-delegates" if observed.get("program") is not None else \
        "engine-report" if observed.get("report") is not None else \
        "process-libraries" if observed.get("libraries") is not None else "none"
    engine_source = {"llama.cpp": "engine-report", "litert-lm": "process-libraries",
                     "executorch": "program-delegates", "genie-x": "process-libraries"}[engine]
    if observed["source"] != source or source not in (engine_source, "none"):
        errors.append(reason("backend.inconsistent", f"{where}/source",
                             f"{engine} reports backend evidence as {engine_source}"))
    status = assess_backend(engine, backend, observed)
    if observed["status"] != status:
        errors.append(reason("backend.recomputed", f"{where}/status",
                             f"Recorded {observed['status']!r} but the evidence gives {status!r}"))
    return errors


def _executorch_identity(record, run):
    """ExecuTorch records: react-native-executorch settings, no execution reports, the tokenizer artifact."""
    errors, settings, protocol = [], record["effectiveSettings"], record["protocol"]
    if settings is None or settings["binding"] != "react-native-executorch" or record["executionReports"]:
        return [reason("settings.engine", "/effectiveSettings",
                       "ExecuTorch records state their react-native-executorch settings and have no executionReports")]
    generation = settings["generation"]
    if generation["maxNewTokens"] != protocol["maxDecodeTokens"] or \
            generation["ignoreEos"] != (protocol.get("decodeMode") == "fixed") or \
            generation["promptPrefix"] != executorch_prompt_prefix(protocol["chatTemplate"]):
        errors.append(reason("settings.protocol-mismatch", "/effectiveSettings/generation",
                             "maxNewTokens is maxDecodeTokens, ignoreEos holds exactly under decodeMode fixed, and "
                             "promptPrefix follows the chat template"))
    if not record["engineVersion"].endswith(f"react-native-executorch {settings['distribution']['version']} (xnnpack)"):
        errors.append(reason("settings.distribution", "/effectiveSettings/distribution/version",
                             "The installed react-native-executorch version disagrees with engineVersion"))
    if "tokenizerArtifact" not in record:
        return errors + [reason("artifact.expected", "/tokenizerArtifact", "ExecuTorch records carry tokenizerArtifact")]
    tokenizer, stop = record["tokenizerArtifact"], record["stopReason"]
    hashed = record["artifact"] is not None and stop != "model-hash-mismatch"
    if (tokenizer is not None) != hashed:
        errors.append(reason("artifact.expected", "/tokenizerArtifact",
                             "tokenizerArtifact is recorded exactly when the run's model was hashed and matched"))
    if tokenizer is not None and run is not None:
        expected = run.get("tokenizerSha256")
        if tokenizer["file"] != run.get("tokenizerFile") or tokenizer["expectedSha256"] != expected:
            errors.append(reason("artifact.expected", "/tokenizerArtifact",
                                 "tokenizerArtifact names the configured tokenizerFile and tokenizerSha256"))
        if (expected is not None and tokenizer["sha256"] != expected) != (stop == "tokenizer-hash-mismatch"):
            errors.append(reason("artifact.expected", "/stopReason",
                                 "stopReason is tokenizer-hash-mismatch exactly when the hashed tokenizer differs from "
                                 "the configured tokenizerSha256"))
    return errors


def _executorch_attempts(record):
    """Every ExecuTorch repetition's metrics follow from the runner's statistics (executorch.ts executorchMetrics)."""
    errors = []
    for index, attempt in enumerate(record["coldRuns"]):
        metrics = attempt if attempt["status"] == "complete" else attempt.get("metrics") \
            if attempt["status"] == "failed" else None
        if metrics is None:
            continue
        where, stats, internal = f"/coldRuns/{index}", metrics.get("executorchStats"), metrics.get("engineInternal")
        if stats is None or internal is None:
            errors.append(reason("execution.inconsistent", where,
                                 "ExecuTorch repetitions carry executorchStats and engineInternal"))
            continue
        prompt_ms = stats["firstTokenMs"] - stats["inferenceStartMs"]
        predicted_ms = stats["inferenceEndMs"] - stats["firstTokenMs"]
        chunk = stats["prefillChunkTokens"]
        expected = {"promptTokens": stats["numPromptTokens"], "decodeTokens": stats["emittedTokens"],
                    "prefillMethod": "engine-timings", "decodeMethod": "engine-timings"}
        consistent = all(metrics[key] == value for key, value in expected.items()) and \
            stats["emittedTokens"] == stats["numGeneratedTokens"] + 1 and prompt_ms > 0 and predicted_ms > 0 and \
            stats["prefillChunks"] == (None if chunk is None else -(-stats["numPromptTokens"] // chunk))
        if consistent:
            rates = {"prefillTps": stats["numPromptTokens"] * 1000 / prompt_ms,
                     "decodeTps": stats["numGeneratedTokens"] * 1000 / predicted_ms}
            consistent = all(math.isclose(metrics[key], value, rel_tol=1e-9) for key, value in rates.items()) and \
                math.isclose(internal["promptMs"], prompt_ms, rel_tol=1e-9) and \
                math.isclose(internal["predictedMs"], predicted_ms, rel_tol=1e-9) and \
                math.isclose(internal["promptPerSecond"], rates["prefillTps"], rel_tol=1e-9) and \
                math.isclose(internal["predictedPerSecond"], rates["decodeTps"], rel_tol=1e-9)
        if not consistent:
            errors.append(reason("execution.inconsistent", f"{where}/executorchStats",
                                 "The repetition's metrics do not follow from the ExecuTorch runner statistics"))
    return errors


def _llama_fixed_decode(record, run):
    """llama.cpp under decodeMode fixed (LlamaRnAdapter): the measured requests bias every configured
    end-of-generation token to -infinity ([id, false]) with llama.rn's prompt state cache off, and each
    complete repetition produced maxDecodeTokens tokens, whose decode rate llama.rn computes over the
    maxDecodeTokens - 1 decode steps after the first token (timings.predicted_n, predicted_ms)."""
    errors, settings, protocol = [], record["effectiveSettings"], record["protocol"]
    fixed = protocol.get("decodeMode") == "fixed"
    bias = settings["completion"].get("logit_bias")
    expected = [[token, False] for token in run.get("eogTokenIds", [])] if fixed else None
    # Exactly [int, false] pairs: in Python 0 == False and 1 == True, so equality alone would let
    # a numeric bias such as [106, 0] pass for [106, false].
    pairs = isinstance(bias, list) and all(
        isinstance(pair, list) and len(pair) == 2 and type(pair[0]) is int and pair[1] is False for pair in bias)
    if bias != expected or (bias is not None and not pairs):
        errors.append(reason("settings.protocol-mismatch", "/effectiveSettings/completion/logit_bias",
                             "logit_bias is [id, false] for every configured eogTokenIds entry exactly under "
                             "decodeMode fixed"))
    if fixed and settings["init"].get("state_cache_budget_mb") != 0:
        errors.append(reason("settings.protocol-mismatch", "/effectiveSettings/init/state_cache_budget_mb",
                             "Fixed-decode llama.cpp records run with llama.rn's prompt state cache off"))
    for index, attempt in enumerate(record["coldRuns"] if fixed else []):
        if attempt["status"] != "complete":
            continue
        internal, steps = attempt.get("engineInternal"), protocol["maxDecodeTokens"] - 1
        if internal is None or internal["predictedMs"] <= 0 or \
                not math.isclose(attempt["decodeTps"], steps * 1000 / internal["predictedMs"], rel_tol=1e-6) or \
                not math.isclose(internal["predictedPerSecond"], attempt["decodeTps"], rel_tol=1e-9):
            errors.append(reason("execution.inconsistent", f"/coldRuns/{index}/decodeTps",
                                 "decode tok/s is the maxDecodeTokens - 1 decode steps over predicted_ms"))
    return errors


def _no_executorch_fields(record):
    """Only ExecuTorch records carry a tokenizer artifact or runner statistics."""
    errors = []
    if "tokenizerArtifact" in record:
        errors.append(reason("artifact.expected", "/tokenizerArtifact", "Only ExecuTorch records carry tokenizerArtifact"))
    for index, attempt in enumerate(record["coldRuns"]):
        metrics = attempt if attempt["status"] == "complete" else (attempt.get("metrics") or {})
        if "executorchStats" in metrics:
            errors.append(reason("execution.inconsistent", f"/coldRuns/{index}/executorchStats",
                                 "Only ExecuTorch repetitions carry executorchStats"))
    return errors


def _semantic_accelerator(record, identity=True):
    config, protocol = record["configuration"], record["protocol"]
    errors = _configuration_accelerator(config)
    run = next((item for item in config["runs"] if item["id"] == record["configurationRunId"]), None)
    if run is not None:
        for key in ("backend", "purpose"):
            if run[key] != record[key]:
                errors.append(reason("configuration.run-mismatch", f"/{key}",
                                     f"Record {key} disagrees with the configured run"))
    # accelerator-v2: llama.cpp runs state every llama.rn parameter; LiteRT-LM runs have neither
    # settings nor reports. accelerator-v1 records predate both fields.
    settings = record["effectiveSettings"] if identity else None
    if not identity:
        pass
    elif record["engine"] in ("litert-lm", "genie-x"):
        if settings is not None or record["executionReports"]:
            errors.append(reason("settings.engine", "/effectiveSettings",
                                 "LiteRT-LM and GENIE-X records have no effectiveSettings and no executionReports"))
    elif record["engine"] == "executorch":
        errors += _executorch_identity(record, run)
    elif settings is None or settings["binding"] != "llama.rn":
        errors.append(reason("settings.engine", "/effectiveSettings", "llama.cpp records state their llama.rn settings"))
    elif run is not None and (settings["init"]["n_gpu_layers"] != run.get("nGpuLayers")
                              or settings["init"].get("devices") != run.get("devices")):
        errors.append(reason("settings.protocol-mismatch", "/effectiveSettings/init",
                             "n_gpu_layers/devices disagree with the configured run"))
    elif run is not None:
        errors += _llama_fixed_decode(record, run)
    label = measured_prompt(config)
    digest, prompt_format = MEASURED_PROMPTS.get(label, (None, None))
    if protocol["promptLabel"] != label or text_sha256(protocol["prompt"]) != digest or (
            protocol["promptFormat"] != prompt_format):
        errors.append(reason("protocol.prompt", "/protocol/prompt",
                             "The record measures another prompt than its configuration's measuredPrompt "
                             "(std-chat-v1 by default; prompts.ts)"))
    if protocol["chatTemplate"] != config["chatTemplate"]:
        errors.append(reason("protocol.configuration-mismatch", "/protocol/chatTemplate",
                             "Protocol chatTemplate disagrees with the configuration"))
    if protocol.get("decodeMode") != config.get("decodeMode"):
        errors.append(reason("protocol.configuration-mismatch", "/protocol/decodeMode",
                             "Protocol decodeMode disagrees with the configuration"))
    elif config.get("decodeMode") == "fixed":
        # Every measured repetition decoded exactly the configured length, past the end of turn.
        for index, attempt in enumerate(record["coldRuns"]):
            if attempt["status"] == "complete" and attempt["decodeTokens"] != protocol["maxDecodeTokens"]:
                errors.append(reason("protocol.decode-length", f"/coldRuns/{index}/decodeTokens",
                                     "decodeMode fixed: a repetition decoded another length than maxDecodeTokens"))
    errors += _executorch_attempts(record) if record["engine"] == "executorch" else _no_executorch_fields(record)
    errors += backend_evidence_errors(record["engine"], record["backend"], record["gateBackend"], "/gateBackend")
    evidence = []
    for index, attempt in enumerate(record["coldRuns"]):
        if attempt["status"] == "skipped":
            continue
        where, observed = f"/coldRuns/{index}/observedBackend", attempt["observedBackend"]
        loaded = attempt["instance"] is not None and not (
            attempt["status"] == "failed" and attempt["error"]["phase"] == "load")
        if attempt["status"] == "complete" and observed is None or observed is not None and not loaded:
            errors.append(reason("backend.inconsistent", where,
                                 "Backend evidence is recorded exactly for repetitions whose engine loaded"))
        if observed is None:
            continue
        evidence.append(observed)
        errors += backend_evidence_errors(record["engine"], record["backend"], observed, where)
        # accelerator-v2 llama.cpp: the evidence is derived from that load's execution report.
        report = observed.get("report")
        if identity and report is not None:
            loads = [item for item in record["executionReports"] if item["runIndex"] == attempt["runIndex"]]
            if len(loads) != 1 or any(report[key] != loads[0][source] for key, source in (
                    ("gpu", "gpu"), ("devices", "devices"), ("androidLib", "androidLib"),
                    ("reasonNoGpu", "reasonNoGPU"))):
                errors.append(reason("backend.inconsistent", f"{where}/report",
                                     "Backend evidence must restate the execution report of the same load"))
    summary = summarize_backend(evidence)
    if record["observedBackend"] != summary:
        errors.append(reason("backend.recomputed", "/observedBackend",
                             f"Recorded {record['observedBackend']} but the repetitions give {summary}"))
    return errors + _correctness_accelerator(record, run, config["runs"])


def validate(record):
    """Return a list of reasons; an empty list means the record is valid schemaVersion 1 evidence."""
    if not isinstance(record, dict):
        return [reason("json.not-object", "", "A session record must be a JSON object")]
    found = _non_finite(record, "")
    if found is not None:
        return [reason("json.non-finite", found, "Numbers must be finite")]
    if record.get("schema") != "xebench-raw":
        return [reason("record.schema", "/schema", "Expected schema xebench-raw")]
    if "schemaVersion" not in record:
        return [reason("version.missing", "/schemaVersion", "Missing schemaVersion")]
    version = record["schemaVersion"]
    if isinstance(version, bool) or version not in SUPPORTED_VERSIONS:
        return [reason("version.unsupported", "/schemaVersion",
                       f"Unsupported schemaVersion {json.dumps(version)}; supported: {list(SUPPORTED_VERSIONS)}")]
    errors = _Validator(schema()).validate(record, schema(), "")
    if not errors:
        try:
            errors = _semantic(record)
        except (TypeError, ValueError, KeyError, IndexError, AttributeError, OverflowError):
            # Schema-valid but semantically unusable input (e.g. a float where an index is
            # needed) is rejected with a reason, never raised to the caller.
            errors = [reason("record.unprocessable", "", "Record could not be checked against cross-field rules")]
    if len(errors) > MAX_REASONS:
        errors = errors[:MAX_REASONS] + [reason("reasons.truncated", "", f"{len(errors) - MAX_REASONS} more")]
    return errors


def validate_text(text):
    try:
        return validate(parse(text))
    except Invalid as error:
        return error.reasons


def _records(path):
    text = Path(path).read_text(encoding="utf-8")
    if str(path).endswith(".jsonl"):
        lines = [line for line in text.splitlines() if line.strip()]
        return [(str(index), line) for index, line in enumerate(lines)] or [("0", text)]
    try:
        value = parse(text)
    except Invalid:
        return [("0", text)]
    if isinstance(value, list):
        return [(str(index), json.dumps(item)) for index, item in enumerate(value)]
    if isinstance(value, dict) and isinstance(value.get("records"), dict) and "schema" not in value:
        return [(key, json.dumps(item)) for key, item in value["records"].items()]
    return [("0", text)]


def main(argv):
    if not argv:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    ok, count = True, 0
    for path in argv:
        for key, text in _records(path):
            reasons = validate_text(text)
            ok, count = ok and not reasons, count + 1
            print(json.dumps({"file": path, "record": key, "valid": not reasons, "reasons": reasons}))
    # An empty array or records mapping must not pass a CI check vacuously.
    if not count:
        print("No records found", file=sys.stderr)
    return 0 if ok and count else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
