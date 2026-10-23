from contextlib import redirect_stderr
import hashlib
import io
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
APP_RECORDS = json.loads((Path(__file__).resolve().parent / "fixtures/raw-session/app-emitted.json")
                         .read_text())["records"]


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
    def test_file_references_preserve_large_records_and_checkpoints(self):
        with tempfile.TemporaryDirectory() as root:
            session = capture.Capture(root, {})
            files = {}
            lines = []
            for index, key in enumerate(('partial', 'complete')):
                name = f'fixture-{index}.json'
                files[name] = json.dumps(APP_RECORDS[key])
                reference = {'name': name, 'sha256': hashlib.sha256(files[name].encode()).hexdigest()}
                lines.append(f'{index} I ReactNativeJS: XEBENCH_RESULT_FILE {json.dumps(reference)}')
            snapshot = '\n'.join(lines)
            session.ingest(snapshot, files.__getitem__)
            session.ingest(snapshot, files.__getitem__)
            manifest = session.finish('complete')
            self.assertEqual(manifest['invalidRecords'], 0)
            self.assertEqual(len(manifest['checkpoints']), 1)
            self.assertEqual(len(manifest['records']), 1)
            self.assertEqual(manifest['records'][0]['validation'], 'raw-session-v1')
            saved = (session.directory / manifest['records'][0]['file']).read_text()
            self.assertEqual(saved, files['fixture-1.json'])
            self.assertEqual(json.loads(saved)['protocol']['prompt'], APP_RECORDS['complete']['protocol']['prompt'])

    def test_rejections_keep_payload_and_machine_readable_reason(self):
        complete = APP_RECORDS['complete']
        legacy = json.loads(result_line().split('XEBENCH_RESULT ', 1)[1])
        cases = {
            'version.missing': legacy,
            'version.unsupported': {**complete, 'schemaVersion': 2},
            'summary.mismatch': {**complete, 'summary': {**complete['summary'], 'decodeTps': {
                **complete['summary']['decodeTps'], 'median': 99}}},
            'status.attempts': {**complete, 'coldRuns': complete['coldRuns'][:2]},
        }
        for code, record in cases.items():
            with self.subTest(code=code), tempfile.TemporaryDirectory() as root:
                session = capture.Capture(root, {})
                payload = json.dumps(record)
                reference = {'name': 'fixture.json', 'sha256': hashlib.sha256(payload.encode()).hexdigest()}
                session.ingest('1 I ReactNativeJS: XEBENCH_RESULT_FILE ' + json.dumps(reference),
                               {'fixture.json': payload}.__getitem__)
                manifest = session.finish('partial')
                self.assertEqual(session.invalid, 1)
                self.assertFalse(manifest['records'] or manifest['checkpoints'])
                rejection = manifest['rejections'][0]
                self.assertIn(code, rejection['codes'])
                stored = (session.directory / rejection['file']).read_bytes()
                # Exact device bytes: the claimed checksum can be re-checked from saved evidence.
                self.assertEqual(stored, payload.encode())
                detail = json.loads((session.directory / rejection['reasonFile']).read_text())
                self.assertEqual(detail['payloadFile'], rejection['file'])
                self.assertEqual(detail['sourceFile'], 'fixture.json')
                self.assertEqual(detail['claimedSha256'], reference['sha256'])
                self.assertEqual(hashlib.sha256(stored).hexdigest(), detail['claimedSha256'])
                self.assertIn(code, [item['code'] for item in detail['reasons']])
                self.assertTrue(all(set(item) == {'code', 'path', 'message'} for item in detail['reasons']))

    def test_file_reference_rejects_traversal_mismatch_and_size(self):
        cases = [
            ({'name': '../private.json', 'sha256': 'a' * 64}, '{}', False),
            ({'name': 'fixture.json', 'sha256': 'a' * 64}, '{}', True),
            ({'name': 'fixture.json', 'sha256': 'a' * 64}, 'x' * (capture.MAX_RECORD_BYTES + 1), True),
        ]
        for reference, payload, should_read in cases:
            with self.subTest(reference=reference), tempfile.TemporaryDirectory() as root:
                session = capture.Capture(root, {})
                reads = []
                def read_file(name):
                    reads.append(name)
                    return payload
                session.ingest('100 I ReactNativeJS: XEBENCH_RESULT_FILE ' + json.dumps(reference), read_file)
                self.assertEqual(bool(reads), should_read)
                self.assertEqual(session.invalid, 1)
                self.assertFalse(session.records)
                expected = 'transport.size' if len(payload) > capture.MAX_RECORD_BYTES else \
                    'transport.checksum' if should_read else 'transport.reference'
                self.assertEqual(session.rejections[0]['codes'], [expected])
                stored = (session.directory / session.rejections[0]['file']).read_text()
                self.assertEqual(stored, payload if should_read else json.dumps(reference) + '\n')
                detail = json.loads((session.directory / session.rejections[0]['reasonFile']).read_text())
                # A well-formed reference keeps its claim even when the bytes disagree with it.
                self.assertEqual(detail['claimedSha256'], reference['sha256'] if should_read else None)
                self.assertEqual(detail['sourceFile'], reference['name'] if should_read else None)

    def test_rejected_file_record_can_be_rechecked_from_saved_evidence(self):
        retrieved = json.dumps(APP_RECORDS['complete'])  # no trailing newline on the device
        cases = {
            'checksum': hashlib.sha256(b'other bytes').hexdigest(),
            'validation': hashlib.sha256(retrieved.encode()).hexdigest(),
        }
        for case, claimed in cases.items():
            payload = retrieved if case == 'checksum' else retrieved.replace('"loadMs": 17', '"loadMs": 99')
            claimed = claimed if case == 'checksum' else hashlib.sha256(payload.encode()).hexdigest()
            with self.subTest(case=case), tempfile.TemporaryDirectory() as root:
                session = capture.Capture(root, {})
                reference = {'name': 'fixture.json', 'sha256': claimed}
                session.ingest('1 I ReactNativeJS: XEBENCH_RESULT_FILE ' + json.dumps(reference),
                               {'fixture.json': payload}.__getitem__)
                rejection = session.rejections[0]
                detail = json.loads((session.directory / rejection['reasonFile']).read_text())
                stored = (session.directory / rejection['file']).read_bytes()
                self.assertEqual(stored, payload.encode())
                self.assertEqual((detail['sourceFile'], detail['claimedSha256']), ('fixture.json', claimed))
                # The saved bytes reproduce the device checksum exactly when it matched,
                # and demonstrably differ from the claim when it did not.
                self.assertEqual(hashlib.sha256(stored).hexdigest() == claimed, case == 'validation')
        with tempfile.TemporaryDirectory() as root:
            session = capture.Capture(root, {})
            session.ingest('1 I ReactNativeJS: XEBENCH_RESULT {"schema": "other"}')
            detail = json.loads((session.directory / session.rejections[0]['reasonFile']).read_text())
            self.assertIsNone(detail['claimedSha256'])
            self.assertEqual((session.directory / session.rejections[0]['file']).read_text(), '{"schema": "other"}\n')

    def test_hostile_inline_payloads_are_rejected_not_fatal(self):
        with tempfile.TemporaryDirectory() as root:
            session = capture.Capture(root, {})
            for index, payload in enumerate(['{"a": [' * 2000 + '1' + ']}' * 2000, '{"a": ' + '1' * 5000 + '}']):
                session.ingest(f'{index} I ReactNativeJS: XEBENCH_RESULT {payload}')
            self.assertEqual(session.invalid, 2)
            self.assertFalse(session.records)

    def test_integer_beyond_double_range_is_rejected_with_payload_kept(self):
        payload = json.dumps(APP_RECORDS['partial']).replace('"loadMs": 17', '"loadMs": 1' + '0' * 400)
        reference = {'name': 'fixture.json', 'sha256': hashlib.sha256(payload.encode()).hexdigest()}
        with tempfile.TemporaryDirectory() as root:
            session = capture.Capture(root, {})
            session.ingest('1 I ReactNativeJS: XEBENCH_RESULT_FILE ' + json.dumps(reference),
                           {'fixture.json': payload}.__getitem__)
            self.assertEqual(session.rejections[0]['codes'], ['json.non-finite'])
            self.assertEqual((session.directory / session.rejections[0]['file']).read_text(), payload)

    def test_undecodable_reference_is_rejected_not_fatal(self):
        def read_file(_name):
            raise UnicodeDecodeError('utf-8', b'\xff', 0, 1, 'invalid start byte')
        with tempfile.TemporaryDirectory() as root:
            session = capture.Capture(root, {})
            reference = {'name': 'fixture.json', 'sha256': 'a' * 64}
            session.ingest('1 I ReactNativeJS: XEBENCH_RESULT_FILE ' + json.dumps(reference), read_file)
            self.assertEqual(session.rejections[0]['codes'], ['transport.encoding'])

    def test_failed_session_cannot_be_successful_without_error_marker(self):
        payload = json.dumps(APP_RECORDS['failed'])
        reference = {'name': 'fixture.json', 'sha256': hashlib.sha256(payload.encode()).hexdigest()}
        log = '100 I ReactNativeJS: XEBENCH_RESULT_FILE ' + json.dumps(reference) + \
              '\n101 I ReactNativeJS: XEBENCH_DONE'
        with tempfile.TemporaryDirectory() as root:
            code, manifest = self.run_device(root, log, files={'fixture.json': payload})
            self.assertEqual(code, 4)
            self.assertEqual(manifest['failedRecords'], 1)
            self.assertEqual(manifest['invalidRecords'], 0)

    def test_deeply_invalid_record_makes_capture_partial(self):
        record = {**APP_RECORDS['complete'], 'coldRuns': APP_RECORDS['complete']['coldRuns'][:3]}
        payload = json.dumps(record)
        reference = {'name': 'fixture.json', 'sha256': hashlib.sha256(payload.encode()).hexdigest()}
        log = '100 I ReactNativeJS: XEBENCH_RESULT_FILE ' + json.dumps(reference) + \
              '\n101 I ReactNativeJS: XEBENCH_DONE'
        with tempfile.TemporaryDirectory() as root:
            code, manifest = self.run_device(root, log, files={'fixture.json': payload})
            self.assertEqual(code, 4)
            self.assertEqual(manifest['status'], 'partial')
            self.assertEqual(manifest['invalidRecords'], 1)
            self.assertIn('status.attempts', manifest['rejections'][0]['codes'])

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
            self.assertEqual(len(list(session.directory.glob("invalid-*.reason.json"))), 5)
            self.assertEqual([item["codes"] for item in session.rejections], [
                ["json.parse"], ["json.not-object"], ["json.non-finite"], ["record.schema"], ["json.non-finite"]])

    def test_marker_inside_payload_is_not_control_message(self):
        with tempfile.TemporaryDirectory() as root:
            session = capture.Capture(root, {})
            session.ingest(result_line(quant=": XEBENCH_DONE"))
            self.assertFalse(session.done)
            self.assertEqual(len(session.records), 1)

    def run_device(self, root, log, fail=False, files=None):
        calls, now = [], [0]

        def adb(*args):
            calls.append(args)
            if fail:
                raise subprocess.CalledProcessError(1, ["adb", "private-serial"])
            if args[:2] == ("shell", "pidof"):
                return "123"
            if args[:3] == ("logcat", "-b", "events"):
                return ""
            if args[0] == 'exec-out':
                self.assertEqual(args[:4], ('exec-out', 'head', '-c', str(capture.MAX_RECORD_BYTES + 1)))
                self.assertTrue(args[4].startswith('/sdcard/Android/data/com.xebenchapp/files/xebench-results/'))
                return files[Path(args[4]).name]
            if args[0] == "logcat":
                return log
            return "100.000" if args[:2] == ("shell", "date") else "fixture"

        def sleep(seconds):
            now[0] += seconds

        code = capture.capture_device("private-serial", 2, root, adb=adb,
                                      monotonic=lambda: now[0], sleep=sleep)
        path = next(Path(root).glob("*/manifest.json"))
        self.assertNotIn("private-serial", str(path) + path.read_text())
        self.assertFalse(any("-c" in c for c in calls if c[0] == 'logcat'))
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
            self.assertEqual(manifest["records"][0]["validation"], "legacy-unvalidated")

    def test_capture_retrieves_referenced_file_without_trimming_bytes(self):
        payload = json.dumps(APP_RECORDS['complete']) + '\n'
        reference = {'name': 'fixture.json', 'sha256': hashlib.sha256(payload.encode()).hexdigest()}
        log = '100 I ReactNativeJS: XEBENCH_RESULT_FILE ' + json.dumps(reference) + \
              '\n101 I ReactNativeJS: XEBENCH_DONE'
        with tempfile.TemporaryDirectory() as root:
            code, manifest = self.run_device(root, log, files={'fixture.json': payload})
            self.assertEqual(code, 0)
            self.assertEqual(len(manifest['records']), 1)
            path = next(Path(root).glob('*/raw-*.jsonl'))
            self.assertEqual(path.read_bytes(), payload.encode())
            self.assertEqual(manifest['records'][0]['sha256'], reference['sha256'])

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


