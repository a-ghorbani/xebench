#!/usr/bin/env python3
"""Export sanitized, content-addressed evidence bundles from local captures, and verify them.

  export_evidence.py export --out DIR [--base-url https://...] CAPTURE_DIR...
  export_evidence.py verify BUNDLE_DIR [--sha256 HEX]

Captures written by capture_app_run.py are private local diagnostics. Export
re-validates every record with raw_session.py and refuses (exit 1, nothing
written) on invalid, tampered, legacy or unscreenable input. Only allowlisted
fields are copied; anything else is dropped and listed in the local report on
stdout, never in the bundle. Logcat, error logs, rejected payloads and their
reason files are never read; rejections contribute only counts and reason codes.

Bundle layout (DIR/<bundleSha256>/):
  manifest.json              lists every other file with its SHA-256 and size
  captures/<captureId>.json  capture status, counts, rejection codes, APK identity
  sessions/<runId>.json      one sanitized session: identity, configuration,
                             every repetition and the reported summary

Hashing rule: bundleSha256 is the SHA-256 of the exact bytes of manifest.json.
Each file is canonical JSON (see canonical()), so the same input always gives
the same bytes and hash; no export time, host or path is included. Verifiers
hash bytes as served and never re-serialize to check a hash.

verify checks the hash, every listed checksum and size, that no unlisted file
exists, canonical form, allowlist conformance (manifest included), the privacy
screen, file-name/ID binding, repetition numbering/count/status, capture counts,
and that every summary is recomputed from its successful repetitions.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import raw_session  # noqa: E402

BUNDLE_SCHEMA = "xebench-evidence-bundle"
SESSION_SCHEMA = "xebench-evidence-session"
CAPTURE_SCHEMA = "xebench-evidence-capture"
FORMAT_VERSION = 1
MAX_RAW_BYTES = 2 * 1024 * 1024
MAX_BUNDLE_FILE_BYTES = 4 * 1024 * 1024
HASH_RULE = ("bundleSha256 is the SHA-256 of the bytes of manifest.json; manifest.json lists every "
             "other file with its SHA-256 and byte size")
CANONICAL_RULE = ("JSON with sorted keys, separators ',' and ':', ASCII only (other characters as "
                  "u-escapes), no NaN or Infinity, and one trailing LF")
CAPTURE_STATUSES = ("complete", "partial", "timeout", "setup-failed", "startup-failed", "capture-failed",
                    "cleanup-failed", "interrupted", "app-changed")


def canonical(value):
    """The single serialization used for every bundle file (and so for the hash)."""
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
            + "\n").encode("ascii")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


class Refused(ValueError):
    """Input cannot be exported; messages name a location, never the offending value."""

    def __init__(self, where, message):
        super().__init__(f"{where or '/'}: {message}")
        self.where, self.message = where, message


class Context:
    def __init__(self):
        self.dropped = []
        self.source = ""

    def drop(self, path, kind):
        self.dropped.append({"source": self.source, "path": path, "kind": kind})


def _pointer(path, key):
    return f"{path}/{str(key).replace('~', '~0').replace('/', '~1')}"


# ── Allowlist specification ──────────────────────────────────────────────────
# Approved fields must match their form or the export is refused; fields that
# are not approved are dropped and reported. Free text is never approved: every
# string form below excludes '/', '\\', '@' and control characters.

class Scalar:
    def __init__(self, check, describe):
        self.check, self.describe = check, describe

    def apply(self, value, path, ctx):
        if not self.check(value):
            raise Refused(path, f"Approved field is not a {self.describe}")
        return value


def _text(pattern, describe):
    compiled = re.compile(pattern)
    return Scalar(lambda v: isinstance(v, str) and compiled.fullmatch(v) is not None, describe)


def _number(lo=-math.inf, hi=math.inf, integer=False):
    def check(v):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return False
        if isinstance(v, float) and not math.isfinite(v):
            return False
        if integer and not (isinstance(v, int) or v.is_integer()):
            return False
        return lo <= v <= hi
    return Scalar(check, "integer" if integer else "finite number")


class Nullable:
    def __init__(self, spec):
        self.spec = spec

    def apply(self, value, path, ctx):
        return None if value is None else self.spec.apply(value, path, ctx)


class Obj:
    def __init__(self, fields, required=(), excluded=(), derive=None):
        self.fields, self.required, self.excluded, self.derive = fields, tuple(required), set(excluded), derive

    def apply(self, value, path, ctx):
        if not isinstance(value, dict):
            raise Refused(path, "Approved field is not an object")
        if self.derive is not None:
            value = {**value, **self.derive(value)}
        for key in self.required:
            if key not in value:
                raise Refused(_pointer(path, key), "Missing approved field")
        out = {}
        for key, child in value.items():
            where = _pointer(path, key)
            if key in self.fields:
                out[key] = self.fields[key].apply(child, where, ctx)
            else:
                ctx.drop(where, "excluded" if key in self.excluded else "unknown-field")
        return out


class Arr:
    def __init__(self, item, max_items=10000):
        self.item, self.max_items = item, max_items

    def apply(self, value, path, ctx):
        if not isinstance(value, list) or len(value) > self.max_items:
            raise Refused(path, "Approved field is not a bounded array")
        return [self.item.apply(item, _pointer(path, index), ctx) for index, item in enumerate(value)]


UNRECOGNIZED = "unrecognized"


class Vocab:
    """A closed vocabulary (reason codes, signal names, guard notes).

    Values outside it are replaced by "unrecognized" and reported, so a new
    app reason keeps its count and position without letting free text through.
    """

    def __init__(self, values):
        self.values = frozenset(values) | {UNRECOGNIZED}

    def apply(self, value, path, ctx):
        if isinstance(value, str) and value in self.values:
            return value
        ctx.drop(path, "unrecognized-value")
        return UNRECOGNIZED


class VocabCounts:
    """Counters keyed by a vocabulary; unknown keys are summed under "unrecognized"."""

    def __init__(self, vocab, value):
        self.vocab, self.value = vocab, value

    def apply(self, value, path, ctx):
        if not isinstance(value, dict):
            raise Refused(path, "Approved field is not an object")
        out = {}
        for key, child in value.items():
            count = self.value.apply(child, _pointer(path, key), ctx)
            if key not in self.vocab.values:
                ctx.drop(_pointer(path, key), "unrecognized-key")
                key = UNRECOGNIZED
            out[key] = out.get(key, 0) + count
        return out


class Variant:
    def __init__(self, discriminator, options):
        self.discriminator, self.options = discriminator, options

    def apply(self, value, path, ctx):
        key = value.get(self.discriminator) if isinstance(value, dict) else None
        option = self.options.get(key) if isinstance(key, str) else None
        if option is None:
            raise Refused(_pointer(path, self.discriminator), "Unsupported variant")
        return option.apply(value, path, ctx)


# Enumerated values (statuses, methods, phases, modes) are lowercase words: no
# digits, so serials and hex identifiers are refused. Versions may hold digits.
ENUM_FORM = _text(r"[a-z][a-z-]{0,63}", "lowercase enumerated value")
VERSION = _text(r"[A-Za-z][A-Za-z0-9._-]{0,63}", "version identifier")
LABEL = _text(r"[A-Za-z0-9][A-Za-z0-9 ._+()-]{0,127}", "label")
PROSE = _text(r"[A-Za-z0-9][A-Za-z0-9 ,.;:()'_-]{0,199}", "short plain-text constant")
HEX64 = _text(r"[0-9a-f]{64}", "lowercase SHA-256")
RUN_ID = _text(r"[A-Za-z0-9][A-Za-z0-9-]{0,99}", "run id")
UUID = _text(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "UUID")
UTC = _text(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,9})?(Z|\+00:00)", "UTC timestamp")
MODEL_FILE = _text(r"[A-Za-z0-9][A-Za-z0-9._-]{0,122}\.gguf", "model file basename")
APK_NAME = _text(r"[A-Za-z0-9][A-Za-z0-9._-]{0,123}\.apk", "APK file name")
PACKAGE = _text(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+", "package name")
DEVICE_TEXT = _text(r"[A-Za-z0-9][A-Za-z0-9 ._+()-]{0,63}", "device property")
RELEASE = _text(r"[0-9][0-9A-Za-z.]{0,15}", "Android release")
INSTANCE_ID = _text(r"[a-z0-9]{1,16}-[a-z0-9]{1,16}", "process instance id")
# Capture rejection codes are generated by the host (raw_session.py, capture_app_run.py).
REJECTION_CODES = Vocab((
    "attempts.count", "attempts.run-index", "conditions.inconsistent", "configuration.duplicate-run-id",
    "configuration.run-mismatch", "configuration.unknown-run", "guards.inconsistent", "ingest.error",
    "json.duplicate-key", "json.non-finite", "json.not-object", "json.parse", "protocol.configuration-mismatch",
    "reasons.truncated", "record.schema", "schema.additional-property", "schema.const", "schema.enum",
    "schema.exclusive-minimum", "schema.maximum", "schema.max-items", "schema.max-length", "schema.minimum",
    "schema.min-items", "schema.min-length", "schema.one-of", "schema.pattern", "schema.required", "schema.type",
    "status.attempts", "summary.count", "summary.mismatch", "transport.checksum", "transport.encoding",
    "transport.reference", "transport.size", "version.missing", "version.unsupported",
    # cpu-reference-v2 cross-field checks (raw_session._semantic_v2).
    "attempts.cooldown", "conditions.monitor", "conditions.policy", "conditions.recomputed", "guards.recomputed",
    "instance.inconsistent", "order.inconsistent", "order.recomputed", "readiness.inconsistent",
    "readiness.recomputed", "record.unprocessable", "status.artifact",
    # cpu-reference-v3 identity checks (raw_session._semantic_v3).
    "artifact.expected", "build.inconsistent", "configuration.source", "execution.inconsistent",
    "settings.protocol-mismatch",
))

# Condition signals and reason codes emitted by the app (xebenchapp/src/harness),
# including the conditions-enforcement additions.
SIGNALS = ("timestampMs", "elapsedMs", "batteryPct", "batteryTempC", "charging", "batteryStatus", "plugged",
           "currentNowUa", "powerSaveMode", "thermalStatus",
           "thermalHeadroom", "screenOn", "foreground", "keyguardLocked", "focusLossCount", "processUptimeMs",
           "rssMb", "pssMb")
REASONS = ("low-battery", "severe-thermal-state", "power-saving", "screen-off", "not-foreground", "device-locked",
           "probe-incomplete", "probe-pending", "thermal-not-ready", "battery-too-hot", "battery-not-cooled",
           "thermal-headroom-low", "interrupted", "readiness-timeout", "condition-probe-pending", "charging",
           *(f"unknown-{signal}" for signal in SIGNALS))
GUARD_NOTES = ("Run conditions and actual backend execution have not been verified",
               "Actual backend execution has not been verified", "Run conditions not fully verified",
               *raw_session.UNMEASURED_NOTES.values(), *REASONS)
SIGNAL, REASON, NOTE = Vocab(SIGNALS), Vocab(REASONS), Vocab(GUARD_NOTES)

# Every lowercase word the app (xebenchapp/src/harness, main and the
# conditions-enforcement branch) or the capture host writes into an enumerated
# field. A lowercase word is still free text (a username, a hostname), so the
# form check alone is not enough: other words become "unrecognized" and are
# reported, like the vocabularies above.
ENUM_WORDS = (
    "after", "allowed", "android", "ane", "applied-when-measured", "battery-not-cooled", "battery-too-hot",
    "before", "busy", "callback-derived", "changed", "charging", "cheap-native-reads-no-pss", "cleanup-failed",
    "community", "complete", "diagnostic-only", "not-eligible", "on-battery", "condition-probe-pending", "conditions", "configuration-aborted", "configured", "cpu",
    "device-locked", "during", "endpoint-pss-and-sampled-rss-not-peak", "endpoints-not-continuous", "engine",
    "engine-timings", "error", "external-power-disconnected", "executorch", "fail", "failed", "first-request-on-instance", "focus-loss-count",
    "foreground", "fresh-instance", "fresh-instance-first-request", "generated", "genie-x", "gpu", "inference",
    "interrupted", "ios", "litert-lm", "load", "low-battery", "macos", "matched", "measured", "never-eligible",
    "not-foreground", "not-measured", "not-running", "npu", "pass", "power-saving", "probe-incomplete",
    "probe-pending", "publication-eligibility-only", "readiness-timeout", "ready", "record-and-flag", "record-only",
    "recorded", "release", "repetition-failed", "running", "satisfied", "screen-off", "setup-failed",
    "severe-thermal-state", "skipped", "snapshots-not-peak", "started", "stopped", "stuck", "tethered-not-charging",
    "thermal-headroom-low", "thermal-not-ready", "timeout", "total-time-derived", "ttft-derived", "unavailable",
    "unknown", "unverified", "vendor", "violated", "wait-then-skip", "wait-while-charging",
    # cpu-reference-v3 identity: model hash check, configuration source, build identity and settings.
    "model-hash-mismatch", "bundled", "staged", "reported", "reference", "experimental", "geniex", "debug",
    "binding-defaults", "auto", "on", "off",
)


class Enum(Vocab):
    """A lowercase enumerated value: refused if not word-shaped, "unrecognized" if not an app word."""

    def apply(self, value, path, ctx):
        ENUM_FORM.apply(value, path, ctx)
        return super().apply(value, path, ctx)


ENUM = Enum(ENUM_WORDS)
# protocol.coldDefinition is an app constant (session.ts), not operator text.
COLD_DEFINITION = Vocab(("fresh engine instance in warm app process; artifact hashing warms OS file cache",))

BOOL = Scalar(lambda v: isinstance(v, bool), "boolean")
NUM, NONNEG, INT, COUNT = _number(), _number(0), _number(integer=True), _number(0, integer=True)
N_NUM, N_NONNEG, N_COUNT, N_BOOL = Nullable(NUM), Nullable(NONNEG), Nullable(COUNT), Nullable(BOOL)
REASON_LIST, SIGNAL_LIST = Arr(REASON, max_items=256), Arr(SIGNAL, max_items=64)

SNAPSHOT = Obj({
    "probeStatus": ENUM, "timestampMs": N_NONNEG, "elapsedMs": N_NONNEG,
    "batteryPct": Nullable(_number(0, 100)), "batteryTempC": N_NUM, "charging": N_BOOL,
    # charging-policy-v2 raw BatteryManager values (status code, plug bitmask, current_now).
    "batteryStatus": Nullable(_number(1, 5, integer=True)), "plugged": Nullable(_number(0, 255, integer=True)),
    "currentNowUa": Nullable(_number(-1e9, 1e9, integer=True)),
    "powerSaveMode": N_BOOL, "thermalStatus": Nullable(_number(0, 6, integer=True)),
    "thermalHeadroom": N_NONNEG, "screenOn": N_BOOL, "foreground": N_BOOL, "keyguardLocked": N_BOOL,
    "focusLossCount": N_COUNT, "processUptimeMs": N_NONNEG, "rssMb": N_NONNEG, "pssMb": N_NONNEG,
    "unavailable": SIGNAL_LIST,
})
ASSESSMENT = Obj({"status": ENUM, "reasons": REASON_LIST}, required=("status",))
MONITOR = Obj({
    "status": ENUM, "intervalMs": N_NONNEG, "startedElapsedMs": N_NONNEG, "stoppedElapsedMs": N_NONNEG,
    "ticks": N_COUNT, "dropped": N_COUNT, "expired": N_BOOL, "maxSampleCostMs": N_NONNEG,
    "totalSampleCostMs": N_NONNEG,
    "summary": Obj({
        "samples": COUNT, "incompleteSamples": COUNT, "failures": REASON_LIST, "maxThermalStatus": N_NUM,
        "maxThermalHeadroom": N_NUM, "maxBatteryTempC": N_NUM, "minBatteryPct": N_NUM, "maxRssMb": N_NUM,
        "maxGapMs": N_NONNEG,
        "externalPower": Obj({"connected": COUNT, "disconnected": COUNT, "unknown": COUNT}),
        "chargingStates": Obj({"notCharging": COUNT, "charging": COUNT, "onBattery": COUNT, "unknown": COUNT}),
    }),
    "samples": Arr(SNAPSHOT, max_items=1000),
})
CONDITIONS = Obj({
    "before": Nullable(SNAPSHOT), "after": Nullable(SNAPSHOT),
    "beforeAssessment": Nullable(ASSESSMENT), "afterAssessment": Nullable(ASSESSMENT),
    "during": Nullable(MONITOR), "duringAssessment": Nullable(ASSESSMENT), "charging": ENUM,
})
READINESS = Obj({
    "status": ENUM, "waitedMs": NONNEG, "polls": COUNT, "timeoutSec": NONNEG,
    "baselineBatteryTempC": N_NUM, "headroomApplied": BOOL,
    "notReady": VocabCounts(REASON, COUNT), "reasons": REASON_LIST,
})
INSTANCE = Obj({"kind": ENUM, "request": ENUM, "processInstanceId": INSTANCE_ID,
                "modelLoadsBeforeInProcess": COUNT})
ENGINE_INTERNAL = Obj({"promptMs": NONNEG, "promptPerSecond": NONNEG, "predictedMs": NONNEG,
                       "predictedPerSecond": NONNEG})
METRIC_FIELDS = {
    "loadMs": NONNEG, "ttftMs": NONNEG, "promptTokens": COUNT, "prefillTps": NONNEG, "prefillMethod": ENUM,
    "decodeTokens": COUNT, "decodeTps": NONNEG, "decodeMethod": ENUM, "engineInternal": ENGINE_INTERNAL,
}
ATTEMPT_BASE = {"runIndex": COUNT, "status": ENUM, "conditions": CONDITIONS, "cooldownMs": NONNEG,
                "readiness": READINESS, "instance": Nullable(INSTANCE)}
# Error and cleanup messages are free engine/log text: only the phase is exported.
ERROR = Obj({"phase": ENUM}, required=("phase",), excluded=("message",))
ATTEMPT = Variant("status", {
    "complete": Obj({**ATTEMPT_BASE, **METRIC_FIELDS}, required=("runIndex", "status") + raw_session.METRICS),
    "failed": Obj({**ATTEMPT_BASE, "error": ERROR, "metrics": Nullable(Obj(METRIC_FIELDS)),
                   "cleanupError": ERROR}, required=("runIndex", "status", "error")),
    "skipped": Obj({**ATTEMPT_BASE, "reason": ENUM}, required=("runIndex", "status")),
})
AGGREGATE = Obj({"median": NONNEG, "iqr": NONNEG, "min": NONNEG, "max": NONNEG, "n": _number(1, integer=True)},
                required=("median", "iqr", "min", "max", "n"))
SUMMARY = Obj({metric: Nullable(AGGREGATE) for metric in raw_session.METRICS}, required=raw_session.METRICS)
CONDITION_POLICY = Obj({
    "version": VERSION, "mode": ENUM, "minBatteryPct": NUM, "maxThermalStatus": NUM,
    "charging": ENUM, "coverage": ENUM, "memory": ENUM, "required": SIGNAL_LIST,
    "readiness": Obj({"mode": ENUM, "pollSec": NONNEG, "maxThermalStatus": NUM, "maxBatteryTempC": NUM,
                      "maxBatteryTempRiseC": NUM, "maxThermalHeadroom": NUM, "headroom": ENUM,
                      "headroomConfirmMs": NONNEG}),
    "run": Obj({"mode": ENUM, "minBatteryPct": NUM, "maxThermalStatus": NUM, "interruptions": ENUM,
                "sampling": Obj({"intervalMs": NONNEG, "maxDurationMs": NONNEG, "persistedSamples": COUNT,
                                 "maxGapFactor": NONNEG, "signals": ENUM})}),
})
CHARGING_POLICY = Obj({
    "version": VERSION, "publicationRequires": ENUM, "effect": ENUM,
    # charging-policy-v2 (tethered, not charging).
    "name": ENUM, "externalPower": ENUM, "notChargingStatuses": Arr(_number(1, 5, integer=True), max_items=8),
    "chargingStatus": _number(1, 5, integer=True), "dischargingStatus": _number(1, 5, integer=True),
    "onBattery": ENUM, "currentNow": ENUM, "readiness": ENUM, "unknown": ENUM,
})
CONDITION_VERDICT = Obj({"status": ENUM, "reasons": REASON_LIST, "charging": ENUM})
# ── cpu-reference-v3 identity ──────────────────────────────────────────────
HEX40 = _text(r"[0-9a-f]{40}", "lowercase commit hash")
# build.gradle versionName plus the flavor suffix; anything else (an operator's own text) is refused.
VERSION_NAME = _text(r"[0-9]{1,4}(\.[0-9]{1,4}){0,3}(-(reference|experimental|geniex))?", "app version name")
# Certificate of the checked-in android/app/debug.keystore. Other signing keys are reported only as
# "other": a private key's fingerprint would identify its owner across bundles.
REPOSITORY_DEBUG_CERT_SHA256 = "fac61745dc0903786fb9ede62a962b399f7348f0bb6f899b8332667591033b9c"
SIGNING_KEY = Vocab(("repository-debug-key", "other"))


def _signing_key(build):
    if "signingKey" in build:
        return {}
    certs = build.get("signingCertSha256")
    if not isinstance(certs, list):
        return {"signingKey": None}
    return {"signingKey": "repository-debug-key" if certs == [REPOSITORY_DEBUG_CERT_SHA256] else "other"}


BUILD = Obj({
    "status": ENUM, "gitCommit": Nullable(HEX40), "gitDirty": N_BOOL, "flavor": Nullable(ENUM),
    "buildType": Nullable(ENUM), "versionName": Nullable(VERSION_NAME), "versionCode": N_COUNT,
    "signingKey": Nullable(SIGNING_KEY),
}, required=("status", "gitCommit", "gitDirty", "flavor", "buildType", "versionName", "versionCode", "signingKey"),
    excluded=("signingCertSha256",), derive=_signing_key)
CONFIGURATION_SOURCE = Obj({"kind": ENUM, "sha256": Nullable(HEX64)}, required=("kind", "sha256"))
CACHE_TYPE = Vocab(("f16", "f32", "bf16", "q8_0", "q4_0", "q4_1", "iq4_nl", "q5_0", "q5_1"))
EFFECTIVE_SETTINGS = Obj({
    "binding": Vocab(("llama.rn",)), "unlisted": ENUM,
    "init": Obj({"n_ctx": COUNT, "n_batch": COUNT, "n_ubatch": COUNT, "n_threads": N_COUNT, "n_gpu_layers": INT,
                 "use_mmap": BOOL, "use_mlock": BOOL, "flash_attn_type": ENUM, "cache_type_k": CACHE_TYPE,
                 "cache_type_v": CACHE_TYPE}, required=("n_ctx", "n_batch", "n_threads", "n_gpu_layers")),
    "completion": Obj({"n_predict": COUNT, "temperature": NONNEG, "seed": COUNT},
                      required=("n_predict", "temperature", "seed")),
}, required=("binding", "init", "completion", "unlisted"))
# Exact llama.rn 0.12 (RNLlamaJSI.cpp) strings; other text becomes "unrecognized".
REASON_NO_GPU = Vocab(("", "GPU devices disabled by user", "GPU backend is available but was not selected",
                       "GPU backend is not available"))
ENGINE_DEVICE = Vocab(("CPU", "GPUOpenCL", "HTP0", "HTP1", "HTP2", "HTP3", "Metal", "BLAS"))
ANDROID_LIB = Vocab(("rnllama", "rnllama_jni", "rnllama_jni_v8", "rnllama_jni_v8_2", "rnllama_jni_v8_2_dotprod",
                     "rnllama_jni_v8_2_i8mm", "rnllama_jni_v8_2_dotprod_i8mm",
                     "rnllama_jni_v8_2_dotprod_i8mm_hexagon_opencl", "rnllama_jni_x86_64"))
# ggml backend names and the feature names ggml-cpu reports (ggml_backend_cpu_get_features).
ENGINE_BACKEND = Vocab(("CPU", "OpenCL", "Hexagon", "HTP", "Metal", "BLAS"))
ENGINE_FEATURES = frozenset((
    "SSE3", "SSSE3", "AVX", "AVX_VNNI", "AVX2", "F16C", "FMA", "BMI2", "AVX512", "AVX512_VBMI", "AVX512_VNNI",
    "AVX512_BF16", "AMX_INT8", "NEON", "ARM_FMA", "FP16_VA", "MATMUL_INT8", "SVE", "DOTPROD", "SVE_CNT", "SME",
    "RISCV_V", "RVV_VLEN", "VSX", "VXE", "WASM_SIMD", "LLAMAFILE", "ACCELERATE", "CPU_HBM", "OPENMP", "KLEIDIAI",
    "REPACK"))
_SYSTEM_INFO = re.compile(r"system_info: n_threads = ([0-9]{1,4})(?: \(n_threads_batch = (-?[0-9]{1,4})\))?"
                          r" / ([0-9]{1,4}) \| (.*)")
_SYSTEM_BACKEND = re.compile(r"([A-Za-z][A-Za-z0-9_]{0,31}) : ")
_SYSTEM_FEATURE = re.compile(r"([A-Z][A-Z0-9_]{0,31}) = ([0-9]{1,6}) \| ")


def parse_system_info(text):
    """llama.cpp's system_info line as threads plus per-backend features; None if it is not that line."""
    match = _SYSTEM_INFO.fullmatch(text)
    if not match:
        return None
    threads, batch, concurrency, rest = match.groups()
    backends, position = [], 0
    while position < len(rest):
        name = _SYSTEM_BACKEND.match(rest, position)
        if not name:
            return None
        position, features = name.end(), {}
        while position < len(rest):
            feature = _SYSTEM_FEATURE.match(rest, position)
            if not feature:
                break
            features[feature.group(1)] = int(feature.group(2))
            position = feature.end()
        backends.append({"name": name.group(1), "features": features})
    return {"nThreads": int(threads), "nThreadsBatch": None if batch is None else int(batch),
            "hardwareConcurrency": int(concurrency), "backends": backends}


