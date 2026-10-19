"""Real-shaped evidence bundles for publication tests, built from the committed fixtures.

Records come from the app-emitted raw-session fixtures (tests/fixtures/raw-session/),
pass through the same capture -> raw_session -> export path as device runs
(tests/test_export_evidence.py helpers), and give sanitized bundles. Capture IDs and
times are pinned, so the same scenario always gives the same bytes and bundleSha256.
No raw capture is committed: bundles are generated into temporary directories.
"""
import copy
import hashlib
import json
from pathlib import Path
import re
import shutil

from test_export_evidence import ACCEL, APP_V2, HOST_DEVICE, evidence, make_capture

STARTED, FINISHED = "2026-10-18T09:00:00+00:00", "2026-10-18T09:30:00+00:00"


def cpu_record(name, run_id, *, timestamp=None, device=None, runtimes=True, **changes):
    """An app-emitted cpu-reference-v3 record (tests/fixtures/raw-session/app-emitted.json)."""
    record = copy.deepcopy(APP_V2[name])
    record["runId"] = run_id
    if timestamp:
        record["timestampIso"] = timestamp
    if device:
        record["deviceInfo"]["model"] = device
    if not runtimes:
        record.pop("runtimeVersions", None)
    record.update(changes)
    return anchored(record)


def accel_record(name, run_id, *, timestamp=None, device=None, runtimes=True):
    """An app-emitted accelerator-v2 record; non-f8 fixtures get their own configuration."""
    record = copy.deepcopy(ACCEL[name])
    record["runId"] = run_id
    if not name.startswith("f8-"):
        record["configuration"]["id"] = f"fixture-{name}"
    if timestamp:
        record["timestampIso"] = timestamp
    if device:
        record["deviceInfo"]["model"] = device
    if not runtimes:
        record.pop("runtimeVersions", None)
    return anchored(record)


def configuration_bytes(configuration):
    """The bytes a fixture configuration file holds in the reference roots tests build."""
    return (json.dumps(configuration, indent=2) + "\n").encode("utf-8")


def anchored(record):
    """Point the record's configurationSource hash at its configuration's fixture file, as the
    app does for the file it reads (the committed fixtures carry placeholder hashes). A record
    whose configuration a test changes afterwards no longer matches its file."""
    source = record.get("configurationSource")
    if isinstance(source, dict) and source.get("sha256") is not None:
        source["sha256"] = hashlib.sha256(configuration_bytes(record["configuration"])).hexdigest()
    return record


def bundle(root, records, capture_id, *, consistency="matched", apks=None):
    """Export `records`; returns (bundle directory, bundleSha256).

    Records are captured on the host device their deviceInfo names, one capture per
    device model, as on a real lab run: publication refuses a session whose model
    disagrees with its capture's host. The first capture is `capture_id`; the others
    differ from it in the fourth UUID group.
    """
    root = Path(root)
    by_model = {}
    for record in records:
        by_model.setdefault(record["deviceInfo"]["model"], []).append(record)
    directories = []
    for position, (model, group) in enumerate(by_model.items()):
        cid = capture_id if position == 0 else f"{capture_id[:19]}{0x8000 + position:04x}{capture_id[23:]}"
        directory = make_capture(root / f"results-{cid}", group, device_info={**HOST_DEVICE, "model": model})
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest.update(captureId=cid, startedAt=STARTED, finishedAt=FINISHED)
        identity = manifest["appIdentity"]
        if apks is not None:
            identity["before"] = identity["after"] = {"status": "recorded", "apks": apks}
        identity["consistency"] = consistency
        if consistency == "changed":
            manifest["status"] = "app-changed"
        manifest_path.write_text(json.dumps(manifest))
        directories.append(directory)
    report = evidence.export(directories, root / "bundles")
    return Path(report["bundle"]), report["evidence"]["bundleSha256"]


# Synthetic device models the fixtures use, captured on HOST_DEVICE's SoC. They exist only in
# the reference roots tests build; the committed configs/devices.json lists lab phones only.
FIXTURE_DEVICES = ("Fixture", "Fixture Phone", "Fixture Tablet")


