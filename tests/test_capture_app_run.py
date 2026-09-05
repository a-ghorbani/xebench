import hashlib
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "capture_app_run", Path(__file__).resolve().parents[1] / "scripts/capture_app_run.py")
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)


def result_line(timestamp="100.000", quant="Q4_0"):
    record = {"schema": "xebench-raw", "engine": "llama.cpp", "backend": "cpu",
              "model": "fixture-model", "quant": quant, "deviceInfo": {}}
    return f"{timestamp} 123 123 I ReactNativeJS: XEBENCH_RESULT {json.dumps(record)}"


def signal_worker(root, ready, stops):
    def adb(*args):
        if args[:3] == ("shell", "am", "force-stop"):
            stops.value += 1
            if stops.value == 2:
                # Repeated cancellation during cleanup must not skip the manifest.
                os.kill(os.getpid(), signal.SIGTERM)
        if args[:2] == ("shell", "pidof"):
            return "123"
        if args[:3] == ("logcat", "-b", "events"):
            return ""
        return result_line() if args[0] == "logcat" else "fixture"

    def sleep(_seconds):
        ready.set()
        time.sleep(0.1)

    raise SystemExit(capture.capture_device("fixture-device", 30, root, adb=adb, sleep=sleep))


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
            if args[:3] == ("logcat", "-b", "events"):
                return ""
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
        self.assertTrue(all("--pid=123" in c for c in calls if c[0] == "logcat" and "events" not in c))
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
                    if args[:3] == ("logcat", "-b", "events"):
                        return ""
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

    def test_real_signals_preserve_manifest_and_stop_workload(self):
        context = multiprocessing.get_context("spawn")
        for signum in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=signum), tempfile.TemporaryDirectory() as root:
                ready, stops = context.Event(), context.Value("i", 0)
                process = context.Process(target=signal_worker, args=(root, ready, stops))
                process.start()
                try:
                    self.assertTrue(ready.wait(5), "capture did not reach polling")
                    os.kill(process.pid, signum)
                    process.join(5)
                    self.assertFalse(process.is_alive(), "cleanup did not finish")
                    self.assertEqual(process.exitcode, 128 + signum)
                    self.assertEqual(stops.value, 2)
                    manifest = json.loads(next(Path(root).glob("*/manifest.json")).read_text())
                    self.assertEqual(manifest["status"], "interrupted")
                    self.assertEqual(len(manifest["records"]), 1)
                finally:
                    if process.is_alive():
                        process.kill()
                    process.join()

    def test_startup_crash_recovers_only_new_app_process_evidence(self):
        for launch_fails in (False, True):
            with self.subTest(launch_fails=launch_fails), tempfile.TemporaryDirectory() as root:
                calls, event_reads = [], 0
                old = "100.0 10 10 I am_proc_start: [0,111,10000,com.xebenchapp,activity,fixture]"

                def adb(*args):
                    nonlocal event_reads
                    calls.append(args)
                    if args[:3] == ("shell", "am", "start") and launch_fails:
                        raise subprocess.CalledProcessError(1, ["adb"])
                    if args[:2] == ("shell", "pidof"):
                        raise subprocess.CalledProcessError(1, ["adb"])
                    if args[:3] == ("logcat", "-b", "events"):
                        event_reads += 1
                        if event_reads == 1:
                            return old
                        return old + "\n" + "\n".join([
                            "100.1 10 10 I am_proc_start: [0,123,10000,com.xebenchapp,activity,fixture]",
                            "100.2 10 10 I am_proc_start: [0,456,10000,com.xebenchapp:worker,service,fixture]",
                            "100.3 10 10 I am_proc_start: [0,789,10001,another.app,activity,fixture]",
                        ])
                    if args[0] == "logcat":
                        self.assertIn("--pid=123", args)
                        return result_line() + "\n101 I ReactNativeJS: XEBENCH_ERROR fixture startup failure"
                    return "fixture"

                code = capture.capture_device("fixture-device", 2, root, adb=adb)
                manifest = json.loads(next(Path(root).glob("*/manifest.json")).read_text())
                self.assertEqual(code, 2)
                self.assertEqual(manifest["status"], "startup-failed")
                self.assertEqual(manifest["engineErrors"], 1)
                self.assertEqual(len(manifest["records"]), 1)
                diagnostic = next(Path(root).glob("*/error-*.txt")).read_text()
                self.assertIn("fixture startup failure", diagnostic)
                self.assertEqual(calls[-1], ("shell", "am", "force-stop", capture.APP))

    def test_pid_recovery_rejects_old_events_and_other_processes(self):
        old = "100.0 I am_proc_start: [0,123,10000,com.xebenchapp,activity,fixture]"
        other = "100.1 I am_proc_start: [0,456,10000,com.xebenchapp:worker,activity,fixture]"
        self.assertIsNone(capture.started_pid(old + "\n" + other, {old}))

    def test_native_startup_crash_without_js_markers_is_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            event_reads = 0
            crash = "100.1 123 123 E AndroidRuntime: FATAL EXCEPTION: main\n" + \
                    "100.1 123 123 E AndroidRuntime: java.lang.UnsatisfiedLinkError: fixture"

            def adb(*args):
                nonlocal event_reads
                if args[:2] == ("shell", "pidof"):
                    return ""
                if args[:3] == ("logcat", "-b", "events"):
                    event_reads += 1
                    return "" if event_reads == 1 else \
                        "100.0 I am_proc_start: [0,123,10000,com.xebenchapp,activity,fixture]"
                if args[0] == "logcat":
                    self.assertIn("--pid=123", args)
                    self.assertIn("all", args)
                    self.assertIn("*:V", args)
                    return crash
                return "fixture"

            self.assertEqual(capture.capture_device("fixture-device", 2, root, adb=adb), 2)
            manifest = json.loads(next(Path(root).glob("*/manifest.json")).read_text())
            self.assertEqual(manifest["status"], "startup-failed")
            self.assertFalse(manifest["records"])
            self.assertEqual(next(Path(root).glob("*/startup-logcat.txt")).read_text(), crash + "\n")

    def test_signal_handlers_are_restored(self):
        handlers = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
        with tempfile.TemporaryDirectory() as root:
            self.run_device(root, "", fail=True)
        self.assertEqual(handlers, {s: signal.getsignal(s) for s in handlers})

    def test_signal_during_checksum_keeps_manifest_consistent(self):
        original_sha256 = hashlib.sha256

        def cancel_during_checksum(data):
            os.kill(os.getpid(), signal.SIGTERM)
            return original_sha256(data)

        with tempfile.TemporaryDirectory() as root, patch.object(capture.hashlib, "sha256", cancel_during_checksum):
            code, manifest = self.run_device(root, result_line())
            self.assertEqual(code, 143)
            self.assertEqual(manifest["status"], "interrupted")
            self.assertEqual(len(manifest["records"]), 1)
            record = next(Path(root).glob("*/raw-*.jsonl"))
            self.assertEqual(manifest["records"][0]["sha256"], original_sha256(record.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
