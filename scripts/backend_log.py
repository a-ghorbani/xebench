"""NPU backend confirmation from the app's own log (rules litert-npu-log-v1 and geniex-npu-log-v1).

Neither LiteRT-LM nor GENIE-X reports an executing backend, so an NPU repetition's process-library
evidence is at most `consistent` (backend.ts). Their own logs show the Qualcomm HTP path doing the
work. capture_app_run.py reads the app process's log (pid-scoped, tags LOGCAT_FILTER only) and keeps
the lines that match a rule's events; export_evidence.py puts them, sanitized, in the capture
document; and publication_policy.py derives the backend status from them with `confirmation()`, so a
bundle alone reproduces the verdict. A capture's log carries one rule: the GENIE-X rule when the
capture recorded a GENIE-X session, else the LiteRT-LM rule (`rule_for_engines`).

litert-npu-log-v1. A LiteRT-LM NPU repetition is `confirmed` when the log of its capture shows:
  - for that repetition's engine load (a time between its `before` and `after` condition
    snapshots, which bracket load and request): the QNN context created from the bundle
    (`qnn-context`);
  - for the process, at or before that load: the dispatch library loaded (`dispatch-library`) and
    the QNN HTP backend selected (`htp-backend`). LiteRT-LM loads the dispatch library and selects
    the backend once per process, at the first NPU engine load (the output gate's), so later loads
    in the same process do not log them again;
  - at least once in the capture: the HTP skel opened on the compute DSP (`htp-skel-cdsp`).

geniex-npu-log-v1 (pending device confirmation: written from the geniex-android 0.8.0 sources and
the format strings in its shipped libraries, not yet from a device log). A GENIE-X NPU repetition is
`confirmed` when the log of its capture shows, within that repetition's window (its engine load and
request; GENIE-X's QAIRT plugin logs each of these once per model load or request):
  - `htp-runtime`: the QNN runtime resolved from the APK's own native library directory
    (`HTP runtime path: … (auto-resolved from geniex_core directory (flat layout))`), not an
    override;
  - `htp-arch`: the compute DSP answered with its HTP architecture (`HTP arch vNN detected.`);
  - `htp-graphs`: the bundle's QNN context binaries were created on the HTP backend and their
    graphs retrieved (`HTP graphs will execute on …`, logged only after every context of the load
    was created from its binary);
  - `prompt`: the request was tokenized and prefilled by GENIE-X's QAIRT pipeline
    (`prompt: N tokens, first-turn BOS=…`; the llama_cpp plugin never logs it);
and, at least once in the capture, the HTP skel opened on the compute DSP (`htp-skel-cdsp`, the
FastRPC line litert-npu-log-v1 uses).

A session is `confirmed` when every measured repetition is.
"""
import re

APP = "com.xebenchapp"
GENIEX_TAG = "GenieXSdk"
# logcat filter specs: LiteRT-LM's tag, geniex-android's tag (its JNI routes every SDK, plugin and
# QNN log line there), and the app's process-name tag, which libcdsprpc (FastRPC, dspqueue) logs
# under. Everything else is silenced.
LOGCAT_FILTER = ("litert:I", f"{GENIEX_TAG}:I", f"{APP}:I", "*:S")

_HEX = r"0x(?:[0-9a-fA-F]+|_)"
# FastRPC opening the QNN HTP skel on the compute DSP (libcdsprpc, logged under the process name).
_SKEL = (APP, re.compile(
    rf"\S*fastrpc_apps_user\.c:[0-9]+: remote_handle64_open: opened handle {_HEX} \(remote {_HEX}\) "
    r"for (?:file:///)?libQnnHtpV[0-9]+Skel\.so\?[A-Za-z0-9_&=.]*_dom=cdsp on domain [0-9]+\b[ -~]*"))
# geniex-qairt prefixes "[file:line:func] " only in GENIEX_DEBUG builds; release AARs log the message.
_GX = r"(?:\[[^\]]{1,200}\] )?"
# An absolute directory: sanitized to "_" (the app's install path holds random components).
_DIR = r"(?:/(?:data|vendor|system|odm|apex|product)/\S+|_)"