class FeatureFlags:
    """Feature name -> integer value; names outside ggml's list are counted under "unrecognized"."""

    def apply(self, value, path, ctx):
        if not isinstance(value, dict) or len(value) > 64:
            raise Refused(path, "Approved field is not a bounded object")
        out = {}
        for key, child in value.items():
            number = _number(0, 999999, integer=True).apply(child, _pointer(path, key), ctx)
            if key in ENGINE_FEATURES:
                out[key] = number
                continue
            if key != UNRECOGNIZED:
                ctx.drop(_pointer(path, key), "unrecognized-key")
                number = 1
            out[UNRECOGNIZED] = out.get(UNRECOGNIZED, 0) + number
        return out


SYSTEM_INFO = Obj({"nThreads": COUNT, "nThreadsBatch": Nullable(INT), "hardwareConcurrency": COUNT,
                   "backends": Arr(Obj({"name": ENGINE_BACKEND, "features": FeatureFlags()},
                                       required=("name", "features")), max_items=16)},
                  required=("nThreads", "nThreadsBatch", "hardwareConcurrency", "backends"))


class SystemInfo:
    """The raw line is free text that could carry anything; only its parsed, closed form is exported."""

    def apply(self, value, path, ctx):
        if isinstance(value, str):
            parsed = parse_system_info(value)
            if parsed is None:
                ctx.drop(path, "unparsed-value")
                return None
            value = parsed
        return SYSTEM_INFO.apply(value, path, ctx)


