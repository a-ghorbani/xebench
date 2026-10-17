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


def _check_keywords(node, where):
    """Fail loudly on schema features this interpreter would otherwise ignore."""
    if not isinstance(node, dict):
        raise ValueError(f"Schema must be an object at {where}")
    unknown = set(node) - _KEYWORDS
    if unknown:
        raise ValueError(f"Unsupported JSON Schema keyword(s) {sorted(unknown)} at {where}")
    if "$ref" in node and set(node) - _ANNOTATIONS - {"$ref"}:
        raise ValueError(f"Keywords beside $ref are not supported at {where}")
    if "type" in node:
        names = node["type"] if isinstance(node["type"], list) else [node["type"]]
        if not names or set(names) - _TYPES:
            raise ValueError(f"Unsupported type {node['type']!r} at {where}")
    for key in ("properties", "$defs"):
        for name, child in node.get(key, {}).items():
            _check_keywords(child, f"{where}/{key}/{name}")
    for key in ("items", "additionalProperties"):
        if isinstance(node.get(key), dict):
            _check_keywords(node[key], f"{where}/{key}")
    for index, child in enumerate(node.get("oneOf", [])):
        _check_keywords(child, f"{where}/oneOf/{index}")


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
        # Report the alternative selected by its status/type discriminator when
        # one exists, so reasons name the real defect instead of every branch.
        for option, result in zip(options, results):
            target = self.resolve(option["$ref"]) if "$ref" in option else option
            status = target.get("properties", {}).get("status", {})
            if "const" in status:
                if isinstance(value, dict) and value.get("status") == status["const"]:
                    return result
            elif isinstance(target.get("type"), str) and target["type"] != "null" and _is_type(value, target["type"]):
                return result
        return [reason("schema.one-of", path, "Matches no allowed alternative")]


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
    failed = [index for index, attempt in enumerate(attempts) if attempt["status"] == "failed"]
    if status == "complete" and (failed or len(attempts) != expected):
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

    # Apps built before condition snapshots (PR #11) omit all condition evidence;
    # a mix of present and missing evidence is never valid.
    observed = ["conditions" in attempt for attempt in attempts] + ["conditionPolicy" in record]
    if any(observed) and not all(observed):
        errors.append(reason("conditions.inconsistent", "/conditionPolicy",
                             "conditionPolicy and every attempt's conditions must be present together"))
        return errors
    if not any(observed) and record["guardsPassed"] is not None:
        errors.append(reason("guards.inconsistent", "/guardsPassed",
                             "Without condition evidence guardsPassed must stay null"))
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


def validate(record):
    """Return a list of reasons; an empty list means the record is valid v1 evidence."""
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
        errors = _semantic(record)
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
    ok = True
    for path in argv:
        for key, text in _records(path):
            reasons = validate_text(text)
            ok = ok and not reasons
            print(json.dumps({"file": path, "record": key, "valid": not reasons, "reasons": reasons}))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
