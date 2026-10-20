"""LiteRT-LM NPU backend confirmation from the app's own log (rule litert-npu-log-v1).

LiteRT-LM reports no executing backend, so an NPU repetition's process-library evidence is at
most `consistent` (backend.ts). Its own log does show the Qualcomm dispatch path doing the work.
capture_app_run.py reads the app process's log (pid-scoped, tags LOGCAT_FILTER only) and keeps the
lines that match EVENTS; export_evidence.py puts them, sanitized, in the capture document; and
publication_policy.py derives the backend status from them with `confirmation()`, so a bundle
alone reproduces the verdict.

A LiteRT-LM NPU repetition is `confirmed` when the log of its capture shows:
  - for that repetition's engine load (a time between its `before` and `after` condition
    snapshots, which bracket load and request): the QNN context created from the bundle
    (`qnn-context`);
  - for the process, at or before that load: the dispatch library loaded (`dispatch-library`) and
    the QNN HTP backend selected (`htp-backend`). LiteRT-LM loads the dispatch library and selects
    the backend once per process, at the first NPU engine load (the output gate's), so later loads
    in the same process do not log them again;
  - at least once in the capture: the HTP skel opened on the compute DSP (`htp-skel-cdsp`).
A session is `confirmed` when every measured repetition is.
"""
import re

RULE = "litert-npu-log-v1"
APP = "com.xebenchapp"
# logcat filter specs: LiteRT-LM's own tag, and the app's process-name tag, which libcdsprpc
# (FastRPC, dspqueue) logs under. Everything else is silenced.
LOGCAT_FILTER = ("litert:I", f"{APP}:I", "*:S")

_HEX = r"0x(?:[0-9a-fA-F]+|_)"
# kind -> (tag, message pattern). Patterns accept both the raw and the sanitized message.
EVENTS = {
    "dispatch-library": ("litert", re.compile(
        r"\[litert_dispatch\.cc:[0-9]+\] Loading shared library: (?:\S*/)?libLiteRtDispatch_Qualcomm\.so")),
    "htp-backend": ("litert", re.compile(r"\s*BackendType\s*:\s*Htp\([0-9]+\)")),
    "qnn-context": ("litert", re.compile(
        rf"\[litert_dispatch_device_context\.cc:[0-9]+\] Creating new QNN context for bytecode {_HEX} "
        r"\(size [0-9]+\)")),
    "htp-skel-cdsp": (APP, re.compile(
        rf"\S*fastrpc_apps_user\.c:[0-9]+: remote_handle64_open: opened handle {_HEX} \(remote {_HEX}\) "
        r"for (?:file:///)?libQnnHtpV[0-9]+Skel\.so\?[A-Za-z0-9_&=.]*_dom=cdsp on domain [0-9]+\b[ -~]*")),
}
KINDS = tuple(EVENTS)
MAX_LINE = 300
MAX_EVENTS = 5000

# logcat -v epoch: "<seconds>.<millis> <pid> <tid> <level> <tag>: <message>" (tag padded with spaces).
_LINE = re.compile(r"\s*([0-9]{1,12})\.([0-9]{3})\s+([0-9]+)\s+([0-9]+)\s+([VDIWEF])\s+(\S[^:]*?)\s*: (.*)")
# Absolute library paths (the app's install directory holds a random component) keep their file name.
_PATH = re.compile(r"(?<![:/A-Za-z0-9])/(?:data|vendor|system|odm|apex|product)/\S*?/(lib[A-Za-z0-9_]+\.so)")


def sanitize(message):
    """The message without install paths, file URIs or pointer values: stable, and free of device identifiers."""
    return re.sub(_HEX, "0x_", _PATH.sub(r"\1", message)).replace("file:///", "")


def classify(tag, message):
    """The event kind a (tag, message) pair is, or None."""
    for kind, (event_tag, pattern) in EVENTS.items():
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


def matching_lines(text, pid):
    """The lines of a logcat snapshot that are backend events of process pid, in order."""
    found = []
    for line in text.splitlines():
        parsed = parse_line(line, pid)
        if parsed and classify(parsed[1], parsed[2]):
            found.append(line.rstrip("\r\n"))
    return found


def events_from_lines(lines):
    """Sanitized events {timeMs, kind, tag, line} of matching lines; ValueError on any other line."""
    events = []
    for index, line in enumerate(lines):
        parsed = parse_line(line)
        kind = parsed and classify(parsed[1], parsed[2])
        if not kind:
            raise ValueError(f"backend log line {index} is not a {RULE} event")
        time_ms, tag, message = parsed
        events.append({"timeMs": time_ms, "kind": kind, "tag": tag, "line": sanitize(message)[:MAX_LINE]})
    if len(events) > MAX_EVENTS:
        raise ValueError("backend log has too many events")
    return events


def valid_event(event):
    """An exported event: its line is still an event of the kind it claims (a relabel is refused)."""
    return (isinstance(event, dict) and set(event) == {"timeMs", "kind", "tag", "line"}
            and isinstance(event["timeMs"], int) and not isinstance(event["timeMs"], bool) and event["timeMs"] >= 0
            and event["kind"] in EVENTS and isinstance(event["tag"], str) and isinstance(event["line"], str)
            and len(event["line"]) <= MAX_LINE and classify(event["tag"], event["line"]) == event["kind"]
            and sanitize(event["line"]) == event["line"])


def confirmation(record, backend_log):
    """{rule, status, repetitions[{runIndex, confirmed, missing}]} for a LiteRT-LM NPU accelerator
    session and its capture's backend log; None when the rule does not apply (another engine or
    backend, or a capture without a backend log)."""
    if record.get("engine") != "litert-lm" or record.get("backend") != "npu" or not isinstance(backend_log, dict):
        return None
    if backend_log.get("rule") != RULE or not isinstance(backend_log.get("events"), list):
        return None
    times = {kind: sorted(event["timeMs"] for event in backend_log["events"]
                          if valid_event(event) and event["kind"] == kind) for kind in KINDS}
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
            if not any(start <= t <= end for t in times["qnn-context"]):
                missing.append("qnn-context")
            missing += [kind for kind in ("dispatch-library", "htp-backend") if not any(t <= end for t in times[kind])]
            if not times["htp-skel-cdsp"]:
                missing.append("htp-skel-cdsp")
        repetitions.append({"runIndex": attempt.get("runIndex"), "confirmed": not missing, "missing": missing})
    status = "confirmed" if repetitions and all(item["confirmed"] for item in repetitions) else "unconfirmed"
    return {"rule": RULE, "status": status, "repetitions": repetitions}
