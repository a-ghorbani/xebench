#!/usr/bin/env python3
"""
bench_llamacpp.py — run llama-bench (native aarch64) on a device over adb and
emit one xebench-raw EngineSessionRecord JSONL line.

llama.cpp is the "gold standard" leg: llama-bench reports pp/tg throughput from
the engine's own in-process timers (mean +/- stddev over -r reps). No JS bridge,
no app layer. This is the community-standard tool, so numbers are directly
disputable/reproducible by anyone.

Usage (real device):
  python3 scripts/bench_llamacpp.py \
      --bin-dir build/llama-android/bin \
      --model /path/to/Llama-3.2-1B-Instruct-Q4_K_M.gguf \
      --pp 512 --tg 128 --reps 3 --outdir results

Offline self-test (no device, validate record assembly):
  python3 scripts/bench_llamacpp.py --dry-run-from tests/sample-llama-bench.json \
      --model Llama-3.2-1B-Instruct-Q4_K_M.gguf --outdir /tmp
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xebench_common as xc  # noqa: E402

HARNESS_VERSION = "xebench-0.2-native"
BUNDLE_LIBS = [
    "libggml-base.so", "libggml-cpu.so", "libggml.so",
    "libllama.so", "libllama-common.so", "libllama-bench-impl.so",
]


def parse_llama_bench_json(data: list[dict]) -> dict:
    """Pull pp/tg throughput + provenance from llama-bench -o json output."""
    pp = tg = None
    for e in data:
        n_prompt = int(e.get("n_prompt", 0))
        n_gen = int(e.get("n_gen", 0))
        if n_prompt > 0 and n_gen == 0:
            pp = e
        elif n_gen > 0 and n_prompt == 0:
            tg = e
    if pp is None or tg is None:
        raise RuntimeError(
            "llama-bench output missing a pp-only or tg-only row; "
            f"got {len(data)} rows"
        )
    ref = pp
    return {
        "pp": pp, "tg": tg,
        "build_commit": ref.get("build_commit"),
        "n_threads": ref.get("n_threads"),
        "n_batch": ref.get("n_batch"),
        "n_gpu_layers": ref.get("n_gpu_layers", 0),
        "model_size": ref.get("model_size"),
        "model_n_params": ref.get("model_n_params"),
        "cpu_info": ref.get("cpu_info"),
    }


def agg(avg: float | None, stddev: float | None, reps: int) -> dict:
    """llama-bench gives mean+/-stddev over reps, not a median. Represent
    honestly: put the mean in `median` (aggregate.mjs reads that) and keep the
    true mean/stddev alongside so nothing is laundered."""
    v = None if avg is None else round(avg, 2)
    return {
        "median": v, "mean": v, "stddev": None if stddev is None else round(stddev, 2),
        "iqr": None, "min": v, "max": v, "n": reps,
        "_note": "llama-bench mean of reps, not median",
    }


def run_on_device(args) -> list[dict]:
    serial = args.serial
    devs = xc.list_devices()
    if not devs:
        raise SystemExit("no adb device connected — plug in a phone, or use --dry-run-from")
    if serial is None and len(devs) == 1:
        serial = devs[0]

    xc.ensure_dir(serial)
    print(f"[push] binary + {len(BUNDLE_LIBS)} libs -> {xc.DEVICE_TMP}", file=sys.stderr)
    xc.push(os.path.join(args.bin_dir, "llama-bench"), serial)
    for lib in BUNDLE_LIBS:
        xc.push(os.path.join(args.bin_dir, lib), serial)
    xc.adb_shell(f"chmod 755 {xc.DEVICE_TMP}/llama-bench", serial, check=False)

    model_name = os.path.basename(args.model)
    remote_model = f"{xc.DEVICE_TMP}/{model_name}"
    exists = xc.adb_shell(f"[ -f {remote_model} ] && echo Y || echo N", serial, check=False).strip()
    if exists != "Y":
        print(f"[push] model {model_name} ({os.path.getsize(args.model)//(1024*1024)} MB)", file=sys.stderr)
        xc.push(args.model, serial)

    print("[run] llama-bench", file=sys.stderr)
    cmd = (
        f"cd {xc.DEVICE_TMP} && LD_LIBRARY_PATH=. ./llama-bench "
        f"-m {model_name} -p {args.pp} -n {args.tg} -r {args.reps} -o json"
    )
    out = xc.adb_shell(cmd, serial)
    # llama-bench prints the json array; strip any leading warmup log noise
    start = out.find("[")
    data = json.loads(out[start:])
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin-dir", default="build/llama-android/bin")
    ap.add_argument("--model", required=True, help="host path (real run) or filename (dry-run)")
    ap.add_argument("--model-label", default=None)
    ap.add_argument("--quant", default=None)
    ap.add_argument("--serial", default=None)
    ap.add_argument("--pp", type=int, default=512)
    ap.add_argument("--tg", type=int, default=128)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--outdir", default="results")
    ap.add_argument("--dry-run-from", default=None, help="parse this llama-bench json instead of running a device")
    args = ap.parse_args()

    if args.dry_run_from:
        with open(args.dry_run_from) as f:
            data = json.load(f)
        dev_info = {
            "manufacturer": "DRY", "model": "RUN", "device": "dryrun",
            "soc": "unknown", "androidSdk": 0, "androidRelease": "",
            "cpuCores": 0, "totalMemMb": 0,
        }
        thermal = {"thermalStatus": -1, "thermalHeadroom": None, "batteryTempC": None,
                   "batteryPct": 0, "charging": False, "powerSaveMode": False, "timestampMs": 0}
        model_size_mb = None
    else:
        data = run_on_device(args)
        dev_info = xc.device_info(args.serial)
        thermal = xc.thermal_snapshot(args.serial)
        model_size_mb = xc.device_file_size_mb(
            f"{xc.DEVICE_TMP}/{os.path.basename(args.model)}", args.serial)

    parsed = parse_llama_bench_json(data)
    model_file = os.path.basename(args.model)
    model_label = args.model_label or xc.canonical_model(model_file) or model_file
    quant = args.quant or xc.extract_quant(model_file)

    pp, tg = parsed["pp"], parsed["tg"]
    record = {
        "schema": "xebench-raw",
        "harnessVersion": HARNESS_VERSION,
        "runId": uuid.uuid4().hex[:12],
        "timestampIso": xc.now_iso(),
        "engine": "llama.cpp",
        "engineVersion": f"llama-bench (llama.cpp {parsed['build_commit']})",
        "backend": "cpu",
        "platform": "android",
        "deviceInfo": dev_info,
        "model": model_label,
        "quant": quant,
        "modelFile": model_file,
        "modelFileSizeMb": model_size_mb,
        "protocol": {
            "nColdRuns": args.reps,
            "promptLabel": f"llama-bench pp{args.pp}",
            "promptChars": None,
            "maxDecodeTokens": args.tg,
            "cooldownSec": 0,
            "sustainedTargetSec": 0,
            "nThreads": parsed["n_threads"],
            "nCtx": None,
            "coldDefinition": "llama-bench internal warmup + reps (not app cold-start)",
        },
        "coldRuns": [{"runIndex": 0, "prefillMethod": "engine-timings", "decodeMethod": "engine-timings"}],
        "summary": {
            "ttftMs": agg(None, None, args.reps),  # llama-bench separates pp/tg; no TTFT
            "prefillTps": agg(pp.get("avg_ts"), pp.get("stddev_ts"), args.reps),
            "decodeTps": agg(tg.get("avg_ts"), tg.get("stddev_ts"), args.reps),
            "peakPssMb": agg(None, None, args.reps),  # v1.1: RSS sampler
            "deltaPssMb": agg(None, None, args.reps),
            "loadMs": agg(None, None, args.reps),
        },
        "sustained": None,
        "processReused": False,
        "guardsPassed": thermal["batteryPct"] >= 50 and not thermal["powerSaveMode"] if not args.dry_run_from else True,
        "guardNotes": [],
        "thermalBefore": thermal,
        "rawEngineOutput": data,
    }
    if not args.dry_run_from:
        if thermal["batteryPct"] < 50:
            record["guardNotes"].append(f"battery {thermal['batteryPct']}% < 50%")
        if thermal["powerSaveMode"]:
            record["guardNotes"].append("power-save mode ON")

    os.makedirs(args.outdir, exist_ok=True)
    out_path = os.path.join(args.outdir, f"raw-{record['runId']}.jsonl")
    with open(out_path, "w") as f:
        f.write(json.dumps(record) + "\n")
    print(f"[ok] pp={pp.get('avg_ts')} tg={tg.get('avg_ts')} tok/s -> {out_path}", file=sys.stderr)
    print(out_path)


if __name__ == "__main__":
    main()