def policy_dump(showing, secure):
    """`dumpsys window policy` as Android 16 prints the keyguard part of it."""
    flag = lambda value: str(value).lower()  # noqa: E731
    return "\n".join([
        "WINDOW MANAGER POLICY STATE (dumpsys window policy)",
        "    mSafeMode=false mSystemReady=true mSystemBooted=true",
        "    mKeyguardDelegate:",
        "    KeyguardServiceDelegate",
        f"      showing={flag(showing)}",
        f"      showingAndNotOccluded={flag(showing)}",
        f"      inputRestricted={flag(showing)}",
        "      occluded=false",
        f"      secure={flag(secure)}",
        "      dreaming=false",
        "      systemIsReady=true",
        "      KeyguardStateMonitor",
        f"        mIsShowing={flag(showing)}",
        "        mSimSecure=false",
        "        mTrusted=false",
    ])


class FakePhone:
    """A fake adb with a keyguard: `wm dismiss-keyguard` removes a swipe lock (when `dismissable`) and
    records every call, so a test can show what was sent and in which order."""

    def __init__(self, showing, secure, dismissable=True, dump=None):
        self.showing, self.secure, self.dismissable, self.dump = showing, secure, dismissable, dump
        self.calls, self.now = [], 0

    def adb(self, *args):
        self.calls.append(args)
        if args == ("shell", "dumpsys", "window", "policy"):
            return self.dump if self.dump is not None else policy_dump(self.showing, self.secure)
        if args == ("shell", "wm", "dismiss-keyguard"):
            if not self.secure and self.dismissable:
                self.showing = False
            return ""
        if args[:2] == ("shell", "pidof"):
            return "123"
        if args[:3] == ("logcat", "-b", "events"):
            return ""
        if args[0] == "logcat":
            return result_line() + "\n101 I ReactNativeJS: XEBENCH_DONE"
        return "100.000" if args[:2] == ("shell", "date") else "fixture"

    def sleep(self, seconds):
        self.now += seconds

    def run(self, root):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = capture.capture_device("private-serial", 30, root, adb=self.adb, monotonic=lambda: self.now,
                                          sleep=self.sleep)
        manifest = json.loads(next(Path(root).glob("*/manifest.json")).read_text())
        return code, manifest, stderr.getvalue()

    def index(self, *args):
        return self.calls.index(args)


