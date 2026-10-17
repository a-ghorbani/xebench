#!/usr/bin/env python3
"""Analyse the charging A/B experiment (docs/experiments/charging-ab.md). Stdlib only.

Reads local capture directories (``manifest.json`` + ``raw-*.jsonl`` written by
scripts/capture_app_run.py), optionally with the driver's ``blocks.jsonl``, and
compares, per device, plugged-and-charging (A) with plugged-and-not-charging (B)
repetitions. The condition label of a repetition is the *observed* Android
battery status at every endpoint and persisted 1 Hz sample (pre-registered):

- A: every observation has batteryStatus CHARGING (2);
- B: every observation has batteryStatus FULL (5) or NOT_CHARGING (4);
- anything else (mixed, DISCHARGING, unknown, or unplugged) is excluded from the
  primary comparison and only counted.

``currentNowUa`` is reported as a raw diagnostic only: its unit and sign are
vendor-defined.

Usage::

    charging_ab_analyze.py DIR [DIR...] [--json OUT] [--seed N] [--resamples N]

Each DIR is either one capture directory or a directory containing capture
directories (searched recursively). Prints Markdown tables to stdout.
"""
import argparse
import json
import math
from pathlib import Path
import random
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import raw_session  # noqa: E402

# Pre-registered (docs/experiments/charging-ab.md); do not change after data collection.
THRESHOLD_PCT = 3.0
SEED = 20261017
RESAMPLES = 10000
METRICS = ("decodeTps", "prefillTps", "ttftMs", "loadMs")
PRIMARY = ("decodeTps", "prefillTps")
MIN_PER_CONDITION = 10  # below this a device result is descriptive only
CHARGING, NOT_CHARGING = {2}, {4, 5}
DIAGNOSTICS = ("beforeTempC", "afterTempC", "maxTempC", "tempRiseC", "maxThermalStatus", "maxHeadroom",
               "medianCurrentRaw", "readinessWaitS")


