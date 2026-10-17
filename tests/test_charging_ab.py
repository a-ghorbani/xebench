"""Charging A/B experiment analysis (scripts/experiments/charging_ab_analyze.py)."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import unittest.mock

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
        # Incomplete monitor coverage leaves the status partly unobserved: excluded, never A or B.
        gap = conditions(2)
        gap["during"]["dropped"] = 1
        self.assertEqual(analyze.label(gap), "excluded:incomplete-coverage")
        expired = conditions(4)
        expired["during"]["expired"] = True
        self.assertEqual(analyze.label(expired), "excluded:incomplete-coverage")
        unsampled = conditions(5)
        unsampled["during"] = None
        self.assertEqual(analyze.label(unsampled), "excluded:incomplete-coverage")
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
        # The reference configuration later pinned the GGUF's SHA-256 (modelSha256); the
        # pre-registered experiment configuration stays byte-identical and names the same run.
        self.assertEqual(CONFIG["runs"], [{k: v for k, v in run.items() if k != "modelSha256"}
                                          for run in reference["runs"]])
        self.assertEqual({k: CONFIG[k] for k in ("cooldownSec", "maxDecodeTokens", "nColdRuns")},
                         {"cooldownSec": reference["cooldownSec"], "maxDecodeTokens": reference["maxDecodeTokens"],
                          "nColdRuns": 3})
        # Amendment 1 (pre-data): readiness bound and battery-rise override.
        self.assertEqual((CONFIG["readinessTimeoutSec"], CONFIG["maxBatteryTempRiseC"]), (300, 10))


if __name__ == "__main__":
    unittest.main()


RUN_SPEC = importlib.util.spec_from_file_location("charging_ab_run", ROOT / "scripts/experiments/charging_ab_run.py")
driver = importlib.util.module_from_spec(RUN_SPEC)
RUN_SPEC.loader.exec_module(driver)


class FakePhone:
    """A fake device: one charge setting, a battery that follows it, and adb failures on demand."""

    def __init__(self, original="3"):
        self.setting, self.original, self.config = original, original, None
        self.fail_restores = 0  # restore attempts that time out, like the hung adb server of D1
        self.commands = []

    def device(self, serial):
        phone = self

        class Device:
            def __init__(self, _serial):
                self.serial = serial

            def adb(self, *args, timeout=30, check=True):
                phone.commands.append(args)
                if args[0] == "push":
                    phone.config = Path(args[1]).read_text()
                elif args[:3] == ("shell", "rm", "-f"):
                    phone.config = None
                elif args[:2] == ("shell", "ls"):
                    return "ls: No such file or directory"
                elif args[:2] == ("shell", "getprop"):
                    return "Fixture"
                return ""

            def shell(self, command):
                phone.commands.append(("shell", command))
                if command.startswith("settings get"):
                    return phone.setting
                if command.endswith(" " + phone.original) and phone.fail_restores:
                    phone.fail_restores -= 1
                    raise driver.subprocess.TimeoutExpired(command, 30)
                phone.setting = command.rsplit(" ", 1)[1]
                return ""

            def battery(self):
                status = "2" if phone.setting == "0" else "4"
                return {"status": status, "level": "90", "temperature": "300", "readAt": driver.utc()}

        return Device(serial)


class DriverRestoreTests(unittest.TestCase):
    """The driver must leave no charge setting or staged configuration changed (D1)."""

    ARGS = ["SERIAL", "--label", "fixture", "--start", "A", "--read", "settings get global protect_battery",
            "--set-a", "settings put global protect_battery 0", "--set-b", "settings put global protect_battery 1",
            "--restore", "settings put global protect_battery {original}", "--max-pairs", "1"]

    def run_driver(self, phone, capture):
        self.blocks = []
        with tempfile.TemporaryDirectory() as root, \
                unittest.mock.patch.object(driver, "ROOT", Path(root)), \
                unittest.mock.patch.object(driver, "Device", phone.device), \
                unittest.mock.patch.object(driver.capture_app_run, "capture_device", capture), \
                unittest.mock.patch.object(driver, "counts", lambda _d: {"A": 0, "B": 0}), \
                unittest.mock.patch.object(driver, "STATE_POLL_S", 0), \
                unittest.mock.patch.object(driver.time, "sleep", lambda _s: None):
            try:
                driver.main(self.ARGS)
            finally:
                lines = (Path(root) / "results/charging-ab/fixture/blocks.jsonl").read_text().splitlines()
                self.blocks = [json.loads(line) for line in lines]

    def test_crash_mid_run_restores_the_setting_and_config(self):
        phone = FakePhone()

        def crash(*_args):
            self.assertEqual(phone.setting, "0")  # condition A was applied
            raise RuntimeError("driver bug")
        with self.assertRaises(RuntimeError):
            self.run_driver(phone, crash)
        self.assertEqual((phone.setting, phone.config), ("3", None))
        self.assertEqual(self.blocks[-1]["kind"], "restore")
        self.assertTrue(self.blocks[-1]["restored"])

    def test_hung_adb_during_restore_is_retried(self):
        phone = FakePhone()
        phone.fail_restores = 2
        self.run_driver(phone, lambda *_args: 0)
        self.assertEqual(phone.setting, "3")
        self.assertTrue(self.blocks[-1]["restored"])

    def test_sigterm_outside_a_capture_still_restores(self):
        phone = FakePhone()

        def terminated(*_args):
            driver._terminate(15, None)
        with self.assertRaises(KeyboardInterrupt):
            self.run_driver(phone, terminated)
        self.assertEqual(phone.setting, "3")
        self.assertIs(driver.signal.getsignal(driver.signal.SIGTERM), driver.signal.SIG_DFL)

    def test_interrupted_capture_stops_the_experiment(self):
        phone, calls = FakePhone(), []

        def interrupted(*_args):
            calls.append(1)
            return 143
        self.run_driver(phone, interrupted)
        self.assertEqual(len(calls), 1)
        self.assertEqual([block["reason"] for block in self.blocks if block["kind"] == "stop"], ["interrupted"])
        self.assertEqual(phone.setting, "3")
