"""
xebench native-harness shared helpers: adb wrappers + on-device probes.

Used by bench_llamacpp.py and (later) bench_executorch.py. Emits the same
EngineSessionRecord shape that scripts/aggregate.mjs consumes, so native runs
and the (archived) RN harness land in one pipeline.
"""
from __future__ import annotations
import json
import re
import shlex
import subprocess
from datetime import datetime, timezone

DEVICE_TMP = "/data/local/tmp/xebench"


def adb(args: list[str], serial: str | None = None, check: bool = True) -> str:
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    cmd += args
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"adb {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout


def adb_shell(cmd: str, serial: str | None = None, check: bool = True) -> str:
    return adb(["shell", cmd], serial=serial, check=check)


def list_devices() -> list[str]:
    out = adb(["devices"])
    devs = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if line and "\tdevice" in line:
            devs.append(line.split("\t")[0])
    return devs


def getprop(prop: str, serial: str | None = None) -> str:
    return adb_shell(f"getprop {shlex.quote(prop)}", serial=serial, check=False).strip()


def device_info(serial: str | None = None) -> dict:
    """Best-effort device descriptor. Missing props -> empty string / 0."""
    def gi(cmd: str) -> int:
        try:
            return int(adb_shell(cmd, serial=serial, check=False).strip() or 0)
        except ValueError:
            return 0

    mem_kb = 0
    meminfo = adb_shell("cat /proc/meminfo", serial=serial, check=False)
    m = re.search(r"MemTotal:\s+(\d+)\s+kB", meminfo)
    if m:
        mem_kb = int(m.group(1))

    soc = getprop("ro.soc.model", serial) or getprop("ro.board.platform", serial) or getprop("ro.hardware", serial)
    return {
        "manufacturer": getprop("ro.product.manufacturer", serial),
        "model": getprop("ro.product.model", serial),
        "device": getprop("ro.product.device", serial),
        "soc": soc,
        "androidSdk": gi("getprop ro.build.version.sdk"),
        "androidRelease": getprop("ro.build.version.release", serial),
        "cpuCores": gi("nproc"),
        "totalMemMb": round(mem_kb / 1024) if mem_kb else 0,
    }


def thermal_snapshot(serial: str | None = None) -> dict:
    """Thermal + battery + power state. Every field degrades to null on absence."""
    ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    # thermal status: `cmd thermalservice` on API 29+; categorical 0..6
    thermal_status = -1
    tstat = adb_shell("cmd thermalservice", serial=serial, check=False)
    ms = re.search(r"Thermal Status:\s*(\d+)", tstat)
    if ms:
        thermal_status = int(ms.group(1))

    # thermal headroom (float, 1.0 ~ severe throttle). API 30+.
    headroom = None
    # (no stable shell command; left null in native harness — app path had it)

    # battery via dumpsys battery
    batt = adb_shell("dumpsys battery", serial=serial, check=False)
    def bfield(key: str):
        m = re.search(rf"{key}:\s*(-?\d+)", batt)
        return int(m.group(1)) if m else None
    temp_raw = bfield("temperature")  # tenths of a degree C
    batt_temp = temp_raw / 10.0 if temp_raw is not None else None
    level = bfield("level")
    scale = bfield("scale") or 100
    batt_pct = round(100 * level / scale) if level is not None else 0
    status = bfield("status")  # 2=charging,5=full
    ac = bfield("AC powered") or 0
    usb = bfield("USB powered") or 0
    charging = bool(status in (2, 5) or ac or usb)

    lp = adb_shell("settings get global low_power", serial=serial, check=False).strip()
    power_save = lp == "1"

    return {
        "thermalStatus": thermal_status,
        "thermalHeadroom": headroom,
        "batteryTempC": batt_temp,
        "batteryPct": batt_pct,
        "charging": charging,
        "powerSaveMode": power_save,
        "timestampMs": ts_ms,
    }


def push(local: str, serial: str | None = None) -> None:
    adb(["push", local, DEVICE_TMP + "/"], serial=serial)


def ensure_dir(serial: str | None = None) -> None:
    adb_shell(f"mkdir -p {DEVICE_TMP}", serial=serial, check=False)


def device_file_size_mb(remote: str, serial: str | None = None) -> float | None:
    out = adb_shell(f"stat -c %s {shlex.quote(remote)}", serial=serial, check=False).strip()
    try:
        return round(int(out) / (1024 * 1024), 1)
    except ValueError:
        return None


CANONICAL_MODELS = [
    ("Llama 3.2 1B", re.compile(r"llama[-_. ]?3\.2[-_. ]?1b", re.I)),
    ("Llama 3.2 3B", re.compile(r"llama[-_. ]?3\.2[-_. ]?3b", re.I)),
    ("Gemma 3 1B", re.compile(r"gemma[-_. ]?3[-_. ]?1b", re.I)),
    ("Qwen 2.5 1.5B", re.compile(r"qwen[-_. ]?2\.5[-_. ]?1\.5b", re.I)),
    ("Qwen 3 1.7B", re.compile(r"qwen[-_. ]?3[-_. ]?1\.7b", re.I)),
]
QUANT_RE = re.compile(r"(q[0-9]_[k0-9](?:_[msl])?|iq[0-9]_\w+|f16|bf16|q8_0|q4_0)", re.I)


def canonical_model(filename: str) -> str | None:
    for label, pat in CANONICAL_MODELS:
        if pat.search(filename):
            return label
    return None


def extract_quant(filename: str) -> str | None:
    m = QUANT_RE.search(filename)
    return m.group(1).upper() if m else None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
