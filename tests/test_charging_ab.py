"""Charging A/B experiment analysis (scripts/experiments/charging_ab_analyze.py)."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("charging_ab_analyze",
                                              ROOT / "scripts/experiments/charging_ab_analyze.py")
analyze = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analyze)
APP = json.loads((ROOT / "tests/fixtures/raw-session/app-emitted.json").read_text())["records"]
CONFIG = json.loads((ROOT / "xebenchapp/configs/experiment-charging-ab-v1.json").read_text())


def conditions(status, plugged=2):
    record = copy.deepcopy(APP["chargingRecordOnly"])
    attempt = record["coldRuns"][0]["conditions"]
    for snapshot in (attempt["before"], attempt["after"], *attempt["during"]["samples"]):
        snapshot.update(batteryStatus=status, plugged=plugged)
    return attempt


class LabelTests(unittest.TestCase):
    def test_observed_status_labels(self):
        self.assertEqual(analyze.label(conditions(2)), "A")
        self.assertEqual(analyze.label(APP["chargingRecordOnly"]["coldRuns"][0]["conditions"]), "A")
        self.assertEqual(analyze.label(APP["complete"]["coldRuns"][0]["conditions"]), "B")
        # DISCHARGING while plugged is neither condition; a missing plug is excluded.
        mixed = conditions(4)
        mixed["during"]["samples"][0]["batteryStatus"] = 3
        self.assertEqual(analyze.label(mixed), "excluded:mixed-or-other-status")
        self.assertEqual(analyze.label(conditions(5, plugged=0)), "excluded:unplugged-or-unknown-plug")
        # A charging sample in a not-charging run makes it mixed, never B.
        flipped = copy.deepcopy(APP["chargingDuringRun"]["coldRuns"][0]["conditions"])
        self.assertEqual(analyze.label(flipped), "excluded:mixed-or-other-status")


class StatisticsTests(unittest.TestCase):
    def reps(self, values, cond):
        return [{"label": cond, **{metric: value for metric in analyze.METRICS},
                 **{name: 30.0 for name in analyze.DIAGNOSTICS}, "maxThermalStatus": 0} for value in values]

    def test_verdict_follows_the_preregistered_threshold(self):
        self.assertTrue(analyze.verdict(-1.0, 2.9).startswith("irrelevant"))
        self.assertTrue(analyze.verdict(3.0, 8.0).startswith("relevant"))
        self.assertTrue(analyze.verdict(-9.0, -3.0).startswith("relevant"))
        self.assertEqual(analyze.verdict(-1.0, 3.5), "inconclusive")
        self.assertEqual(analyze.verdict(None, None), "insufficient data")
        self.assertEqual(analyze.THRESHOLD_PCT, 3.0)

    def test_comparison_is_reproducible_and_signed_b_minus_a(self):
        reps = self.reps([100, 101, 99, 100.5, 99.5, 100], "A") + self.reps([90, 91, 89, 90.5, 89.5, 90], "B")
        first = analyze.compare(reps, resamples=500, min_n=6)
        self.assertEqual(first, analyze.compare(reps, resamples=500, min_n=6))
        self.assertEqual(analyze.compare(reps, resamples=50)["metrics"]["decodeTps"]["verdict"],
                         "descriptive only (n<10)")
        decode = first["metrics"]["decodeTps"]
        self.assertAlmostEqual(decode["deltaPct"], -10.0)
        self.assertLess(decode["ci95"][1], -3)
        self.assertTrue(decode["verdict"].startswith("relevant"))
        self.assertEqual(analyze.compare(self.reps([1], "A") + self.reps([1, 2], "B"))["metrics"]["decodeTps"],
                         {"verdict": "insufficient data"})

    def test_only_valid_experiment_records_are_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / "capture-1"
            capture.mkdir()
            lines = {"raw-a.jsonl": APP["chargingRecordOnly"], "raw-b.jsonl": APP["complete"]}
            broken = copy.deepcopy(APP["chargingRecordOnly"])
            broken["runId"] = "1767225600000-9-broken"
            broken["chargingPolicy"]["readiness"] = "wait-while-charging"
            lines["raw-c.jsonl"] = broken
            for name, record in lines.items():
                (capture / name).write_text(json.dumps(record) + "\n")
            (capture / "manifest.json").write_text(json.dumps({
                "schema": "xebench-local-capture", "captureId": "capture-1", "deviceInfo": {"model": "Fixture"},
                "records": [{"file": name} for name in lines]}))
            devices = analyze.load([directory])
        entry = devices["Fixture"]
        # The reference (non-experiment) session is ignored, the inconsistent one rejected.
        self.assertEqual([rep["label"] for rep in entry["reps"]], ["A"])
        self.assertEqual(entry["invalid"], 1)

    def test_experiment_configuration_is_the_reference_run_with_record_only(self):
        reference = json.loads((ROOT / "xebenchapp/configs/cpu-reference-v1.json").read_text())
        self.assertEqual(CONFIG["experimentChargingReadiness"], "record-only")
        self.assertEqual(CONFIG["runs"], reference["runs"])
        self.assertEqual({k: CONFIG[k] for k in ("cooldownSec", "maxDecodeTokens", "nColdRuns")},
                         {"cooldownSec": reference["cooldownSec"], "maxDecodeTokens": reference["maxDecodeTokens"],
                          "nColdRuns": 3})
        # Amendment 1 (pre-data): readiness bound and battery-rise override.
        self.assertEqual((CONFIG["readinessTimeoutSec"], CONFIG["maxBatteryTempRiseC"]), (300, 10))


if __name__ == "__main__":
    unittest.main()
