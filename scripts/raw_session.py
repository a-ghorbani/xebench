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


def _semantic(record):
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

    attempts, expected, status = record["coldRuns"], protocol["nColdRuns"], record["status"]
    for index, attempt in enumerate(attempts):
        if attempt["runIndex"] != index:
            errors.append(reason("attempts.run-index", f"/coldRuns/{index}/runIndex",
                                 "Repetitions must be numbered consecutively from 0"))
    if len(attempts) > expected:
        errors.append(reason("attempts.count", "/coldRuns",
                             f"{len(attempts)} attempts exceed the configured {expected}"))
    v2 = protocol["version"] == "cpu-reference-v2"
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

    complete = [attempt for attempt in attempts if attempt["status"] == "complete"]
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

    if v2:
        return errors + _semantic_v2(record)
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
STOP_REASONS_UNMEASURED = ("configuration-aborted", "setup-failed")
BACKEND_NOTE = "Actual backend execution has not been verified"
UNVERIFIED_NOTE = "Run conditions not fully verified"
UNMEASURED_NOTES = {"configuration-aborted": "Not run: an earlier run aborted the configuration",
                    "setup-failed": "Not run: the model artifact could not be read"}
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


def assess_readiness(snapshot, baseline, max_rise=None):
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
    if charging_state(snapshot) == "charging":
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
    if not _deep_equal(record["chargingPolicy"], charging_policy):
        errors.append(reason("conditions.policy", "/chargingPolicy",
                             f"chargingPolicy must equal the published {record['chargingPolicy']['version']} values"))
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
    elif stop in STOP_REASONS_UNMEASURED:
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
    if (record["artifact"] is None) != (stop in STOP_REASONS_UNMEASURED):
        errors.append(reason("status.artifact", "/artifact",
                             "artifact is null exactly when the run was recorded without measurement"))

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
                                          protocol.get("maxBatteryTempRiseC"))
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
    expected_notes = [BACKEND_NOTE, *(verdict["reasons"] if verdict["status"] == "fail" else
                                     [UNVERIFIED_NOTE] if verdict["status"] == "unverified" else [])]
    if stop in STOP_REASONS_UNMEASURED:
        expected_notes.append(UNMEASURED_NOTES[stop])
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