def _records(directory):
    """Yield (captureId, deviceModel, record, file) for valid terminal records under directory."""
    for manifest_path in sorted(Path(directory).rglob("manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema") != "xebench-local-capture":
            continue
        device = (manifest.get("deviceInfo") or {}).get("model") or "unknown"
        for entry in manifest.get("records", []):
            path = manifest_path.parent / entry["file"]
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    yield manifest.get("captureId"), device, line, path.name


def label(conditions):
    """A, B or the reason a measured repetition is excluded from the primary comparison."""
    during = conditions.get("during") or {}
    if during.get("status") != "complete":
        return "excluded:unsampled"
    summary = during["summary"]
    observations = [conditions["before"], conditions["after"], *during.get("samples", [])]
    statuses = [item.get("batteryStatus") for item in observations]
    if any((item.get("plugged") or 0) == 0 for item in observations):
        return "excluded:unplugged-or-unknown-plug"
    states = summary["chargingStates"]
    # charging-policy-v3 counts every sample (persisted or not) by status: charging is 2,
    # notCharging is exactly {4, 5}; endpoints and persisted samples are checked raw.
    if all(status in CHARGING for status in statuses) and states.get("charging") == summary["samples"]:
        return "A"
    if all(status in NOT_CHARGING for status in statuses) and states.get("notCharging") == summary["samples"]:
        return "B"
    return "excluded:mixed-or-other-status"


def _max(values):
    known = [value for value in values if value is not None]
    return max(known) if known else None


def repetition(attempt):
    conditions = attempt["conditions"]
    before, after, during = conditions["before"], conditions["after"], conditions["during"]
    summary = during["summary"] if during else {}
    samples = during.get("samples", []) if during else []
    currents = [sample["currentNowUa"] for sample in samples if sample.get("currentNowUa") is not None]
    before_temp, after_temp = before.get("batteryTempC"), after.get("batteryTempC") if after else None
    return {
        "label": label(conditions), "runIndex": attempt["runIndex"],
        **{metric: attempt[metric] for metric in METRICS},
        "beforeTempC": before_temp, "afterTempC": after_temp,
        "maxTempC": _max([before_temp, after_temp, summary.get("maxBatteryTempC")]),
        "tempRiseC": None if before_temp is None or after_temp is None else after_temp - before_temp,
        "maxThermalStatus": _max([before.get("thermalStatus"), (after or {}).get("thermalStatus"),
                                  summary.get("maxThermalStatus")]),
        "maxHeadroom": _max([before.get("thermalHeadroom"), (after or {}).get("thermalHeadroom"),
                             summary.get("maxThermalHeadroom")]),
        "medianCurrentRaw": statistics.median(currents) if currents else None,
        "readinessWaitS": attempt["readiness"]["waitedMs"] / 1000,
        "batteryPctBefore": before.get("batteryPct"),
        "statuses": sorted({item.get("batteryStatus") for item in [before, after or {}, *samples]}, key=str),
        "timestampMs": before.get("timestampMs"),
    }


def load(directories):
    """Per-device repetitions, skipped attempts and rejected records."""
    devices, seen = {}, set()
    for directory in directories:
        for capture_id, device, text, _name in _records(directory):
            reasons = raw_session.validate_text(text)
            entry = devices.setdefault(device, {"reps": [], "skipped": [], "invalid": 0, "failed": 0})
            if reasons:
                entry["invalid"] += 1
                continue
            record = json.loads(text)
            if record["runId"] in seen:
                continue
            seen.add(record["runId"])
            if record.get("configuration", {}).get("experimentChargingReadiness") != "record-only":
                # Only experiment records are analysed; never mix reference sessions in.
                continue
            for attempt in record["coldRuns"]:
                if attempt["status"] == "complete":
                    rep = repetition(attempt)
                    rep["captureId"], rep["runId"] = capture_id, record["runId"]
                    entry["reps"].append(rep)
                elif attempt["status"] == "skipped":
                    entry["skipped"].append({"captureId": capture_id, "reason": attempt["reason"],
                                             "notReady": attempt["readiness"]["notReady"],
                                             "label": "A" if attempt["conditions"]["before"].get("batteryStatus") in CHARGING
                                             else "B" if attempt["conditions"]["before"].get("batteryStatus") in NOT_CHARGING
                                             else "other"})
                else:
                    entry["failed"] += 1
    return devices


def blocks(directories):
    """Driver block metadata keyed by capture ID (planned condition, order)."""
    result = {}
    for directory in directories:
        for path in Path(directory).rglob("blocks.jsonl"):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    block = json.loads(line)
                    if block.get("captureId"):
                        result[block["captureId"]] = block
    return result


def _percentile(sorted_values, p):
    """Linear interpolation at (n-1)*p, as in the app's summaries."""
    index = (len(sorted_values) - 1) * p
    low = math.floor(index)
    high = math.ceil(index)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (index - low)


def bootstrap(a, b, statistic, rng, resamples):
    values = []
    for _ in range(resamples):
        value = statistic([rng.choice(a) for _ in a], [rng.choice(b) for _ in b])
        if value is not None:
            values.append(value)
    values.sort()
    return (_percentile(values, 0.025), _percentile(values, 0.975)) if values else (None, None)


def delta_pct(a, b):
    median_a = statistics.median(a)
    return None if median_a == 0 else 100 * (statistics.median(b) - median_a) / median_a


def delta_abs(a, b):
    return statistics.median(b) - statistics.median(a)


def variance_ratio(a, b):
    var_a = statistics.variance(a)
    return None if var_a == 0 else statistics.variance(b) / var_a


def verdict(low, high, threshold=THRESHOLD_PCT):
    """Pre-registered equivalence-style decision on the 95% CI of the median difference (%)."""
    if low is None or high is None:
        return "insufficient data"
    if -threshold < low and high < threshold:
        return "irrelevant (CI within ±%g%%)" % threshold
    if low >= threshold or high <= -threshold:
        return "relevant (CI beyond ±%g%%)" % threshold
    return "inconclusive"


def compare(reps, seed=SEED, resamples=RESAMPLES, min_n=MIN_PER_CONDITION):
    a = [rep for rep in reps if rep["label"] == "A"]
    b = [rep for rep in reps if rep["label"] == "B"]
    result = {"nA": len(a), "nB": len(b), "metrics": {}, "diagnostics": {}}
    for index, metric in enumerate(METRICS):
        xa, xb = [rep[metric] for rep in a], [rep[metric] for rep in b]
        if len(xa) < 2 or len(xb) < 2:
            result["metrics"][metric] = {"verdict": "insufficient data"}
            continue
        # Independent, reproducible streams per metric.
        rng = random.Random(seed + index)
        low, high = bootstrap(xa, xb, delta_pct, rng, resamples)
        vlow, vhigh = bootstrap(xa, xb, variance_ratio, random.Random(seed + 100 + index), resamples)
        result["metrics"][metric] = {
            "medianA": statistics.median(xa), "medianB": statistics.median(xb),
            "deltaPct": delta_pct(xa, xb), "ci95": [low, high],
            "varianceRatioBA": variance_ratio(xa, xb), "varianceRatioCi95": [vlow, vhigh],
            "iqrPctA": 100 * (_percentile(sorted(xa), .75) - _percentile(sorted(xa), .25)) / statistics.median(xa),
            "iqrPctB": 100 * (_percentile(sorted(xb), .75) - _percentile(sorted(xb), .25)) / statistics.median(xb),
            "verdict": verdict(low, high) if min(len(xa), len(xb)) >= min_n else
            f"descriptive only (n<{min_n})",
        }
    for index, name in enumerate(DIAGNOSTICS):
        xa = [rep[name] for rep in a if rep[name] is not None]
        xb = [rep[name] for rep in b if rep[name] is not None]
        if len(xa) < 2 or len(xb) < 2:
            result["diagnostics"][name] = None
            continue
        low, high = bootstrap(xa, xb, delta_abs, random.Random(seed + 200 + index), resamples)
        result["diagnostics"][name] = {"medianA": statistics.median(xa), "medianB": statistics.median(xb),
                                       "deltaAbs": delta_abs(xa, xb), "ci95": [low, high], "nA": len(xa), "nB": len(xb)}
    result["thermalStatusCounts"] = {
        cond: {str(k): sum(1 for rep in group if rep["maxThermalStatus"] == k)
               for k in sorted({rep["maxThermalStatus"] for rep in group}, key=str)}
        for cond, group in (("A", a), ("B", b))}
    return result


def _fmt(value, digits=2):
    if value is None:
        return "–"
    return f"{value:.{digits}f}"


def report(devices, plan, seed=SEED, resamples=RESAMPLES):
    lines, summary = [], {}
    for device, entry in sorted(devices.items()):
        reps = sorted(entry["reps"], key=lambda rep: rep["timestampMs"] or 0)
        for rep in reps:
            rep["planned"] = (plan.get(rep["captureId"]) or {}).get("condition")
            rep["block"] = (plan.get(rep["captureId"]) or {}).get("block")
        result = compare(reps, seed, resamples)
        labels = {}
        for rep in reps:
            labels[rep["label"]] = labels.get(rep["label"], 0) + 1
        mismatched = sum(1 for rep in reps if rep["planned"] in ("A", "B") and rep["label"] in ("A", "B")
                         and rep["planned"] != rep["label"])
        result.update(labels=labels, plannedLabelMismatch=mismatched, skipped=entry["skipped"],
                      invalidRecords=entry["invalid"], failedAttempts=entry["failed"])
        summary[device] = result
        lines += [f"### {device}", "",
                  f"Pure-label repetitions: A (charging) n={result['nA']}, B (not charging) n={result['nB']}. "
                  f"Labels: {labels}. Planned≠observed: {mismatched}. Skipped attempts: {len(entry['skipped'])} "
                  f"({', '.join(sorted({s['reason'] + '/' + s['label'] for s in entry['skipped']})) or 'none'}). "
                  f"Failed attempts: {entry['failed']}. Rejected records: {entry['invalid']}.", "",
                  "| Metric | median A | median B | Δ (B−A)/A | 95% CI | IQR% A / B | var B/A [95% CI] | Verdict |",
                  "| --- | ---: | ---: | ---: | --- | --- | --- | --- |"]
        for metric in METRICS:
            item = result["metrics"][metric]
            if "medianA" not in item:
                lines.append(f"| {metric} | – | – | – | – | – | – | {item['verdict']} |")
                continue
            lines.append(
                f"| {metric}{' (primary)' if metric in PRIMARY else ''} | {_fmt(item['medianA'])} | "
                f"{_fmt(item['medianB'])} | {item['deltaPct']:+.2f}% | "
                f"[{item['ci95'][0]:+.2f}%, {item['ci95'][1]:+.2f}%] | {_fmt(item['iqrPctA'], 1)} / "
                f"{_fmt(item['iqrPctB'], 1)} | {_fmt(item['varianceRatioBA'])} "
                f"[{_fmt(item['varianceRatioCi95'][0])}, {_fmt(item['varianceRatioCi95'][1])}] | {item['verdict']} |")
        lines += ["", "| Diagnostic | median A | median B | Δ (B−A) | 95% CI |", "| --- | ---: | ---: | ---: | --- |"]
        for name in DIAGNOSTICS:
            item = result["diagnostics"][name]
            if item is None:
                lines.append(f"| {name} | – | – | – | – |")
                continue
            lines.append(f"| {name} | {_fmt(item['medianA'])} | {_fmt(item['medianB'])} | "
                         f"{item['deltaAbs']:+.2f} | [{_fmt(item['ci95'][0])}, {_fmt(item['ci95'][1])}] |")
        lines += ["", f"Max thermal status per repetition (counts): {result['thermalStatusCounts']}", "",
                  "| # | block | planned | observed | decode tok/s | prefill tok/s | TTFT ms | load ms | "
                  "temp before→after °C | max status | max headroom | battery % | statuses | current (raw) |",
                  "| ---: | ---: | --- | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- | ---: |"]
        for number, rep in enumerate(reps, 1):
            lines.append(
                f"| {number} | {rep['block'] if rep['block'] is not None else '–'} | {rep['planned'] or '–'} | "
                f"{rep['label']} | {rep['decodeTps']:.2f} | {rep['prefillTps']:.2f} | {rep['ttftMs']:.0f} | "
                f"{rep['loadMs']:.0f} | {_fmt(rep['beforeTempC'], 1)}→{_fmt(rep['afterTempC'], 1)} | "
                f"{rep['maxThermalStatus']} | {_fmt(rep['maxHeadroom'])} | {_fmt(rep['batteryPctBefore'], 0)} | "
                f"{rep['statuses']} | {_fmt(rep['medianCurrentRaw'], 0)} |")
        lines.append("")
    return "\n".join(lines), summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("directories", nargs="+")
    parser.add_argument("--json", help="also write the per-device summary as JSON")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--resamples", type=int, default=RESAMPLES)
    args = parser.parse_args(argv)
    text, summary = report(load(args.directories), blocks(args.directories), args.seed, args.resamples)
    print(text)
    if args.json:
        Path(args.json).write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return 0 if summary else 1


if __name__ == "__main__":
    raise SystemExit(main())