EXECUTION_REPORT = Obj({
    "runIndex": COUNT, "gpu": N_BOOL, "reasonNoGPU": Nullable(REASON_NO_GPU),
    "devices": Nullable(Arr(ENGINE_DEVICE, max_items=16)), "androidLib": Nullable(ANDROID_LIB),
    "systemInfo": Nullable(SystemInfo()),
}, required=("runIndex", "gpu", "reasonNoGPU", "devices", "androidLib", "systemInfo"))

RUN_ORDER = Obj({"method": VERSION, "seed": _number(0, 2 ** 32 - 1, integer=True), "seedSource": ENUM,
                 "order": Arr(LABEL, max_items=64), "position": COUNT})


def _prompt_digest(protocol):
    # The prompt is an app constant identified by promptLabel; its text is not
    # exported, but its digest lets a reader confirm the exact workload.
    prompt = protocol.get("prompt")
    return {"promptSha256": sha256(prompt.encode("utf-8"))} if isinstance(prompt, str) else {}


PROTOCOL = Obj({
    "version": VERSION, "nColdRuns": COUNT, "cooldownSec": NONNEG, "readinessTimeoutSec": NONNEG,
    "maxBatteryTempRiseC": NONNEG, "promptLabel": LABEL, "promptSha256": HEX64, "maxDecodeTokens": COUNT, "nThreads": COUNT, "nCtx": COUNT,
    "coldDefinition": COLD_DEFINITION, "measurementKind": ENUM, "warmRequests": ENUM,
    "processRestartBetweenRepetitions": BOOL,
}, required=("version", "nColdRuns", "promptLabel", "promptSha256", "maxDecodeTokens", "nThreads", "nCtx"),
    excluded=("prompt",), derive=_prompt_digest)