WAKE = ("shell", "input", "keyevent", "KEYCODE_WAKEUP")
DISMISS = ("shell", "wm", "dismiss-keyguard")
LAUNCH = ("shell", "am", "start", "-W", "-n", f"{capture.APP}/.MainActivity")


class ScreenPreparationTests(unittest.TestCase):
    """Before each capture: wake the screen, dismiss a swipe keyguard, and stop at a secure one (#27)."""

    def test_a_swipe_keyguard_is_dismissed_before_the_launch_and_recorded(self):
        phone = FakePhone(showing=True, secure=False)
        with tempfile.TemporaryDirectory() as root:
            code, manifest, stderr = phone.run(root)
        self.assertEqual(code, 0)
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["screenPreparation"], {"wake": "KEYCODE_WAKEUP", "keyguard": "dismissed"})
        self.assertLess(phone.index(*WAKE), phone.index(*DISMISS))
        self.assertLess(phone.index(*DISMISS), phone.index(*LAUNCH))
        self.assertEqual(phone.calls.count(DISMISS), 1)
        self.assertEqual(stderr, "")

    def test_no_keyguard_showing_sends_no_dismissal(self):
        phone = FakePhone(showing=False, secure=True)  # a secure lock that is not showing (phone unlocked)
        with tempfile.TemporaryDirectory() as root:
            code, manifest, _ = phone.run(root)
        self.assertEqual(code, 0)
        self.assertEqual(manifest["screenPreparation"], {"wake": "KEYCODE_WAKEUP", "keyguard": "not-showing"})
        self.assertNotIn(DISMISS, phone.calls)
        self.assertLess(phone.index(*WAKE), phone.index(*LAUNCH))

    def test_a_secure_keyguard_stops_the_capture_without_touching_it(self):
        phone = FakePhone(showing=True, secure=True)
        with tempfile.TemporaryDirectory() as root:
            code, manifest, stderr = phone.run(root)
        self.assertEqual(code, 2)
        self.assertEqual(manifest["status"], "setup-failed")
        self.assertEqual(manifest["screenPreparation"], {"wake": "KEYCODE_WAKEUP", "keyguard": "secure"})
        self.assertFalse(manifest["records"])
        # Nothing is sent to the lock screen and the app is never started (or stopped again).
        self.assertNotIn(DISMISS, phone.calls)
        self.assertNotIn(LAUNCH, phone.calls)
        self.assertFalse(any(call[:1] == ("shell",) and call[1:2] == ("input",) and call != WAKE
                             for call in phone.calls))
        self.assertEqual(phone.calls.count(("shell", "am", "force-stop", capture.APP)), 1)
        self.assertIn("secure lock screen (PIN, pattern or password)", stderr)
        self.assertIn("never dismisses a secure lock", stderr)

    def test_an_unreadable_keyguard_state_dismisses_nothing(self):
        phone = FakePhone(showing=True, secure=False, dump="fixture")
        with tempfile.TemporaryDirectory() as root:
            code, manifest, _ = phone.run(root)
        self.assertEqual(code, 0)
        self.assertEqual(manifest["screenPreparation"], {"wake": "KEYCODE_WAKEUP", "keyguard": "unknown"})
        self.assertNotIn(DISMISS, phone.calls)

    def test_a_refused_keyguard_dump_is_unknown_not_a_failed_capture(self):
        phone = FakePhone(showing=True, secure=False)
        original = phone.adb

        def adb(*args):
            if args == ("shell", "dumpsys", "window", "policy"):
                phone.calls.append(args)
                raise subprocess.CalledProcessError(1, ["adb"])
            return original(*args)
        phone.adb = adb
        with tempfile.TemporaryDirectory() as root:
            code, manifest, _ = phone.run(root)
        self.assertEqual(code, 0)
        self.assertEqual(manifest["screenPreparation"], {"wake": "KEYCODE_WAKEUP", "keyguard": "unknown"})
        self.assertNotIn(DISMISS, phone.calls)

    def test_a_keyguard_that_stays_up_is_recorded_and_left_to_the_app_readiness_check(self):
        phone = FakePhone(showing=True, secure=False, dismissable=False)
        with tempfile.TemporaryDirectory() as root:
            code, manifest, _ = phone.run(root)
        self.assertEqual(manifest["screenPreparation"], {"wake": "KEYCODE_WAKEUP", "keyguard": "still-showing"})
        self.assertEqual(phone.calls.count(DISMISS), 1)
        self.assertEqual(phone.calls.count(("shell", "dumpsys", "window", "policy")),
                         1 + capture.KEYGUARD_DISMISS_CHECKS)
        self.assertEqual(code, 0)

    def test_keyguard_state_reads_only_the_delegate_flags(self):
        self.assertEqual(capture.keyguard_state(policy_dump(True, False)), {"showing": True, "secure": False})
        self.assertEqual(capture.keyguard_state(policy_dump(False, True)), {"showing": False, "secure": True})
        self.assertEqual(capture.keyguard_state("fixture"), {"showing": None, "secure": None})
        # Flags outside the KeyguardServiceDelegate block, or before it, are not the keyguard's.
        self.assertEqual(capture.keyguard_state("  showing=true\n  secure=false\nKeyguardServiceDelegate\n"),
                         {"showing": None, "secure": None})
        far = "KeyguardServiceDelegate\n" + "  x=1\n" * capture.KEYGUARD_BLOCK_LINES + "  showing=true\n"
        self.assertEqual(capture.keyguard_state(far), {"showing": None, "secure": None})


if __name__ == "__main__":
    unittest.main()
