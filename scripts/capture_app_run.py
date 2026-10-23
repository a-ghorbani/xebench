#!/usr/bin/env python3
"""Capture a foreground app invocation into unique local evidence files.

Raw payloads are private diagnostics, not sanitized public evidence. This
transport layer does not certify benchmark protocol compliance.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backend_log  # noqa: E402
import raw_session  # noqa: E402

APP = "com.xebenchapp"
ROOT = Path(__file__).resolve().parents[1]
MAX_RECORD_BYTES = 1024 * 1024


class KeyguardStop(Exception):
    """The keyguard is still up before the launch, so the capture stops (setup-failed) instead of
    starting the app behind it. args: (screenPreparation result, message for the operator)."""


SECURE_LOCK_MESSAGE = (
    "The phone shows a secure lock screen (PIN, pattern or password). The capture never dismisses or enters "
    "anything on a secure lock: unlock the phone by hand, or set the lab phone's screen lock to Swipe or None, "
    "then run the capture again.")
STILL_SHOWING_MESSAGE = (
    "The lock screen is still showing after `wm dismiss-keyguard`. Unlock the phone by hand and run the capture "
    "again; the app is not started behind a lock screen.")

# `dumpsys window policy` prints a line that is exactly "KeyguardServiceDelegate" (indented), then
# the delegate's flags, among them whether the keyguard is showing and whether it is secure (a PIN,
# pattern or password; a swipe lock is not). Both are the system's cached view, updated by
# keyguard callbacks: right after the lock settings change, `secure` can lag. A stale
# `secure=false` on a secure lock is still safe here: `wm dismiss-keyguard` then brings up the
# bouncer and unlocks nothing, the re-check still sees the keyguard showing, and the capture stops.
_KEYGUARD_DELEGATE = re.compile(r"^[ \t]*KeyguardServiceDelegate[ \t]*$", re.MULTILINE)
_KEYGUARD_FLAG = re.compile(r"^\s*(showing|secure)=(true|false)\s*$", re.MULTILINE)
KEYGUARD_BLOCK_LINES = 24
KEYGUARD_DISMISS_CHECKS = 5


def keyguard_state(dump):
    """{"showing": bool or None, "secure": bool or None} from `dumpsys window policy`."""
    state = {"showing": None, "secure": None}
    marker = _KEYGUARD_DELEGATE.search(dump)
    if marker:
        # Only the delegate's own block: its flags follow the marker line within a few lines.
        block = "\n".join(dump[marker.end():].splitlines()[:KEYGUARD_BLOCK_LINES])
        for match in _KEYGUARD_FLAG.finditer(block):
            if state[match[1]] is None:
                state[match[1]] = match[2] == "true"
    return state


def _read_keyguard(command):
    # A refused or timed-out dump leaves the state unknown; it never fails the capture by itself.
    try:
        return keyguard_state(command("shell", "dumpsys", "window", "policy"))
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return {"showing": None, "secure": None}


def prepare_screen(command, sleep):
    """Wake the screen and dismiss a swipe keyguard before the app starts; the result for the manifest.

    With the keyguard showing, the app starts behind it and its readiness check waits on
    `device-locked` until the timeout. The wake key is sent first (it only turns the screen on),
    then the keyguard state is read. A swipe lock (not secure) is dismissed with
    `wm dismiss-keyguard`. On a secure lock nothing is dismissed or entered: KeyguardStop. A lock
    screen still showing after the dismissal also stops the capture. When the state cannot be read,
    nothing is dismissed and the app's own readiness check (keyguardLocked) still guards the run.
    """
    command("shell", "input", "keyevent", "KEYCODE_WAKEUP")
    result = {"wake": "KEYCODE_WAKEUP", "keyguard": "unknown"}
    state = _read_keyguard(command)
    if state["showing"] is False:
        result["keyguard"] = "not-showing"
    elif state["showing"] is True and state["secure"] is True:
        result["keyguard"] = "secure"
        raise KeyguardStop(result, SECURE_LOCK_MESSAGE)
    elif state["showing"] is True and state["secure"] is False:
        command("shell", "wm", "dismiss-keyguard")
        for _ in range(KEYGUARD_DISMISS_CHECKS):
            state = _read_keyguard(command)
            if state["showing"] is False:
                result["keyguard"] = "dismissed"
                break
            if state["secure"] is True:
                result["keyguard"] = "secure"
                raise KeyguardStop(result, SECURE_LOCK_MESSAGE)
            sleep(0.5)
        else:
            result["keyguard"] = "still-showing"
            raise KeyguardStop(result, STILL_SHOWING_MESSAGE)
    return result


class CaptureInterrupted(BaseException):
    def __init__(self, signum):
        self.signum = signum


def started_pid(events, previous_events):
    """Recover only a new main-process PID, never another app or subprocess.

    Android am_proc_start fields: User, PID, UID, Process Name, Type, Component.
    https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/services/core/java/com/android/server/am/EventLogTags.logtags
    """
    for line in reversed(events.splitlines()):
        if line in previous_events:
            continue
        match = re.search(r"am_proc_start:\s*\[\d+,(\d+),\d+," + re.escape(APP) + r",", line)
        if match:
            return match.group(1)
    return None


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def strict_json(text):
    def reject_constant(value):
        raise ValueError(f"Non-finite JSON constant: {value}")
    return json.loads(text, parse_constant=reject_constant)


def legacy_reasons(record):
    """Minimal transport checks kept for pre-v1 inline records (no schemaVersion)."""
    if record.get("schema") != "xebench-raw":
        return [raw_session.reason("record.schema", "/schema", "Expected schema xebench-raw")]
    reasons = [raw_session.reason("schema.required", f"/{field}", f"Missing {field}")
               for field in ("engine", "backend", "model", "quant")
               if not isinstance(record.get(field), str) or not record[field]]
    if not isinstance(record.get("deviceInfo", {}), dict):
        reasons.append(raw_session.reason("schema.type", "/deviceInfo", "Invalid deviceInfo"))
    return reasons


def installed_apks(command):
    """Fingerprint the selected app's base/split APKs; never retain install paths.

    Unknown probe results do not certify binary identity. Deadlines/cancellation
    still propagate through the capture lifecycle. Paths passed to Android's
    shell are restricted to a literal, option-free alphabet before hashing.
    """
    try:
        lines = command('shell', 'pm', 'path', APP).splitlines()
        if not 1 <= len(lines) <= 32:
            raise ValueError('Unexpected APK count')
        paths = []
        for line in lines:
            match = re.fullmatch(r'package:(/data/app/[a-zA-Z0-9/_.=+~-]+\.apk)', line)
            if not match or any(part in ('', '.', '..') for part in match[1].split('/')[1:]):
                raise ValueError('Unsupported APK path')
            paths.append(match[1])
        names = [Path(path).name for path in paths]
        if 'base.apk' not in names or len(set(names)) != len(names):
            raise ValueError('Missing base or duplicate APK')
        digests = {}
        for line in command('shell', 'sha256sum', *sorted(paths)).splitlines():
            match = re.fullmatch(r'([0-9a-f]{64})\s+(/\S+)', line)
            if not match or match[2] not in paths or match[2] in digests:
                raise ValueError('Unexpected checksum output')
            digests[match[2]] = match[1]
        if set(digests) != set(paths):
            raise ValueError('Missing checksum')
        return {'status': 'recorded', 'apks': sorted(
            [{'name': Path(path).name, 'sha256': digest} for path, digest in digests.items()],
            key=lambda item: item['name'])}
    except TimeoutError:
        raise
    except (OSError, subprocess.CalledProcessError, ValueError):
        return {'status': 'unavailable'}


class Capture:
    """Immutable files and a terminal manifest for one invocation."""

    def __init__(self, root, device_info):
        self.capture_id = str(uuid.uuid4())
        self.directory = Path(root) / self.capture_id
        self.directory.mkdir(parents=True, exist_ok=False)
        self.device_info = device_info
        self.started_at = utc_now()
        self.seen = set()
        self.records = []
        self.checkpoints = []
        self.failed_records = 0
        self.errors = 0
        self.invalid = 0
        self.rejections = []
        self.done = False
        self.app_identity = {'packageName': APP, 'before': None, 'after': None, 'consistency': 'unverified'}
        # Backend events of the app process (backend_log.py), in order; None until the log was read.
        self.backend_lines = None
        # Engines of the records kept: they select the backend log's rule (backend_log.rule_for_engines).
        self.engines = set()
        # What prepare_screen did before the launch (wake key sent, keyguard state); None until it ran.
        self.screen = None

    def read_reference(self, payload, read_file):
        """Resolve a bounded logcat file reference to the exact checksummed bytes."""
        claimed = {}

        def fail(code, message, content=None):
            error = raw_session.Invalid([raw_session.reason(code, "", message)], payload=content)
            # Once well formed, the reference's name and checksum stay with the rejection.
            error.reference = claimed or None
            raise error
        try:
            reference = strict_json(payload)
        except ValueError:
            fail("transport.reference", "Malformed file reference")
        if not isinstance(reference, dict) or set(reference) != {"name", "sha256"}:
            fail("transport.reference", "Invalid file reference")
        name, digest = reference["name"], reference["sha256"]
        if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9-]{1,120}\.json", name):
            fail("transport.reference", "Unsafe file reference")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or read_file is None:
            fail("transport.reference", "Missing file checksum or reader")
        claimed.update(name=name, sha256=digest)
        try:
            content = read_file(name)
        except UnicodeDecodeError:
            fail("transport.encoding", "Record is not valid UTF-8")
        raw = content.encode("utf-8")
        # Keep the retrieved bytes as the rejected payload for diagnosis.
        if len(raw) > MAX_RECORD_BYTES:
            fail("transport.size", f"Record exceeds {MAX_RECORD_BYTES} bytes", content)
        if hashlib.sha256(raw).hexdigest() != digest:
            fail("transport.checksum", "Record checksum mismatch", content)
        return content, name, digest

    def reject(self, marker, payload, source_file, reasons, claimed_sha256=None, retrieved=False):
        """Keep the rejected payload and, beside it, why it was rejected.

        Retrieved device files are stored as retrieved, without an added newline,
        so claimedSha256 can be re-checked from saved evidence (adb output is read
        as text, so this holds for UTF-8 records without CR; oversized files keep
        only the bounded prefix). Inline logcat payloads end with a newline.
        """
        stem = f"invalid-{uuid.uuid4()}"
        with (self.directory / f"{stem}.txt").open("xb") as out:
            out.write((payload if retrieved else payload + "\n").encode("utf-8"))
        rejection = {"schema": "xebench-ingest-rejection", "schemaVersion": 1, "payloadFile": f"{stem}.txt",
                     "marker": marker, "sourceFile": source_file, "claimedSha256": claimed_sha256,
                     "reasons": reasons}
        with (self.directory / f"{stem}.reason.json").open("x", encoding="utf-8") as out:
            json.dump(rejection, out, indent=2, allow_nan=False)
            out.write("\n")
        # Count only after both files exist so invalidRecords matches rejections.
        self.invalid += 1
        self.rejections.append({"file": f"{stem}.txt", "reasonFile": f"{stem}.reason.json",
                                "codes": sorted({item["code"] for item in reasons})})

    def ingest(self, snapshot, read_file=None):
        for line in snapshot.splitlines():
            if "XEBENCH_" not in line or line in self.seen:
                continue
            self.seen.add(line)
            # Only interpret the message prefix, never marker text in a payload.
            message = line.partition("ReactNativeJS: ")[2]
            if message == "XEBENCH_DONE":
                self.done = True
            elif message.startswith("XEBENCH_ERROR "):
                self.errors += 1
                with (self.directory / f"error-{uuid.uuid4()}.txt").open("x", encoding="utf-8") as out:
                    out.write(message + "\n")
            elif message.startswith(("XEBENCH_RESULT ", "XEBENCH_RESULT_FILE ")):
                marker, _, payload = message.partition(" ")
                payload = payload.strip()
                source_file = claimed = None
                retrieved = False
                try:
                    if marker == "XEBENCH_RESULT_FILE":
                        payload, source_file, claimed = self.read_reference(payload, read_file)
                        retrieved = True
                    record = raw_session.parse(payload)
                    if isinstance(record, dict) and "schemaVersion" not in record and source_file is None:
                        # Pre-v1 inline records are preserved but never deep-validated.
                        reasons, validation = legacy_reasons(record), "legacy-unvalidated"
                    else:
                        reasons, validation = raw_session.validate(record), raw_session.VALIDATION
                    if reasons:
                        raise raw_session.Invalid(reasons)
                except raw_session.Invalid as error:
                    if error.payload is not None:
                        payload, retrieved = error.payload, True
                    reference = getattr(error, "reference", None)
                    if reference:
                        source_file, claimed = reference["name"], reference["sha256"]
                    self.reject(marker, payload, source_file, error.reasons, claimed, retrieved)
                    continue
                except (ValueError, TypeError, RecursionError, ArithmeticError) as error:
                    # Unexpected content must still be preserved, never crash capture.
                    self.reject(marker, payload, source_file,
                                [raw_session.reason("ingest.error", "", type(error).__name__)], claimed, retrieved)
                    continue
                legacy = validation == "legacy-unvalidated"
                if legacy:
                    device = record.setdefault("deviceInfo", {})
                    for key, value in self.device_info.items():
                        if not device.get(key):
                            device[key] = value
                is_checkpoint = record.get("status") == "running"
                prefix = "checkpoint" if is_checkpoint else "raw"
                name = f"{prefix}-{uuid.uuid4()}.jsonl"
                # Validated v1 records keep their exact bytes (file references keep
                # the on-device checksum); host device facts live in the manifest.
                # Only legacy inline records retain their existing enrichment.
                if legacy:
                    data = json.dumps(record, allow_nan=False) + "\n"
                else:
                    data = payload if source_file else payload + "\n"
                data = data.encode("utf-8")
                with (self.directory / name).open("xb") as out:
                    out.write(data)
                entries = self.checkpoints if is_checkpoint else self.records
                entry = {"file": name, "sha256": hashlib.sha256(data).hexdigest(), "validation": validation}
                if source_file is not None:
                    entry["sourceFile"] = source_file
                entries.append(entry)
                if isinstance(record.get("engine"), str):
                    self.engines.add(record["engine"])
                if record.get("status") == "failed":
                    self.failed_records += 1

    def ingest_backend_log(self, snapshot, pid):
        """Keep the backend events (backend_log.EVENTS) of process pid from a filtered logcat snapshot.

        Only matching lines are kept, never the rest of the log."""
        if self.backend_lines is None:
            self.backend_lines = []
        seen = set(self.backend_lines)
        for line in backend_log.matching_lines(snapshot, pid):
            if line not in seen:
                seen.add(line)
                self.backend_lines.append(line)

    def finish(self, status):
        backend = None
        if self.backend_lines is not None:
            # One rule per capture: GENIE-X's when it recorded a GENIE-X session, else LiteRT-LM's.
            rule = backend_log.rule_for_engines(self.engines)
            lines = [line for line in self.backend_lines
                     if backend_log.classify(*backend_log.parse_line(line)[1:], rule=rule)]
            data = "".join(line + "\n" for line in lines).encode("utf-8")
            with (self.directory / "backend-log.txt").open("xb") as out:
                out.write(data)
            backend = {"rule": rule, "file": "backend-log.txt", "sha256": hashlib.sha256(data).hexdigest(),
                       "lines": len(lines)}
        manifest = {
            "schema": "xebench-local-capture", "schemaVersion": 1,
            "captureId": self.capture_id, "startedAt": self.started_at,
            "finishedAt": utc_now(), "status": status, "doneObserved": self.done,
            "engineErrors": self.errors, "invalidRecords": self.invalid, "rejections": self.rejections,
            "records": self.records, "checkpoints": self.checkpoints,
            "failedRecords": self.failed_records, "deviceInfo": self.device_info,
            "appIdentity": self.app_identity,
            "publicationReady": False,
        }
        if backend is not None:
            manifest["backendLog"] = backend
        if self.screen is not None:
            manifest["screenPreparation"] = self.screen
        with (self.directory / "manifest.json").open("x", encoding="utf-8") as out:
            json.dump(manifest, out, indent=2, allow_nan=False)
            out.write("\n")
        return manifest


def capture_device(serial, timeout, output_root, adb=None, monotonic=time.monotonic, sleep=time.sleep):
    deadline = monotonic() + timeout
    cancelled = None

    def check_interrupted():
        if cancelled is not None:
            raise CaptureInterrupted(cancelled)

    def command(*args):
        check_interrupted()
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("Capture deadline reached")
        if adb is not None:
            output = adb(*args)
        else:
            output = subprocess.run(
                ["adb", "-s", serial, *args], check=True, capture_output=True,
                text=True, timeout=min(remaining, 15),
            ).stdout
            if args[0] != "exec-out":
                output = output.strip()
        check_interrupted()
        return output

    def read_record_file(name):
        return command("exec-out", "head", "-c", str(MAX_RECORD_BYTES + 1),
                       f"/sdcard/Android/data/{APP}/files/xebench-results/{name}")

    capture = Capture(output_root, {})
    status, code = "setup-failed", 2
    stopped = None
    launched = False
    previous_handlers = {}

    def interrupt(signum, _frame):
        # Raise only at safe boundaries, never between writing a record and
        # registering its checksum. A running ADB call remains bounded to 15s.
        nonlocal cancelled
        cancelled = signum

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, interrupt)
        for key, prop in (("soc", "ro.soc.model"), ("manufacturer", "ro.product.manufacturer"),
                          ("device", "ro.product.device"), ("model", "ro.product.model")):
            capture.device_info[key] = command("shell", "getprop", prop)
        if not capture.device_info["soc"]:
            capture.device_info["soc"] = command("shell", "getprop", "ro.board.platform")
        command("shell", "am", "force-stop", APP)
        capture.app_identity['before'] = installed_apks(command)
        # Before every launch: wake the screen and dismiss a swipe keyguard (never a secure one).
        try:
            capture.screen = prepare_screen(command, sleep)
        except KeyguardStop as error:
            capture.screen = error.args[0]
            raise
        # Device time avoids host/device skew; PID filtering excludes other apps.
        since = command("shell", "date", "+%s.000")
        event_args = ("logcat", "-b", "events", "-d", "-v", "epoch", "-T", since,
                      "am_proc_start:I", "*:S")
        # Exclude earlier launches even when the device timestamp rounds to the
        # same second. Unavailable event logs must not prevent normal capture.
        try:
            previous_events = set(command(*event_args).splitlines())
        except subprocess.CalledProcessError:
            previous_events = None
        launched = True
        launch_failed = False
        try:
            command("shell", "am", "start", "-W", "-n", f"{APP}/.MainActivity")
        except subprocess.CalledProcessError:
            launch_failed = True
        try:
            pid = command("shell", "pidof", "-s", APP)
        except subprocess.CalledProcessError as error:
            if error.returncode != 1:
                raise
            pid = ""
        exited_at_startup = launch_failed or not pid.isdigit()
        if not pid.isdigit():
            pid = started_pid(command(*event_args), previous_events) if previous_events is not None else None
            if pid is None:
                raise ValueError("App process unavailable")
        status, code = "timeout", 3
        while monotonic() < deadline:
            if exited_at_startup:
                # A native/Java crash may precede JS and emit no XEBENCH marker.
                # Keep this PID-scoped snapshot local; never export it as evidence.
                snapshot = command("logcat", "-b", "all", "-d", "-v", "epoch", "-T", since,
                                   f"--pid={pid}", "*:V")
                with (capture.directory / "startup-logcat.txt").open("x", encoding="utf-8") as out:
                    out.write(snapshot + "\n")
                capture.ingest(snapshot, read_record_file)
                status, code = "startup-failed", 2
                break
            capture.ingest(command("logcat", "-d", "-v", "epoch", "-T", since,
                                   f"--pid={pid}", "ReactNativeJS:I", "*:S"), read_record_file)
            # The app's own LiteRT-LM, GENIE-X and FastRPC lines only (backend_log.LOGCAT_FILTER), read before
            # XEBENCH_DONE is acted on so the last repetition's load is included.
            capture.ingest_backend_log(command("logcat", "-d", "-v", "epoch", "-T", since, f"--pid={pid}",
                                               *backend_log.LOGCAT_FILTER), pid)
            check_interrupted()
            if capture.done:
                status = "complete" if capture.records and not (capture.errors or capture.invalid or capture.failed_records) else "partial"
                code = 0 if status == "complete" else 4
                after = installed_apks(command)
                capture.app_identity['after'] = after
                before = capture.app_identity['before']
                if before['status'] == after['status'] == 'recorded':
                    matched = before['apks'] == after['apks']
                    capture.app_identity['consistency'] = 'matched' if matched else 'changed'
                    if not matched:
                        status, code = 'app-changed', 4
                break
            sleep(min(1, max(0, deadline - monotonic())))
    except KeyguardStop as error:
        status, code = "setup-failed", 2
        stopped = error.args[1]
    except (TimeoutError, subprocess.TimeoutExpired):
        status, code = "timeout", 3
    except (OSError, subprocess.CalledProcessError, ValueError):
        # Do not expose commands containing the serial or unrestricted device logs.
        status, code = ("setup-failed" if status == "setup-failed" else "capture-failed"), 2
    except KeyboardInterrupt:
        status, code = "interrupted", 130
    except CaptureInterrupted as error:
        status, code = "interrupted", 128 + error.signum
    finally:
        # A capture timeout must also stop its workload. Cleanup has its own
        # bounded allowance, since the capture deadline may already be exhausted.
        # Repeated cancellation must not interrupt the bounded cleanup/manifest.
        for signum in previous_handlers:
            signal.signal(signum, signal.SIG_IGN)
        if cancelled is not None:
            status, code = "interrupted", 128 + cancelled
        try:
            if launched:
                try:
                    if adb is not None:
                        adb("shell", "am", "force-stop", APP)
                    else:
                        subprocess.run(["adb", "-s", serial, "shell", "am", "force-stop", APP],
                                       check=True, capture_output=True, timeout=5)
                except (OSError, subprocess.SubprocessError):
                    status, code = "cleanup-failed", 2
            capture.finish(status)
        finally:
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)
    if stopped is not None:
        print(f"Capture stopped: {stopped}", file=sys.stderr)
    print(f"Capture {capture.capture_id}: {status}; {len(capture.records)} record(s), "
          f"{capture.errors} engine error(s), {capture.invalid} invalid record(s).")
    print(f"Local evidence: {capture.directory}")
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("serial", help="ADB selector (not included in artifact names)")
    parser.add_argument("timeout", type=int, nargs="?", default=900)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("timeout must be a positive number of seconds")
    return capture_device(args.serial, args.timeout, ROOT / "results")


if __name__ == "__main__":
    raise SystemExit(main())