CONFIGURATION = Obj({
    "schemaVersion": INT, "id": LABEL, "nColdRuns": COUNT, "cooldownSec": NONNEG,
    "readinessTimeoutSec": NONNEG, "maxBatteryTempRiseC": NONNEG, "maxDecodeTokens": COUNT,
    "orderSeed": _number(0, 2 ** 32 - 1, integer=True),
    "runs": Arr(Obj({"id": LABEL, "engine": LABEL, "model": LABEL, "modelFile": MODEL_FILE, "quant": LABEL,
                     "nThreads": COUNT, "nCtx": COUNT, "modelSha256": HEX64}, required=("id",)), max_items=64),
}, required=("id", "runs"))
RECORD = Obj({
    "runId": RUN_ID, "timestampIso": UTC, "schema": VERSION, "schemaVersion": INT,
    "artifact": Nullable(Obj({"sha256": HEX64, "sizeBytes": COUNT, "expectedSha256": Nullable(HEX64)},
                             required=("sha256", "sizeBytes"))),
    "harnessVersion": LABEL, "engine": LABEL, "engineVersion": LABEL, "backend": ENUM,
    "observedBackend": Nullable(ENUM), "platform": ENUM, "model": LABEL, "quant": LABEL,
    "modelFile": MODEL_FILE, "configuration": CONFIGURATION, "configurationRunId": LABEL, "protocol": PROTOCOL,
    "status": ENUM, "coldRuns": Arr(ATTEMPT, max_items=64), "summary": SUMMARY,
    "guardsPassed": Scalar(lambda v: v is None or isinstance(v, bool), "boolean or null"),
    "guardNotes": Arr(NOTE, max_items=256),
    "conditionPolicy": CONDITION_POLICY,
    # Fields added by the conditions-enforcement work; optional so either record generation exports.
    "runOrder": Nullable(RUN_ORDER), "repetitionsPlanned": COUNT, "stopReason": Nullable(ENUM),
    "chargingPolicy": CHARGING_POLICY, "conditionVerdict": CONDITION_VERDICT,
    # cpu-reference-v3 build, configuration and execution identity.
    "build": BUILD, "configurationSource": CONFIGURATION_SOURCE, "effectiveSettings": EFFECTIVE_SETTINGS,
    "executionReports": Arr(EXECUTION_REPORT, max_items=64),
}, required=("runId", "timestampIso", "schema", "schemaVersion", "artifact", "engine", "model", "quant",
             "modelFile", "configuration", "configurationRunId", "protocol", "status", "coldRuns", "summary"))
