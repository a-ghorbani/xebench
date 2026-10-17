#!/usr/bin/env python3
"""Drive the charging A/B experiment on one tethered phone (docs/experiments/charging-ab.md).

EXPERIMENT ONLY. Alternates blocks of condition A (plugged, batteryStatus
CHARGING) and B (plugged, FULL or NOT_CHARGING) by switching the device's charge
limit / battery-protection setting with operator-supplied ``adb shell`` commands,
waits until Android reports the target status, then runs one capture of the
experiment configuration (xebenchapp/configs/experiment-charging-ab-v1.json)
through scripts/capture_app_run.py.

It never fakes an unplug (``dumpsys battery unplug``/``set`` are refused), never
reboots and only touches com.xebenchapp. The original setting value and any
staged app configuration are recorded first and restored in ``finally``.
Evidence goes to results/charging-ab/<label>/ (gitignored); the serial is not
written to any file.

Example (setting commands are device-specific, see the pre-registration)::

    charging_ab_run.py SERIAL --label s23 --start A \\
        --read  'settings get global protect_battery' \\
        --set-a 'settings put global protect_battery 0' \\
        --set-b 'settings put global protect_battery 1' \\
        --restore 'settings put global protect_battery {original}'
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import capture_app_run  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
import charging_ab_analyze  # noqa: E402

APP_DIR = "/sdcard/Android/data/com.xebenchapp/files"
CONFIG = ROOT / "xebenchapp/configs/experiment-charging-ab-v1.json"
TARGET = {"A": {2}, "B": {4, 5}}
FORBIDDEN = re.compile(r"\b(reboot|uninstall|pm\s+clear|unplug|battery\s+(set|reset|unplug))\b|com\.pocketpal")
# Pre-registered stopping rules.
MIN_PER_CONDITION, TARGET_PER_CONDITION, MAX_PAIRS = 10, 15, 8
STATE_POLL_S, STATE_CONFIRM_POLLS, STATE_TIMEOUT_S = 10, 3, 600
# Amendment 1: a 3-repetition block can wait up to 3 x 300 s for readiness.
CAPTURE_TIMEOUT_S, BLOCK_RESERVE_S, MIN_BATTERY_PCT = 1500, 12 * 60, 55


def utc():
    return datetime.now(timezone.utc).isoformat()


class Device:
    def __init__(self, serial):
        self.serial = serial

    def adb(self, *args, timeout=30, check=True):
        return subprocess.run(["adb", "-s", self.serial, *args], capture_output=True, text=True,
                              timeout=timeout, check=check).stdout.strip()

    def shell(self, command):
        if FORBIDDEN.search(command):
            raise SystemExit(f"Refusing forbidden device command: {command}")
        # One argument: the device shell parses it, so a UI sequence ("a; sleep 2; b") works.
        return self.adb("shell", command)

    def battery(self):
        """Read-only `dumpsys battery` snapshot (status, level, temperature, plug, current)."""
        text = self.adb("shell", "dumpsys", "battery")
        values = {}
        for key, name in (("status", "status"), ("level", "level"), ("temperature", "temperature"),
                          ("AC powered", "ac"), ("USB powered", "usb"), ("Wireless powered", "wireless"),
                          ("Max charging current", "maxChargingCurrent"), ("current now", "currentNow")):
            match = re.search(rf"^\s*{re.escape(key)}:\s*(\S+)", text, re.M | re.I)
            values[name] = match[1] if match else None
        values["readAt"] = utc()
        return values


def wait_for(device, condition, log):
    """Poll until the target status is seen on consecutive polls; keep the screen awake."""
    started, streak, last = time.monotonic(), 0, None
    while time.monotonic() - started < STATE_TIMEOUT_S:
        device.adb("shell", "input", "keyevent", "KEYCODE_WAKEUP", check=False)
        last = device.battery()
        status = int(last["status"]) if (last["status"] or "").isdigit() else None
        streak = streak + 1 if status in TARGET[condition] else 0
        log(f"  wait {condition}: status={status} level={last['level']} temp={last['temperature']} streak={streak}")
        if streak >= STATE_CONFIRM_POLLS:
            return True, last, time.monotonic() - started
        time.sleep(STATE_POLL_S)
    return False, last, time.monotonic() - started


def counts(directory):
    devices = charging_ab_analyze.load([directory])
    labels = {"A": 0, "B": 0}
    for entry in devices.values():
        for rep in entry["reps"]:
            if rep["label"] in labels:
                labels[rep["label"]] += 1
    return labels


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("serial", help="ADB selector (never written to evidence)")
    parser.add_argument("--label", required=True, help="short device label for the evidence folder")
    parser.add_argument("--start", choices=("A", "B"), required=True)
    parser.add_argument("--read", required=True, help="adb shell command that prints the setting value")
    parser.add_argument("--set-a", required=True, help="adb shell command that allows charging (A)")
    parser.add_argument("--set-b", required=True, help="adb shell command that enables the charge limit (B)")
    parser.add_argument("--restore", required=True, help="adb shell command; {original} is the value read first")
    parser.add_argument("--budget-min", type=float, default=170, help="device-time budget (pre-registered ≤ 180)")
    parser.add_argument("--max-pairs", type=int, default=MAX_PAIRS)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[a-z0-9-]{1,32}", args.label):
        parser.error("label must be short lowercase [a-z0-9-]")
    for command in (args.read, args.set_a, args.set_b, args.restore):
        if FORBIDDEN.search(command):
            parser.error(f"forbidden command: {command}")

    out = ROOT / "results" / "charging-ab" / args.label
    captures = out / "captures"
    captures.mkdir(parents=True, exist_ok=True)
    blocks_file, log_file = out / "blocks.jsonl", out / "driver.log"
    device = Device(args.serial)
    started = time.monotonic()

    def log(message):
        line = f"{utc()} {message}"
        print(line, flush=True)
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def write(kind, **fields):
        with blocks_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"kind": kind, "at": utc(), **fields}) + "\n")

    original = device.shell(args.read)
    staged = device.adb("shell", "ls", f"{APP_DIR}/xebench-config.json", check=False)
    backup = None
    if staged.endswith("xebench-config.json") and "No such file" not in staged:
        backup = device.adb("exec-out", "cat", f"{APP_DIR}/xebench-config.json")
        (out / f"config-backup-{int(time.time())}.json").write_text(backup, encoding="utf-8")
    write("start", label=args.label, settingOriginal=original, read=args.read, setA=args.set_a, setB=args.set_b,
          restore=args.restore, configBackedUp=backup is not None, battery=device.battery(),
          model=device.adb("shell", "getprop", "ro.product.model"), budgetMin=args.budget_min)
    log(f"setting original value: {original!r}; staged config present: {backup is not None}")
    try:
        device.adb("push", str(CONFIG), f"{APP_DIR}/xebench-config.json")
        order = ["A", "B"] if args.start == "A" else ["B", "A"]
        failures = {"A": 0, "B": 0}
        stop = "max-pairs"
        for block in range(2 * args.max_pairs):
            condition = order[block % 2]
            elapsed = time.monotonic() - started
            if elapsed + BLOCK_RESERVE_S > args.budget_min * 60:
                stop = "time-budget"
                break
            battery = device.battery()
            if battery["level"] and battery["level"].isdigit() and int(battery["level"]) < MIN_BATTERY_PCT:
                stop = "battery-below-floor"
                break
            command = args.set_a if condition == "A" else args.set_b
            device.shell(command)
            setting = device.shell(args.read)
            log(f"block {block} {condition}: set -> {setting!r}")
            reached, state, waited = wait_for(device, condition, log)
            if not reached:
                failures[condition] += 1
                write("block", block=block, condition=condition, setting=setting, stateReached=False,
                      stateWaitS=waited, battery=state, captureId=None)
                if failures[condition] >= 2:
                    stop = f"state-{condition}-unreachable"
                    break
                continue
            failures[condition] = 0
            before = set(path.name for path in captures.iterdir())
            block_started = utc()
            code = capture_app_run.capture_device(args.serial, CAPTURE_TIMEOUT_S, captures)
            new = sorted(set(path.name for path in captures.iterdir()) - before)
            write("block", block=block, condition=condition, setting=setting, stateReached=True,
                  stateWaitS=waited, batteryBefore=state, batteryAfter=device.battery(), startedAt=block_started,
                  captureExit=code, captureId=new[0] if len(new) == 1 else None)
            seen = counts(captures)
            log(f"block {block} {condition}: capture exit {code}; pure-label reps so far {seen}")
            if block % 2 == 1 and min(seen.values()) >= TARGET_PER_CONDITION:
                stop = "target-reached"
                break
        log(f"stopped: {stop}")
        write("stop", reason=stop, counts=counts(captures), elapsedMin=(time.monotonic() - started) / 60)
    finally:
        restore = args.restore.replace("{original}", original)
        try:
            device.shell(restore)
            after = device.shell(args.read)
        except (subprocess.SubprocessError, OSError) as error:
            after = f"restore failed: {type(error).__name__}"
        try:
            if backup is not None:
                temp = out / "config-restore.json"
                temp.write_text(backup, encoding="utf-8")
                device.adb("push", str(temp), f"{APP_DIR}/xebench-config.json")
                temp.unlink()
            else:
                device.adb("shell", "rm", "-f", f"{APP_DIR}/xebench-config.json")
            config_restored = True
        except (subprocess.SubprocessError, OSError):
            config_restored = False
        write("restore", settingOriginal=original, settingAfterRestore=after, restored=after == original,
              configRestored=config_restored, battery=device.battery())
        log(f"restored setting: {after!r} (original {original!r}); config restored: {config_restored}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