RULE = "litert-npu-log-v1"
GENIEX_RULE = "geniex-npu-log-v1"
# kind -> (tag, message pattern). Patterns accept both the raw and the sanitized message.
EVENTS = {
    "dispatch-library": ("litert", re.compile(
        r"\[litert_dispatch\.cc:[0-9]+\] Loading shared library: (?:\S*/)?libLiteRtDispatch_Qualcomm\.so")),
    "htp-backend": ("litert", re.compile(r"\s*BackendType\s*:\s*Htp\([0-9]+\)")),
    "qnn-context": ("litert", re.compile(
        rf"\[litert_dispatch_device_context\.cc:[0-9]+\] Creating new QNN context for bytecode {_HEX} "
        r"\(size [0-9]+\)")),
    "htp-skel-cdsp": _SKEL,
}
GENIEX_EVENTS = {
    "htp-runtime": (GENIEX_TAG, re.compile(
        rf"{_GX}HTP runtime path: {_DIR} \(auto-resolved from geniex_core directory \(flat layout\)\)")),
    "htp-arch": (GENIEX_TAG, re.compile(rf"{_GX}HTP arch v[0-9]{{2,3}} detected\.")),
    "htp-graphs": (GENIEX_TAG, re.compile(
        rf"{_GX}(?:HTP graphs will execute on 1 core(?: \(default; [ -~]*\))?"
        r"|HTP multicore enabled: graphs will execute on [0-9]+ cores)")),
    "prompt": (GENIEX_TAG, re.compile(
        rf"{_GX}prompt: [0-9]+ tokens, first-turn BOS=(?:true|false), ids start with \[[0-9, ]{{0,80}}\]")),
    "htp-skel-cdsp": _SKEL,
}
# rule -> the engine it confirms, its events, and when each kind must appear for a repetition:
# `window` within the repetition's load and request, `process` at or before its end, `capture` once.
RULES = {
    RULE: {"engine": "litert-lm", "events": EVENTS, "window": ("qnn-context",),
           "process": ("dispatch-library", "htp-backend"), "capture": ("htp-skel-cdsp",)},
    GENIEX_RULE: {"engine": "genie-x", "events": GENIEX_EVENTS,
                  "window": ("htp-runtime", "htp-arch", "htp-graphs", "prompt"), "process": (),
                  "capture": ("htp-skel-cdsp",)},
}
# Rules whose patterns no device log has confirmed yet: confirmation() still evaluates them, but the
# publication policy does not let them confirm a backend. A device check that shows every event on a
# real capture removes the rule from this set in a reviewed change.
PENDING_DEVICE_CONFIRMATION = frozenset({GENIEX_RULE})
KINDS = tuple(EVENTS)
MAX_LINE = 300
MAX_EVENTS = 5000

# logcat -v epoch: "<seconds>.<millis> <pid> <tid> <level> <tag>: <message>" (tag padded with spaces).
_LINE = re.compile(r"\s*([0-9]{1,12})\.([0-9]{3})\s+([0-9]+)\s+([0-9]+)\s+([VDIWEF])\s+(\S[^:]*?)\s*: (.*)")
# Absolute library paths (the app's install directory holds a random component) keep their file name.
_PATH = re.compile(r"(?<![:/A-Za-z0-9])/(?:data|vendor|system|odm|apex|product)/\S*?/(lib[A-Za-z0-9_]+\.so)")
# GENIE-X names the QNN runtime directory itself (no library file name to keep).
_RUNTIME_DIR = re.compile(r"(HTP runtime path: )/(?:data|vendor|system|odm|apex|product)/\S+")


def sanitize(message):
    """The message without install paths, file URIs or pointer values: stable, and free of device identifiers."""
    message = _RUNTIME_DIR.sub(r"\1_", _PATH.sub(r"\1", message))
    return re.sub(_HEX, "0x_", message).replace("file:///", "")


def rule_for_engines(engines):
    """The rule a capture's backend log is kept under: GENIE-X's when it recorded a GENIE-X session."""
    return GENIEX_RULE if "genie-x" in engines else RULE