DEVICE = Obj({"manufacturer": Nullable(DEVICE_TEXT), "model": Nullable(DEVICE_TEXT), "soc": Nullable(DEVICE_TEXT),
              "androidRelease": Nullable(RELEASE)}, required=("manufacturer", "model", "soc", "androidRelease"))
APK_SNAPSHOT = Obj({"status": ENUM, "apks": Arr(Obj({"name": APK_NAME, "sha256": HEX64},
                                                    required=("name", "sha256")), max_items=32)},
                   required=("status",))
APP_IDENTITY = Obj({"packageName": PACKAGE, "consistency": ENUM, "before": Nullable(APK_SNAPSHOT),
                    "after": Nullable(APK_SNAPSHOT)}, required=("packageName", "consistency"))
SESSION_FILE = Obj({
    "schema": Scalar(lambda v: v == SESSION_SCHEMA, SESSION_SCHEMA), "schemaVersion": _number(1, 1, True),
    "sessionId": RUN_ID, "captureId": UUID, "rawSha256": HEX64,
    "validation": Scalar(lambda v: v == raw_session.VALIDATION, raw_session.VALIDATION),
    "device": DEVICE, "record": RECORD,
}, required=("schema", "schemaVersion", "sessionId", "captureId", "rawSha256", "validation", "device", "record"))


class RejectionCodes:
    def apply(self, value, path, ctx):
        if not isinstance(value, dict) or not all(
                key in REJECTION_CODES.values and COUNT.check(count) for key, count in value.items()):
            raise Refused(path, "Approved field is not a map of rejection codes to counts")
        return value


CAPTURE_FILE = Obj({
    "schema": Scalar(lambda v: v == CAPTURE_SCHEMA, CAPTURE_SCHEMA), "schemaVersion": _number(1, 1, True),
    "captureId": UUID, "startedAt": UTC, "finishedAt": UTC,
    "status": Scalar(lambda v: v in CAPTURE_STATUSES, "capture status"), "doneObserved": BOOL,
    "engineErrors": COUNT, "invalidRecords": COUNT, "failedRecords": COUNT, "checkpointCount": COUNT,
    "sessionIds": Arr(RUN_ID, max_items=1000),
    "rejections": Obj({"count": COUNT, "codes": RejectionCodes()}, required=("count", "codes")),
    "appIdentity": APP_IDENTITY,
    "device": Obj({"manufacturer": Nullable(DEVICE_TEXT), "model": Nullable(DEVICE_TEXT),
                   "soc": Nullable(DEVICE_TEXT)}, required=("manufacturer", "model", "soc")),
}, required=("schema", "schemaVersion", "captureId", "startedAt", "finishedAt", "status", "doneObserved",
             "engineErrors", "invalidRecords", "failedRecords", "checkpointCount", "sessionIds",
             "rejections", "appIdentity", "device"))
STATISTICS_RULE = "median and IQR use linear interpolation at index (n - 1) * p over successful repetitions"


def _constant(value):
    return Scalar(lambda v: type(v) is type(value) and v == value, f"constant {value!r}")


MANIFEST_FILE = Obj({
    "schema": _constant(BUNDLE_SCHEMA), "schemaVersion": _constant(FORMAT_VERSION),
    "hashRule": _constant(HASH_RULE), "canonicalJson": _constant(CANONICAL_RULE),
    "statistics": _constant(STATISTICS_RULE),
    "sessionIds": Arr(RUN_ID, max_items=100000), "captureIds": Arr(UUID, max_items=100000),
    "files": Arr(Obj({"path": _text(r"(captures|sessions)/[A-Za-z0-9][A-Za-z0-9-]{0,99}\.json", "bundle path"),
                      "sha256": HEX64, "bytes": COUNT}, required=("path", "sha256", "bytes")), max_items=200000),
}, required=("schema", "schemaVersion", "hashRule", "canonicalJson", "statistics", "sessionIds", "captureIds",
             "files"))


# ── Privacy screen ───────────────────────────────────────────────────────────
# Defense in depth over every key and string in the bundle, after the allowlist.

PRIVATE_PATTERNS = (
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+")),
    ("path", re.compile(r"(/(data|sdcard|storage|mnt|home|Users|root|tmp|var|private|system|vendor|proc)(/|$))"
                        r"|~/|\\|[A-Za-z]:/")),
    ("token", re.compile(r"(?<![A-Za-z0-9])(gh[pousr]_|github_pat_|glpat-|xox[abprs]-|sk-|sk_live_|hf_|AKIA|AIza"
                         r"|ya29\.|eyJ)[A-Za-z0-9_.-]{8,}")),
    ("credential", re.compile(r"(?i)\bbearer\s|\b(password|passwd|secret|api[_-]?key|access[_-]?token"
                              r"|auth[_-]?token)\s*[=:]")),
    ("android-id", re.compile(r"(?<![A-Za-z0-9])(?=[0-9]*[a-fA-F])[0-9a-fA-F]{16}(?![A-Za-z0-9])")),
    ("network-serial", re.compile(r"(?<![0-9.])([0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9.])|\bemulator-[0-9]+")),
)


