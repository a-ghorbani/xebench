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
import time
import uuid

APP = "com.xebenchapp"
ROOT = Path(__file__).resolve().parents[1]


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
        self.errors = 0
        self.invalid = 0
        self.done = False

    def ingest(self, snapshot):
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
            elif message.startswith("XEBENCH_RESULT "):
                payload = message[len("XEBENCH_RESULT "):].strip()
                try:
                    record = strict_json(payload)
                    # JSON exponents can overflow even without literal NaN/Infinity.
                    json.dumps(record, allow_nan=False)
                    if not isinstance(record, dict) or record.get("schema") != "xebench-raw":
                        raise ValueError("Unexpected schema")
                    for field in ("engine", "backend", "model", "quant"):
                        if not isinstance(record.get(field), str) or not record[field]:
                            raise ValueError(f"Missing {field}")
                    if not isinstance(record.get("deviceInfo", {}), dict):
                        raise ValueError("Invalid deviceInfo")
                except (ValueError, TypeError):
                    self.invalid += 1
                    with (self.directory / f"invalid-{uuid.uuid4()}.txt").open("x", encoding="utf-8") as out:
                        out.write(payload + "\n")
                    continue
                device = record.setdefault("deviceInfo", {})
                for key, value in self.device_info.items():
                    if not device.get(key):
                        device[key] = value
                name = f"raw-{uuid.uuid4()}.jsonl"
                data = (json.dumps(record, allow_nan=False) + "\n").encode("utf-8")
                with (self.directory / name).open("xb") as out:
                    out.write(data)
                self.records.append({"file": name, "sha256": hashlib.sha256(data).hexdigest()})

    def finish(self, status):
        manifest = {
            "schema": "xebench-local-capture", "schemaVersion": 1,
            "captureId": self.capture_id, "startedAt": self.started_at,
            "finishedAt": utc_now(), "status": status, "doneObserved": self.done,
            "engineErrors": self.errors, "invalidRecords": self.invalid,
            "records": self.records, "publicationReady": False,
        }
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
            ).stdout.strip()
        check_interrupted()
        return output

    capture = Capture(output_root, {})
    status, code = "setup-failed", 2
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
        command("shell", "input", "keyevent", "KEYCODE_WAKEUP")
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
                capture.ingest(snapshot)
                status, code = "startup-failed", 2
                break
            capture.ingest(command("logcat", "-d", "-v", "epoch", "-T", since,
                                   f"--pid={pid}", "ReactNativeJS:I", "*:S"))
            check_interrupted()
            if capture.done:
                status = "complete" if capture.records and not (capture.errors or capture.invalid) else "partial"
                code = 0 if status == "complete" else 4
                break
            sleep(min(1, max(0, deadline - monotonic())))
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