def reference_root(target):
    """A repository root with the committed reference data (reviewed run configurations,
    model manifests, device table) plus the fixture configurations and devices; returns it."""
    target = Path(target)
    repo = Path(__file__).resolve().parents[1]
    shutil.copytree(repo / "xebenchapp/configs", target / "xebenchapp/configs")
    configurations = [cpu_record(name, "1767225600000-0-ref")["configuration"] for name in APP_V2]
    configurations += [accel_record(name, "1767225600000-0-ref")["configuration"] for name in ACCEL]
    for configuration in configurations:
        data = configuration_bytes(configuration)
        (target / f"xebenchapp/configs/fixture-{hashlib.sha256(data).hexdigest()[:16]}.json").write_bytes(data)
    shutil.copytree(repo / "configs", target / "configs")
    table = target / "configs/devices.json"
    devices = json.loads(table.read_text())
    devices["devices"] += [{"model": model, "name": model, "soc": HOST_DEVICE["soc"]} for model in FIXTURE_DEVICES]
    table.write_text(json.dumps(devices, indent=2) + "\n")
    write_board(target, sorted({configuration["id"] for configuration in configurations
                                if not configuration["id"].startswith("experiment-")
                                and re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,63}", configuration["id"])}))
    return target


FIXTURE_ENGINE_VERSIONS = {"llama.cpp": ("llama.cpp b11385 (bf9a0cc)", "llama.rn 0.0.0-fixture"),
                           "litert-lm": ("LiteRT-LM 0.18.0", None)}
NPU_KEY = "llama.cpp|npu|android|POCO F8 Ultra|Llama 3.2 1B|Q4_0|measured"


def board_config(root, ids, *, engines=None, retired=None, measured_from="2026-01-01"):
    """A configs/board.json (schema 2) for a fixture root: llama.cpp and litert-lm display the
    fixture sessions' versions from `ids` (an id, for every backend, or {"id", "backends"};
    measured from `measured_from`), carrying over their rows
    without versions; executorch and genie-x display their July 2026 rows; the failed llama.cpp
    NPU row of the seeded board is retired. `engines` and `retired` replace those parts."""
    path = Path(root) / "data/benchmarks.json"
    data = json.loads(path.read_text()) if path.exists() else {"rows": []}
    key = lambda row: "|".join(str(row.get(f)) for f in
                               ("engine", "backend", "platform", "device", "model", "quant", "provenance"))
    legacy = lambda engine: sorted(key(row) for row in data["rows"]
                                   if row["engine"] == engine and row.get("validation") == "legacy")
    if engines is None:
        engines = {"executorch": {"set": "pre-version-recording", "measured": "2026-07"},
                   "genie-x": {"set": "pre-version-recording", "measured": "2026-07"}}
        for engine, (engine_version, binding) in FIXTURE_ENGINE_VERSIONS.items():
            configurations = [item if isinstance(item, dict) else {"id": item, "backends": ["cpu", "gpu", "npu"]}
                              for item in ids]
            entry = {"set": "sessions", "configurations": configurations, "engineVersion": engine_version,
                     **({"bindingVersion": binding} if binding else {}), "measuredFrom": measured_from}
            if legacy(engine):
                entry["carryover"] = {"engineVersion": engine_version, **({"bindingVersion": binding} if binding else {}),
                                      "measured": "2026-07", "reason": "fixture rows without versions",
                                      "keys": legacy(engine)}
            engines[engine] = entry
        engines = dict(sorted(engines.items()))
    if retired is None:
        failed = any(key(row) == NPU_KEY and row.get("validation") == "failed" for row in data["rows"])
        retired = [{"key": NPU_KEY, "reason": "fixture: failed row", "retiredAt": "2026-10-19"}] if failed else []
    return {"schema": "xebench-board", "schemaVersion": 2, "description": "Fixture board configuration.",
            "engines": engines, "retiredKeys": retired}


def write_board(root, ids, **options):
    """Write board_config(root, ids, **options) as the root's configs/board.json; returns it."""
    board = board_config(root, ids, **options)
    (Path(root) / "configs/board.json").write_text(json.dumps(board, indent=2) + "\n")
    return board


def host(bundle_dir, repo_root):
    """Copy a bundle to <repo_root>/evidence/<sha>/, where its evidence URL serves it."""
    target = Path(repo_root) / "evidence" / Path(bundle_dir).name
    shutil.copytree(bundle_dir, target)
    return target


def rehash(bundle_dir, path, change):
    """Edit one bundle file and re-hash it and the manifest consistently: a forged bundle
    whose checksums all agree. Returns the new bundle directory (renamed to its new hash)."""
    bundle_dir = Path(bundle_dir)
    target = bundle_dir / path
    document = json.loads(target.read_bytes())
    change(document)
    data = evidence.canonical(document)
    target.write_bytes(data)
    manifest = json.loads((bundle_dir / "manifest.json").read_bytes())
    for item in manifest["files"]:
        if item["path"] == path:
            item.update(sha256=evidence.sha256(data), bytes=len(data))
    manifest_bytes = evidence.canonical(manifest)
    (bundle_dir / "manifest.json").write_bytes(manifest_bytes)
    renamed = bundle_dir.with_name(evidence.sha256(manifest_bytes))
    bundle_dir.rename(renamed)
    return renamed