def privacy_findings(value, path=""):
    """JSON Pointers (and pattern names, never values) of strings that look private."""
    findings, stack = [], [(value, path)]
    while stack:
        node, where = stack.pop()
        if isinstance(node, dict):
            for key, child in node.items():
                stack.append((key, _pointer(where, key) + "#key"))
                stack.append((child, _pointer(where, key)))
        elif isinstance(node, list):
            stack.extend((child, _pointer(where, index)) for index, child in enumerate(node))
        elif isinstance(node, str):
            findings.extend(f"{where}: looks like {name}" for name, pattern in PRIVATE_PATTERNS
                            if pattern.search(node))
    return sorted(findings)


# ── Export ───────────────────────────────────────────────────────────────────

MANIFEST_FIELDS = {"schema", "schemaVersion", "captureId", "startedAt", "finishedAt", "status", "doneObserved",
                   "engineErrors", "invalidRecords", "rejections", "records", "checkpoints", "failedRecords",
                   "deviceInfo", "appIdentity", "publicationReady"}
RECORD_FILE = re.compile(r"(raw|checkpoint)-[0-9a-f-]{36}\.jsonl")


def _read_json(path, limit, where):
    if path.is_symlink() or not path.is_file():
        raise Refused(where, "Missing or non-regular file")
    if path.stat().st_size > limit:
        raise Refused(where, "File is too large")
    data = path.read_bytes()
    if len(data) > limit:
        raise Refused(where, "File is too large")
    try:
        text = data.decode("utf-8")
        return data, text, raw_session.parse(text)
    except UnicodeDecodeError:
        raise Refused(where, "File is not UTF-8")
    except raw_session.Invalid as error:
        raise Refused(where, f"Invalid JSON ({error.reasons[0]['code']})")


def _device_value(value, spec, path):
    if value in (None, ""):
        return None  # Unknown stays unknown.
    return spec.apply(value, path, None)


def _load_entries(directory, manifest, key, statuses, ctx):
    entries = manifest.get(key)
    if not isinstance(entries, list):
        raise Refused(f"/{key}", "Expected a list of record entries")
    loaded = []
    for index, entry in enumerate(entries):
        where = f"/{key}/{index}"
        if not isinstance(entry, dict) or not isinstance(entry.get("file"), str) or \
                not RECORD_FILE.fullmatch(entry["file"]):
            raise Refused(where, "Invalid record entry")
        if entry.get("validation") != raw_session.VALIDATION:
            raise Refused(f"{where}/validation", "Only raw-session-v1 records can be exported")
        data, text, record = _read_json(directory / entry["file"], MAX_RAW_BYTES, f"{where}/file")
        if sha256(data) != entry.get("sha256"):
            raise Refused(f"{where}/sha256", "Record bytes do not match the capture checksum")
        reasons = raw_session.validate_text(text)
        if reasons:
            codes = ", ".join(sorted({item["code"] for item in reasons}))
            raise Refused(where, f"Record fails raw-session-v1 validation ({codes})")
        if record["status"] not in statuses:
            raise Refused(f"{where}/status", f"Unexpected record status in {key}")
        for extra in set(entry) - {"file", "sha256", "validation", "sourceFile"}:
            ctx.drop(f"{where}/{extra}", "unknown-field")
        loaded.append((entry, record))
    return loaded


def load_capture(directory, ctx):
    """Validate one capture directory and return (capture summary, [session documents])."""
    directory = Path(directory)
    ctx.source = directory.name
    _, _, manifest = _read_json(directory / "manifest.json", MAX_RAW_BYTES, "/manifest.json")
    if not isinstance(manifest, dict) or manifest.get("schema") != "xebench-local-capture" or \
            manifest.get("schemaVersion") != 1:
        raise Refused("/schema", "Not a xebench-local-capture v1 manifest")
    for key in sorted(set(manifest) - MANIFEST_FIELDS):
        ctx.drop(_pointer("", key), "unknown-field")
    if "publicationReady" in manifest:
        ctx.drop("/publicationReady", "excluded")  # Validation is not publication eligibility.
    capture_id = UUID.apply(manifest.get("captureId"), "/captureId", ctx)
    ctx.source = capture_id
    records = _load_entries(directory, manifest, "records", ("complete", "failed"), ctx)
    checkpoints = _load_entries(directory, manifest, "checkpoints", ("running",), ctx)
    listed = {entry["file"] for entry, _ in records + checkpoints}
    unlisted = sorted(path.name for path in directory.iterdir()
                      if RECORD_FILE.fullmatch(path.name) and path.name not in listed)
    if unlisted:
        raise Refused("/records", f"{len(unlisted)} record file(s) are not listed in the manifest")
    if not records:
        raise Refused("/records", "Capture has no terminal session record to export")

    rejections = manifest.get("rejections")
    if not isinstance(rejections, list) or manifest.get("invalidRecords") != len(rejections):
        raise Refused("/rejections", "Rejection list disagrees with invalidRecords")
    codes = {}
    for index, item in enumerate(rejections):
        found = item.get("codes") if isinstance(item, dict) else None
        if not isinstance(found, list) or not found:
            raise Refused(f"/rejections/{index}", "Rejection without reason codes")
        for code in {REJECTION_CODES.apply(code, f"/rejections/{index}/codes/{position}", ctx)
                     for position, code in enumerate(found)}:
            codes[code] = codes.get(code, 0) + 1
    failed = sum(1 for _, record in records if record["status"] == "failed")
    if manifest.get("failedRecords") != failed:
        raise Refused("/failedRecords", "failedRecords disagrees with the terminal records")

    device_info = manifest.get("deviceInfo")
    if not isinstance(device_info, dict):
        raise Refused("/deviceInfo", "Expected host device facts")
    for key in sorted(set(device_info) - {"manufacturer", "model", "soc"}):
        # ro.product.device (codename) is excluded: model and SoC identify the hardware.
        ctx.drop(_pointer("/deviceInfo", key), "excluded" if key == "device" else "unknown-field")
    host_device = {key: _device_value(device_info.get(key), DEVICE_TEXT, f"/deviceInfo/{key}")
                   for key in ("manufacturer", "model", "soc")}

    sessions = []
    for entry, record in records:
        ctx.source = f"{capture_id}/{entry['file']}"
        record = dict(record)
        info = record.pop("deviceInfo")
        device = {"manufacturer": host_device["manufacturer"], "soc": host_device["soc"],
                  "model": _device_value(info.get("model"), DEVICE_TEXT, "/deviceInfo/model"),
                  "androidRelease": _device_value(info.get("androidRelease"), RELEASE, "/deviceInfo/androidRelease")}
        sessions.append({
            "schema": SESSION_SCHEMA, "schemaVersion": FORMAT_VERSION, "sessionId": record["runId"],
            "captureId": capture_id, "rawSha256": entry["sha256"], "validation": raw_session.VALIDATION,
            "device": device, "record": RECORD.apply(record, "", ctx),
        })
    ctx.source = capture_id
    status = manifest.get("status")
    if status not in CAPTURE_STATUSES:
        raise Refused("/status", "Unknown capture status")
    identity = manifest.get("appIdentity")
    if status == "complete" and (manifest.get("doneObserved") is not True or rejections or failed or
                                 manifest.get("engineErrors") != 0 or
                                 not isinstance(identity, dict) or identity.get("consistency") == "changed"):
        # capture_app_run.py only reports complete for a clean, finished capture.
        raise Refused("/status", "A complete capture must be finished without errors, rejections or failures")
    capture = {
        "schema": CAPTURE_SCHEMA, "schemaVersion": FORMAT_VERSION, "captureId": capture_id,
        "startedAt": UTC.apply(manifest.get("startedAt"), "/startedAt", ctx),
        "finishedAt": UTC.apply(manifest.get("finishedAt"), "/finishedAt", ctx),
        "status": status, "doneObserved": BOOL.apply(manifest.get("doneObserved"), "/doneObserved", ctx),
        "engineErrors": COUNT.apply(manifest.get("engineErrors"), "/engineErrors", ctx),
        "invalidRecords": len(rejections), "failedRecords": failed, "checkpointCount": len(checkpoints),
        "sessionIds": [session["sessionId"] for session in sessions],
        "rejections": {"count": len(rejections), "codes": codes},
        "appIdentity": APP_IDENTITY.apply(manifest.get("appIdentity"), "/appIdentity", ctx),
        "device": host_device,
    }
    return capture, sessions


