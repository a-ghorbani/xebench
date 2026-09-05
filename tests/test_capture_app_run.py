import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    "capture_app_run", Path(__file__).resolve().parents[1] / "scripts/capture_app_run.py")
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)


def result_line(timestamp="100.000", quant="Q4_0"):
    record = {"schema": "xebench-raw", "engine": "llama.cpp", "backend": "cpu",
              "model": "fixture-model", "quant": quant, "deviceInfo": {}}
    return f"{timestamp} 123 123 I ReactNativeJS: XEBENCH_RESULT {json.dumps(record)}"


class CaptureTests(unittest.TestCase):
    def test_repetitions_quants_and_invocations_are_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            one = capture.Capture(root, {"model": "Fixture Phone"})
            lines = [result_line(), result_line("101.000"), result_line("102.000", "Q8_0")]
            one.ingest("\n".join(lines))
            one.ingest("\n".join(lines))
            two = capture.Capture(root, {})
            two.ingest(lines[0])
            self.assertEqual(len(one.records), 3)
            self.assertEqual(len(two.records), 1)
            self.assertNotEqual(one.directory, two.directory)
            for entry in one.finish("complete")["records"]:
                data = (one.directory / entry["file"]).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), entry["sha256"])
                self.assertEqual(json.loads(data)["deviceInfo"]["model"], "Fixture Phone")
            with self.assertRaises(FileExistsError):
                one.finish("complete")

    def test_invalid_payloads_are_not_successful_records(self):
        with tempfile.TemporaryDirectory() as root:
            session = capture.Capture(root, {})
            for i, payload in enumerate(['{"schema":', '[]', '{"value": NaN}', '{}',
                                          '{"value": 1e999}']):
                session.ingest(f"{i} I ReactNativeJS: XEBENCH_RESULT {payload}")
            self.assertEqual(session.invalid, 5)
            self.assertFalse(session.records)
            self.assertEqual(len(list(session.directory.glob("invalid-*.txt"))), 5)

    def test_marker_inside_payload_is_not_control_message(self):
        with tempfile.TemporaryDirectory() as root:
            session = capture.Capture(root, {})
            session.ingest(result_line(quant=": XEBENCH_DONE"))
            self.assertFalse(session.done)
            self.assertEqual(len(session.records), 1)

    def run_device(self, root, log, fail=False):
        calls, now = [], [0]

        def adb(*args):
            calls.append(args)
            if fail:
                raise subprocess.CalledProcessError(1, ["adb", "private-serial"])
            if args[:2] == ("shell", "pidof"):
                return "123"
            if args[0] == "logcat":
                return log
            return "100.000" if args[:2] == ("shell", "date") else "fixture"

        def sleep(seconds):
            now[0] += seconds

        code = capture.capture_device("private-serial", 2, root, adb=adb,
                                      monotonic=lambda: now[0], sleep=sleep)
        path = next(Path(root).glob("*/manifest.json"))
        self.assertNotIn("private-serial", str(path) + path.read_text())
        self.assertFalse(any("-c" in c for c in calls))
        self.assertTrue(all("--pid=123" in c for c in calls if c[0] == "logcat"))
        if not fail:
            self.assertEqual(calls[-1], ("shell", "am", "force-stop", capture.APP))
        return code, json.loads(path.read_text())

    def test_success_requires_done_and_records(self):
        with tempfile.TemporaryDirectory() as root:
            code, manifest = self.run_device(root, result_line() + "\n101 I ReactNativeJS: XEBENCH_DONE")
            self.assertEqual(code, 0)
            self.assertEqual(manifest["status"], "complete")
            self.assertFalse(manifest["publicationReady"])

    def test_timeout_preserves_partial_records(self):
        with tempfile.TemporaryDirectory() as root:
            code, manifest = self.run_device(root, result_line())
            self.assertEqual(code, 3)
            self.assertEqual(manifest["status"], "timeout")
            self.assertEqual(len(manifest["records"]), 1)

    def test_engine_error_is_partial_even_with_done_and_result(self):
        with tempfile.TemporaryDirectory() as root:
            code, manifest = self.run_device(root, result_line() +
                "\n101 I ReactNativeJS: XEBENCH_ERROR fixture\n102 I ReactNativeJS: XEBENCH_DONE")
            self.assertEqual(code, 4)
            self.assertEqual(manifest["engineErrors"], 1)

    def test_done_without_results_is_partial(self):
        with tempfile.TemporaryDirectory() as root:
            code, _ = self.run_device(root, "100 I ReactNativeJS: XEBENCH_DONE")
            self.assertEqual(code, 4)

    def test_setup_failure_still_has_manifest(self):
        with tempfile.TemporaryDirectory() as root:
            code, manifest = self.run_device(root, "", fail=True)
            self.assertEqual(code, 2)
            self.assertEqual(manifest["status"], "setup-failed")

    def test_interruption_and_cleanup_failure_are_recorded(self):
        for cleanup_fails in (False, True):
            with self.subTest(cleanup_fails=cleanup_fails), tempfile.TemporaryDirectory() as root:
                calls = []

                def adb(*args):
                    calls.append(args)
                    if args[0] == "logcat":
                        raise KeyboardInterrupt()
                    if args[:2] == ("shell", "pidof"):
                        return "123"
                    if cleanup_fails and args[:3] == ("shell", "am", "force-stop") and len(calls) > 8:
                        raise subprocess.CalledProcessError(1, ["adb"])
                    return "fixture"

                code = capture.capture_device("private-serial", 2, root, adb=adb)
                manifest = json.loads(next(Path(root).glob("*/manifest.json")).read_text())
                self.assertEqual(code, 2 if cleanup_fails else 130)
                self.assertEqual(manifest["status"], "cleanup-failed" if cleanup_fails else "interrupted")
                self.assertEqual(calls[-1], ("shell", "am", "force-stop", capture.APP))


if __name__ == "__main__":
    unittest.main()