def classify(tag, message, rule=RULE):
    """The event kind a (tag, message) pair is under a rule, or None."""
    for kind, (event_tag, pattern) in RULES[rule]["events"].items():
        if tag == event_tag and pattern.fullmatch(message):
            return kind
    return None


def parse_line(line, pid=None):
    """One logcat -v epoch line -> (timeMs, tag, message), or None (another format or process)."""
    match = _LINE.fullmatch(line.rstrip("\r\n"))
    if not match:
        return None
    seconds, millis, line_pid, _tid, _level, tag, message = match.groups()
    if pid is not None and line_pid != str(pid):
        return None
    return int(seconds) * 1000 + int(millis), tag, message


def matching_lines(text, pid, rules=tuple(RULES)):
    """The lines of a logcat snapshot that are backend events of process pid under any of rules, in order."""
    found = []
    for line in text.splitlines():
        parsed = parse_line(line, pid)
        if parsed and any(classify(parsed[1], parsed[2], rule) for rule in rules):
            found.append(line.rstrip("\r\n"))
    return found


def events_from_lines(lines, rule=RULE):
    """Sanitized events {timeMs, kind, tag, line} of matching lines; ValueError on any other line."""
    events = []
    for index, line in enumerate(lines):
        parsed = parse_line(line)
        kind = parsed and classify(parsed[1], parsed[2], rule)
        if not kind:
            raise ValueError(f"backend log line {index} is not a {rule} event")
        time_ms, tag, message = parsed
        events.append({"timeMs": time_ms, "kind": kind, "tag": tag, "line": sanitize(message)[:MAX_LINE]})
    if len(events) > MAX_EVENTS:
        raise ValueError("backend log has too many events")
    return events


def valid_event(event, rule=RULE):
    """An exported event: its line is still an event of the kind it claims (a relabel is refused)."""
    return (isinstance(event, dict) and set(event) == {"timeMs", "kind", "tag", "line"}
            and isinstance(event["timeMs"], int) and not isinstance(event["timeMs"], bool) and event["timeMs"] >= 0
            and event["kind"] in RULES[rule]["events"] and isinstance(event["tag"], str)
            and isinstance(event["line"], str) and len(event["line"]) <= MAX_LINE
            and classify(event["tag"], event["line"], rule) == event["kind"]
            and sanitize(event["line"]) == event["line"])


def confirmation(record, backend_log):
    """{rule, status, repetitions[{runIndex, confirmed, missing}]} for an NPU accelerator session and
    its capture's backend log; None when no rule applies (another engine or backend, a log under
    another engine's rule, or a capture without a backend log)."""
    if record.get("backend") != "npu" or not isinstance(backend_log, dict):
        return None
    rule = backend_log.get("rule")
    spec = RULES.get(rule) if isinstance(rule, str) else None
    if spec is None or record.get("engine") != spec["engine"] or not isinstance(backend_log.get("events"), list):
        return None
    times = {kind: sorted(event["timeMs"] for event in backend_log["events"]
                          if valid_event(event, rule) and event["kind"] == kind) for kind in spec["events"]}
    repetitions = []
    for attempt in record.get("coldRuns", []):
        if attempt.get("status") != "complete":
            continue
        conditions = attempt.get("conditions") or {}
        start = (conditions.get("before") or {}).get("timestampMs")
        end = (conditions.get("after") or {}).get("timestampMs")
        missing = []
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            missing = ["repetition-window"]
        else:
            missing += [kind for kind in spec["window"] if not any(start <= t <= end for t in times[kind])]
            missing += [kind for kind in spec["process"] if not any(t <= end for t in times[kind])]
            missing += [kind for kind in spec["capture"] if not times[kind]]
        repetitions.append({"runIndex": attempt.get("runIndex"), "confirmed": not missing, "missing": missing})
    status = "confirmed" if repetitions and all(item["confirmed"] for item in repetitions) else "unconfirmed"
    return {"rule": rule, "status": status, "repetitions": repetitions}
