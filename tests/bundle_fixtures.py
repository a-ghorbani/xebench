"""Real-shaped evidence bundles for publication tests, built from the committed fixtures.

Records come from the app-emitted raw-session fixtures (tests/fixtures/raw-session/),
pass through the same capture -> raw_session -> export path as device runs
(tests/test_export_evidence.py helpers), and give sanitized bundles. Capture IDs and
times are pinned, so the same scenario always gives the same bytes and bundleSha256.
No raw capture is committed: bundles are generated into temporary directories.
"""
import copy
import json
from pathlib import Path
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
    return record


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
    model manifests, device table) plus the fixture devices; returns it."""
    target = Path(target)
    repo = Path(__file__).resolve().parents[1]
    shutil.copytree(repo / "xebenchapp/configs", target / "xebenchapp/configs")
    shutil.copytree(repo / "configs", target / "configs")
    table = target / "configs/devices.json"
    devices = json.loads(table.read_text())
    devices["devices"] += [{"model": model, "name": model, "soc": HOST_DEVICE["soc"]} for model in FIXTURE_DEVICES]
    table.write_text(json.dumps(devices, indent=2) + "\n")
    return target


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