def build_bundle(capture_dirs):
    """Return ({path: bytes}, manifest bytes, sha, report) or raise Refused."""
    ctx = Context()
    captures, sessions = {}, {}
    # Refusals name inputs by position: directory names and run IDs may hold the private value.
    for position, directory in enumerate(capture_dirs):
        try:
            capture, found = load_capture(directory, ctx)
        except Refused as error:
            raise Refused(f"capture[{position}]:{error.where}", error.message)
        if capture["captureId"] in captures:
            raise Refused(f"capture[{position}]", "Capture given more than once")
        captures[capture["captureId"]] = capture
        for index, session in enumerate(found):
            if session["sessionId"] in sessions:
                raise Refused(f"capture[{position}]:/records/{index}", "Session appears in more than one record")
            sessions[session["sessionId"]] = session
    documents = {f"captures/{key}.json": value for key, value in captures.items()}
    documents.update({f"sessions/{key}.json": value for key, value in sessions.items()})
    # File names hold run IDs, which may be the private value; name documents by position.
    findings = [f"{path.partition('/')[0]}[{index}]{item}"
                for index, (path, document) in enumerate(sorted(documents.items()))
                for item in privacy_findings(document)]
    if findings:
        raise Refused("privacy-screen", "; ".join(findings))
    files = {path: canonical(document) for path, document in documents.items()}
    manifest = {
        "schema": BUNDLE_SCHEMA, "schemaVersion": FORMAT_VERSION, "hashRule": HASH_RULE,
        "canonicalJson": CANONICAL_RULE, "statistics": STATISTICS_RULE,
        "sessionIds": sorted(sessions), "captureIds": sorted(captures),
        "files": [{"path": path, "sha256": sha256(data), "bytes": len(data)} for path, data in sorted(files.items())],
    }
    findings = privacy_findings(manifest)
    if findings:
        raise Refused("privacy-screen", "manifest.json " + "; ".join(findings))
    manifest_bytes = canonical(manifest)
    report = sorted(ctx.dropped, key=lambda item: (item["source"], item["path"], item["kind"]))
    return files, manifest_bytes, sha256(manifest_bytes), report


def _write_bundle(out_dir, files, manifest_bytes, digest):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    final = out_dir / digest
    if final.exists():
        # Content-addressed: an existing bundle with this hash must be identical.
        if verify(final, digest)["valid"] and (final / "manifest.json").read_bytes() == manifest_bytes:
            return final
        raise Refused(str(digest), "A different bundle already exists at the content address")
    staging = out_dir / f".staging-{uuid.uuid4()}"
    try:
        for path, data in list(files.items()) + [("manifest.json", manifest_bytes)]:
            target = staging / path
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as out:
                out.write(data)
        os.rename(staging, final)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return final


def export(capture_dirs, out_dir, base_url=None):
    if base_url is not None and not re.fullmatch(r"https://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[A-Za-z0-9._~-]+)*/?",
                                                 base_url):
        raise Refused("--base-url", "Expected an https URL without query or fragment")
    files, manifest_bytes, digest, dropped = build_bundle(capture_dirs)
    final = _write_bundle(out_dir, files, manifest_bytes, digest)
    evidence = {"sessionIds": json.loads(manifest_bytes)["sessionIds"], "bundleSha256": digest}
    if base_url is not None:
        evidence["url"] = f"{base_url.rstrip('/')}/{digest}/manifest.json"
    return {"bundle": str(final), "evidence": evidence, "dropped": dropped}


# ── Verify ───────────────────────────────────────────────────────────────────

def _parse_canonical(data, where, errors):
    try:
        value = raw_session.parse(data.decode("utf-8"))
    except (UnicodeDecodeError, raw_session.Invalid):
        errors.append(f"{where}: not valid JSON")
        return None
    if canonical(value) != data:
        errors.append(f"{where}: not in canonical form")
    return value


def _conforms(document, spec, where, errors):
    ctx = Context()
    try:
        spec.apply(document, "", ctx)
    except Refused as error:
        errors.append(f"{where}{error.where}: {error.message}")
        return
    errors.extend(f"{where}{item['path']}: field is not in the export allowlist" for item in ctx.dropped)


def recompute(record):
    """Summary statistics re-derived from the record's successful repetitions."""
    complete = [attempt for attempt in record["coldRuns"] if attempt.get("status") == "complete"]
    return {metric: raw_session.aggregate([attempt[metric] for attempt in complete])
            for metric in raw_session.METRICS}


def _check_statistics(record, where, errors):
    for metric, expected in recompute(record).items():
        actual = record["summary"].get(metric)
        if expected is None or actual is None:
            if expected is not actual:
                errors.append(f"{where}/summary/{metric}: must be null exactly when no repetition succeeded")
            continue
        for key in ("median", "iqr", "min", "max", "n"):
            same = actual[key] == expected[key] if key == "n" else \
                math.isclose(actual[key], expected[key], rel_tol=raw_session.TOLERANCE, abs_tol=raw_session.TOLERANCE)
            if not same:
                errors.append(f"{where}/summary/{metric}/{key}: reported {actual[key]!r}, "
                              f"repetitions give {expected[key]!r}")


def _check_record(record, where, errors):
    """Cheap cross-field rules a reader can re-check without the raw record."""
    runs, planned = record["coldRuns"], record["protocol"]["nColdRuns"]
    if [attempt.get("runIndex") for attempt in runs] != list(range(len(runs))):
        errors.append(f"{where}/coldRuns: repetitions must be numbered consecutively from 0")
    if len(runs) > planned:
        errors.append(f"{where}/coldRuns: more attempts than the configured {planned}")
    statuses = [attempt.get("status") for attempt in runs]
    if record["status"] == "complete":
        ok = len(runs) == planned and all(status == "complete" for status in statuses)
    elif record["status"] == "failed" and not runs:
        # A run recorded without measurement (cpu-reference-v2+): no attempts, and no artifact
        # unless it was hashed and found to differ from the configured hash.
        stop = record.get("stopReason")
        ok = stop in raw_session.STOP_REASONS_UNMEASURED and \
            (record.get("artifact") is None) == (stop in raw_session.STOP_REASONS_UNHASHED)
    elif record["status"] == "failed":
        ok = statuses[-1] in ("failed", "skipped") and all(status == "complete" for status in statuses[:-1])
    else:
        ok = False
    if not ok:
        errors.append(f"{where}/status: does not agree with the repetitions")
    _check_statistics(record, where, errors)


def _check_capture(capture, sessions, path, errors):
    mine = [doc for doc in sessions.values() if doc.get("captureId") == capture["captureId"]]
    if sorted(doc["sessionId"] for doc in mine) != sorted(capture["sessionIds"]):
        errors.append(f"{path}/sessionIds: does not match the sessions that name this capture")
    failed = sum(1 for doc in mine if doc["record"].get("status") == "failed")
    rejections = capture["rejections"]
    if capture["failedRecords"] != failed or capture["invalidRecords"] != rejections["count"] or \
            sum(rejections["codes"].values()) < rejections["count"] or \
            (rejections["count"] == 0) != (not rejections["codes"]):
        errors.append(f"{path}: counts disagree with its sessions or rejections")
    if capture["status"] == "complete" and (not capture["doneObserved"] or failed or rejections["count"] or
                                            capture["engineErrors"] or
                                            capture["appIdentity"]["consistency"] == "changed"):
        errors.append(f"{path}/status: complete capture with errors, rejections or failures")
    for doc in mine:
        device = doc["device"]
        if (device["manufacturer"], device["soc"]) != (capture["device"]["manufacturer"], capture["device"]["soc"]):
            errors.append(f"sessions/{doc['sessionId']}.json/device: disagrees with its capture")


def _verify(bundle, expected_sha, expected_sessions, result, errors):
    manifest_path = bundle / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        errors.append("manifest.json: missing")
        return
    if manifest_path.stat().st_size > MAX_BUNDLE_FILE_BYTES:
        errors.append("manifest.json: too large")
        return
    manifest_bytes = manifest_path.read_bytes()
    digest = result["bundleSha256"] = sha256(manifest_bytes)
    if expected_sha is not None and expected_sha != digest:
        errors.append("manifest.json: SHA-256 differs from the expected bundleSha256")
    if re.fullmatch(r"[0-9a-f]{64}", bundle.name) and bundle.name != digest:
        errors.append("manifest.json: SHA-256 differs from the bundle's content address")
    manifest = _parse_canonical(manifest_bytes, "manifest.json", errors)
    before = len(errors)
    _conforms(manifest, MANIFEST_FILE, "manifest.json", errors)
    if len(errors) > before:
        return
    errors.extend(f"manifest.json{item}" for item in privacy_findings(manifest))
    listed = {}
    for index, item in enumerate(manifest["files"]):
        if item["path"] in listed:
            errors.append(f"manifest.json/files/{index}: duplicate entry")
        listed[item["path"]] = item
    if [item["path"] for item in manifest["files"]] != sorted(listed):
        errors.append("manifest.json/files: entries must be sorted by path")
    present = sorted(str(path.relative_to(bundle)) for path in bundle.rglob("*")
                     if path.is_file() or path.is_symlink())
    errors.extend(f"{path}: file is not listed in the manifest" for path in present
                  if path != "manifest.json" and path not in listed)
    sessions, captures = {}, {}
    for path, item in sorted(listed.items()):
        target = bundle / path
        if target.is_symlink() or not target.is_file() or any(parent.is_symlink() for parent in target.parents
                                                                if parent != bundle and bundle in parent.parents):
            errors.append(f"{path}: listed file is missing or not a regular file")
            continue
        if target.stat().st_size != item["bytes"] or item["bytes"] > MAX_BUNDLE_FILE_BYTES:
            errors.append(f"{path}: checksum or size mismatch")
            continue
        data = target.read_bytes()
        if len(data) != item["bytes"] or sha256(data) != item["sha256"]:
            errors.append(f"{path}: checksum or size mismatch")
            continue
        document = _parse_canonical(data, path, errors)
        kind, _, name = path.partition("/")
        before = len(errors)
        _conforms(document, SESSION_FILE if kind == "sessions" else CAPTURE_FILE, path, errors)
        errors.extend(f"{path}{finding}" for finding in privacy_findings(document))
        if len(errors) > before:
            continue
        # Conforming documents have the expected types; bind each to its file name.
        key = document["sessionId"] if kind == "sessions" else document["captureId"]
        if name != f"{key}.json":
            errors.append(f"{path}: file name does not match its {kind[:-1]} ID")
            continue
        (sessions if kind == "sessions" else captures)[key] = document
    if manifest["sessionIds"] != sorted(sessions):
        errors.append("manifest.json/sessionIds: does not match the session files")
    if manifest["captureIds"] != sorted(captures):
        errors.append("manifest.json/captureIds: does not match the capture files")
    if not sessions:
        errors.append("bundle: holds no session")
    for position, session_id in enumerate(expected_sessions):
        if session_id not in sessions:
            errors.append(f"bundle: expected session [{position}] is missing")
    for session_id, doc in sorted(sessions.items()):
        path, record = f"sessions/{session_id}.json", doc["record"]
        if record["runId"] != session_id:
            errors.append(f"{path}: session ID does not match its record")
        if doc["captureId"] not in captures:
            errors.append(f"{path}: its capture is not in the bundle")
        _check_record(record, path + "/record", errors)
        result["sessions"][session_id] = {
            "status": record["status"], "repetitions": len(record["coldRuns"]),
            "median": {metric: (value or {}).get("median") for metric, value in record["summary"].items()},
        }
    for capture_id, capture in sorted(captures.items()):
        _check_capture(capture, sessions, f"captures/{capture_id}.json", errors)


def verify(bundle, expected_sha=None, expected_sessions=()):
    """Check a bundle; never raises for malformed content, always returns errors instead."""
    errors = []
    result = {"valid": False, "bundleSha256": None, "sessions": {}, "errors": errors}
    try:
        _verify(Path(bundle), expected_sha, tuple(expected_sessions), result, errors)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
        errors.append(f"bundle: malformed or unreadable ({type(error).__name__})")
    result["valid"] = not errors
    return result


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    export_parser = commands.add_parser("export", help="Write a sanitized bundle from capture directories")
    export_parser.add_argument("--out", required=True, help="Directory that receives <bundleSha256>/")
    export_parser.add_argument("--base-url", help="https base under which <bundleSha256>/ will be served")
    export_parser.add_argument("captures", nargs="+", help="Capture directories (results/<captureId>)")
    verify_parser = commands.add_parser("verify", help="Check checksums and recompute summaries")
    verify_parser.add_argument("bundle")
    verify_parser.add_argument("--sha256", help="Expected bundleSha256")
    verify_parser.add_argument("--session", action="append", default=[],
                               help="Session ID the bundle must contain (repeatable), e.g. a row's evidence")
    args = parser.parse_args(argv)
    if args.command == "export":
        try:
            report = export(args.captures, args.out, args.base_url)
        except Refused as error:
            print(f"Export refused: {error}", file=sys.stderr)
            return 1
        except OSError as error:
            # The OS message would name host paths; report only the kind of failure.
            print(f"Export refused: file system error ({type(error).__name__})", file=sys.stderr)
            return 1
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    result = verify(args.bundle, args.sha256, args.session)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
