"""LiteRT-LM NPU backend confirmation from the app's log (scripts/backend_log.py, litert-npu-log-v1).

Covers the event patterns on lines as the POCO F8 Ultra logs them, sanitizing, the confirmation
rule (confirmed, and not confirmed for each missing line), the capture's pid-scoped collection,
the export into the capture document, verify refusing a relabelled event, and the publication
policy deriving `confirmed` from a bundle alone.
"""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import unittest.mock

import bundle_fixtures as fx
from test_export_evidence import ACCEL, HOST_DEVICE, capture, evidence, make_capture

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"scripts/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backend_log = load("backend_log")
policy = load("publication_policy")
REFERENCE = tempfile.TemporaryDirectory()

# The fixture repetitions' condition snapshots are all at this device time (ms).
T = 1767225600000
INSTALL = "/data/app/~~Yv4HL6SxAsf==/com.xebenchapp-WPf80pp==/lib/arm64"
# Messages as LiteRT-LM 0.18.0 and libcdsprpc log them on the POCO F8 Ultra (tag, message).
DISPATCH = ("litert", f"[litert_dispatch.cc:186] Loading shared library: {INSTALL}/libLiteRtDispatch_Qualcomm.so")
HTP = ("litert", "  BackendType              : Htp(2)")
CONTEXT = ("litert", "[litert_dispatch_device_context.cc:248] Creating new QNN context for bytecode 0x79d885d5f0 "
                     "(size 531357696)")
SKEL = ("com.xebenchapp", "vendor/qcom/proprietary/adsprpc/src/fastrpc_apps_user.c:3419: remote_handle64_open: opened "
                          "handle 0xb400007d02ce0b20 (remote 0x2ce530) for file:///libQnnHtpV81Skel.so?"
                          "qnn_2_50_0_skel_handle_invoke&_modver=1.0&_dom=cdsp on domain 3 (spawn time 21994 us, "
                          "load time 50982 us), refs 1")
OTHER = ("litert", "[npu_registry.cc:30] NPU accelerator registered.")


def line(time_ms, event, pid=123):
    tag, message = event
    return f"  {time_ms // 1000}.{time_ms % 1000:03d}  {pid}  4567 I {tag:<8}: {message}"


def gate_lines(time_ms=T - 1000):
    """What the output gate's NPU load logs once per process: dispatch library, HTP, skel, a context."""
    return [line(time_ms, DISPATCH), line(time_ms + 1, HTP), line(time_ms + 2, SKEL), line(time_ms + 3, CONTEXT)]


def setUpModule():
    policy.ROOT = fx.reference_root(Path(REFERENCE.name) / "repo")


def tearDownModule():
    REFERENCE.cleanup()


class PatternTests(unittest.TestCase):
    def test_each_line_is_its_event_and_sanitizes_to_one(self):
        for kind, event in (("dispatch-library", DISPATCH), ("htp-backend", HTP), ("qnn-context", CONTEXT),
                            ("htp-skel-cdsp", SKEL)):
            with self.subTest(kind):
                self.assertEqual(backend_log.classify(*event), kind)
                clean = backend_log.sanitize(event[1])
                self.assertEqual(backend_log.classify(event[0], clean), kind)
                self.assertNotIn("/data/", clean)
                self.assertNotRegex(clean, r"0x[0-9a-f]{4,}")
                self.assertEqual(evidence.privacy_findings({"line": clean}), [])
        self.assertIsNone(backend_log.classify(*OTHER))
        # Another tag (another library or process name) is not the event.
        self.assertIsNone(backend_log.classify("tflite", CONTEXT[1]))
        self.assertIsNone(backend_log.classify("litert", SKEL[1]))

    def test_only_the_app_process_lines_are_kept(self):
        text = "\n".join([line(T, CONTEXT), line(T, CONTEXT, pid=999), line(T, OTHER), "garbage"])
        self.assertEqual(backend_log.matching_lines(text, 123), [line(T, CONTEXT)])
        self.assertEqual(backend_log.parse_line(line(T + 7, HTP))[0], T + 7)

    def test_a_relabelled_event_is_invalid(self):
        event = backend_log.events_from_lines([line(T, CONTEXT)])[0]
        self.assertTrue(backend_log.valid_event(event))
        self.assertFalse(backend_log.valid_event({**event, "kind": "dispatch-library"}))
        self.assertFalse(backend_log.valid_event({**event, "line": CONTEXT[1]}))  # unsanitized
        with self.assertRaises(ValueError):
            backend_log.events_from_lines([line(T, OTHER)])


def npu_record():
    return copy.deepcopy(ACCEL["board-litert-fixed-gemma3-1b-sm8850-litertlm-npu"])


def log_of(lines):
    return {"rule": backend_log.RULE, "events": backend_log.events_from_lines(lines)}


def repetition_lines(record, events=(CONTEXT,)):
    """One line per event inside each measured repetition's window (its load)."""
    return [line(attempt["conditions"]["before"]["timestampMs"], event)
            for attempt in record["coldRuns"] if attempt["status"] == "complete" for event in events]


class ConfirmationTests(unittest.TestCase):
    def test_every_line_present_confirms_every_repetition(self):
        record = npu_record()
        result = backend_log.confirmation(record, log_of(gate_lines() + repetition_lines(record)))
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(len(result["repetitions"]), record["protocol"]["nColdRuns"])
        self.assertTrue(all(item["confirmed"] and not item["missing"] for item in result["repetitions"]))

    def test_each_missing_line_is_not_confirmed(self):
        record = npu_record()
        complete = gate_lines() + repetition_lines(record)
        for kind, event in (("dispatch-library", DISPATCH), ("htp-backend", HTP), ("htp-skel-cdsp", SKEL),
                            ("qnn-context", CONTEXT)):
            with self.subTest(kind):
                lines = [item for item in complete if event[1] not in item]
                result = backend_log.confirmation(record, log_of(lines))
                self.assertEqual(result["status"], "unconfirmed")
                self.assertTrue(all(kind in item["missing"] for item in result["repetitions"]))

    def test_a_repetition_without_its_own_context_is_not_confirmed(self):
        record = npu_record()
        # The gate's context alone, before the repetitions: no repetition shows its own load.
        result = backend_log.confirmation(record, log_of(gate_lines()))
        self.assertEqual(result["status"], "unconfirmed")
        self.assertEqual({tuple(item["missing"]) for item in result["repetitions"]}, {("qnn-context",)})
        # Contexts for every repetition but the last: only that one is unconfirmed.
        lines = gate_lines() + repetition_lines(record)[:-1]
        later = copy.deepcopy(record)
        last = [attempt for attempt in later["coldRuns"] if attempt["status"] == "complete"][-1]
        last["conditions"]["before"]["timestampMs"] = last["conditions"]["after"]["timestampMs"] = T + 60000
        result = backend_log.confirmation(later, log_of(lines))
        self.assertEqual([item["confirmed"] for item in result["repetitions"]][-1], False)
        self.assertEqual(result["status"], "unconfirmed")

    def test_the_process_lines_must_come_before_the_load(self):
        record = npu_record()
        lines = [line(T + 60000, DISPATCH), line(T + 60000, HTP), line(T, SKEL)] + repetition_lines(record)
        result = backend_log.confirmation(record, log_of(lines))
        self.assertEqual(result["status"], "unconfirmed")
        self.assertTrue(all(set(item["missing"]) == {"dispatch-library", "htp-backend"}
                            for item in result["repetitions"]))

    def test_the_rule_applies_to_litert_npu_sessions_with_a_log_only(self):
        lines = gate_lines()
        self.assertIsNone(backend_log.confirmation(copy.deepcopy(ACCEL["board-litert-fixed-gemma3-1b-int4-litertlm-cpu"]),
                                                   log_of(lines)))
        self.assertIsNone(backend_log.confirmation(copy.deepcopy(ACCEL["board-hexagon-llama32-q4km-hexagon"]),
                                                   log_of(lines)))
        self.assertIsNone(backend_log.confirmation(npu_record(), None))
        self.assertIsNone(backend_log.confirmation(npu_record(), {"rule": "another-rule", "events": []}))


class CaptureTests(unittest.TestCase):
    def test_the_capture_reads_only_the_filtered_app_log_and_keeps_only_events(self):
        record = npu_record()
        reference = {"name": "npu.json", "sha256": evidence.sha256(json.dumps(record).encode())}
        result = f"100.000 123 123 I ReactNativeJS: XEBENCH_RESULT_FILE {json.dumps(reference)}\n" \
                 "200.000 123 123 I ReactNativeJS: XEBENCH_DONE"
        backend = "\n".join(gate_lines() + [line(T, OTHER), line(T, CONTEXT, pid=999)])
        calls, now = [], [0]

        def adb(*args):
            calls.append(args)
            if args[:2] == ("shell", "pidof"):
                return "123"
            if args[:3] == ("logcat", "-b", "events"):
                return ""
            if args[0] == "exec-out":
                return json.dumps(record)
            if args[0] == "logcat":
                return backend if args[-len(backend_log.LOGCAT_FILTER):] == backend_log.LOGCAT_FILTER else result
            return "100.000" if args[:2] == ("shell", "date") else "fixture"

        def sleep(seconds):
            now[0] += seconds

        with tempfile.TemporaryDirectory() as root:
            capture.capture_device("private-serial", 5, root, adb=adb, monotonic=lambda: now[0], sleep=sleep)
            directory = next(Path(root).iterdir())
            manifest = json.loads((directory / "manifest.json").read_text())
            kept = (directory / "backend-log.txt").read_text().splitlines()
        backend_calls = [call for call in calls if call[-len(backend_log.LOGCAT_FILTER):] == backend_log.LOGCAT_FILTER]
        self.assertTrue(backend_calls)
        self.assertTrue(all("--pid=123" in call for call in backend_calls))
        self.assertEqual(kept, gate_lines())
        self.assertEqual(manifest["backendLog"]["rule"], backend_log.RULE)
        self.assertEqual(manifest["backendLog"]["lines"], 4)


class BundleTests(unittest.TestCase):
    NPU = "board-litert-fixed-gemma3-1b-sm8850-litertlm-npu"
    CPU = "board-litert-fixed-gemma3-1b-int4-litertlm-cpu"

    def bundle(self, root, lines):
        npu = fx.accel_record(self.NPU, "1767225600000-0-npu")
        cpu = fx.accel_record(self.CPU, "1767225600000-1-cpu")
        directory = make_capture(Path(root) / "results", [npu, cpu], device_info={**HOST_DEVICE, "model": "Fixture"},
                                 backend_log=None if lines is None else "\n".join(lines))
        report = evidence.export([directory], Path(root) / "bundles")
        return Path(report["bundle"]), report["evidence"]["bundleSha256"], npu

    def decision(self, root, lines):
        bundle, sha, _ = self.bundle(root, lines)
        report = policy.bundle_report(bundle, sha)
        self.assertTrue(report["valid"], report["errors"])
        return {item["sessionId"]: item for item in report["sessions"]}["1767225600000-0-npu"]

    def test_the_bundle_alone_confirms_the_npu_backend(self):
        with tempfile.TemporaryDirectory() as root:
            npu = fx.accel_record(self.NPU, "1767225600000-0-npu")
            session = self.decision(root, gate_lines() + repetition_lines(npu))
            self.assertEqual(session["facts"]["backendEvidence"], "confirmed")
            self.assertEqual(session["facts"]["backendLog"]["status"], "confirmed")
            backend = next(c for c in session["decision"]["checks"] if c["check"] == "backend")
            self.assertEqual(backend["outcome"], "pass")
            self.assertIn(backend_log.RULE, backend["detail"])
            self.assertEqual(session["decision"]["validation"], "verified", session["decision"]["checks"])

    def test_without_the_log_or_with_a_line_missing_the_backend_stays_unknown(self):
        npu = fx.accel_record(self.NPU, "1767225600000-0-npu")
        for name, lines in (("no log", None), ("no context", gate_lines()),
                            ("no skel", [item for item in gate_lines() + repetition_lines(npu) if "Skel" not in item])):
            with self.subTest(name), tempfile.TemporaryDirectory() as root:
                session = self.decision(root, lines)
                self.assertEqual(session["facts"]["backendEvidence"], "unknown")
                backend = next(c for c in session["decision"]["checks"] if c["check"] == "backend")
                self.assertEqual(backend["outcome"], "unknown")
                self.assertEqual(session["decision"]["validation"], "unverified")

    def test_the_export_sanitizes_and_verify_refuses_a_relabelled_event(self):
        with tempfile.TemporaryDirectory() as root:
            npu = fx.accel_record(self.NPU, "1767225600000-0-npu")
            bundle, _, _ = self.bundle(root, gate_lines() + repetition_lines(npu))
            path = next((bundle / "captures").iterdir())
            document = json.loads(path.read_text())
            self.assertEqual([event["kind"] for event in document["backendLog"]["events"]][:4],
                             ["dispatch-library", "htp-backend", "htp-skel-cdsp", "qnn-context"])
            self.assertNotIn("/data/app", path.read_text())
            self.assertTrue(evidence.verify(bundle)["valid"])
            document["backendLog"]["events"][-1]["kind"] = "htp-skel-cdsp"
            data = evidence.canonical(document)
            path.write_bytes(data)
            manifest = json.loads((bundle / "manifest.json").read_text())
            for item in manifest["files"]:
                if item["path"] == f"captures/{path.name}":
                    item.update(sha256=evidence.sha256(data), bytes=len(data))
            (bundle / "manifest.json").write_bytes(evidence.canonical(manifest))
            result = evidence.verify(bundle)
            self.assertFalse(result["valid"])
            self.assertTrue(any("backend log event" in error for error in result["errors"]), result["errors"])

    def test_a_tampered_local_log_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            npu = fx.accel_record(self.NPU, "1767225600000-0-npu")
            cpu = fx.accel_record(self.CPU, "1767225600000-1-cpu")
            directory = make_capture(Path(root) / "results", [npu, cpu], backend_log="\n".join(gate_lines()))
            with (directory / "backend-log.txt").open("a") as out:
                out.write(line(T, CONTEXT) + "\n")
            with self.assertRaises(evidence.Refused):
                evidence.export([directory], Path(root) / "bundles")


# geniex-npu-log-v1: messages as geniex-android 0.8.0 logs them under its JNI tag (GenieXSdk), written from
# the format strings in its shipped libgeniex_core.so (pending a device capture).
GX = backend_log.GENIEX_RULE
GX_RUNTIME = ("GenieXSdk", f"HTP runtime path: {INSTALL} (auto-resolved from geniex_core directory (flat layout))")
GX_ARCH = ("GenieXSdk", "HTP arch v81 detected.")
GX_GRAPHS = ("GenieXSdk", "HTP graphs will execute on 1 core (default; set num_cores or add htp_backend_ext_config.json "
                          "`cores` entries to request more)")
GX_PROMPT = ("GenieXSdk", "prompt: 532 tokens, first-turn BOS=false, ids start with [27, 91, 7265, 3575, 4326]")
GX_WINDOW = (GX_RUNTIME, GX_ARCH, GX_GRAPHS, GX_PROMPT)
GX_OTHER = ("GenieXSdk", "prefill_speed=3801.000000 tok/s, decoding_speed=33.400000 tok/s")
GX_NPU, GX_CPU = "geniex-qwen3-1.7b-w4a16-npu", "geniex-qwen3-1.7b-q40-cpu"


def gx_record():
    return copy.deepcopy(ACCEL[GX_NPU])


def gx_log(lines):
    return {"rule": GX, "events": backend_log.events_from_lines(lines, GX)}


def gx_gate_lines(time_ms=T - 1000):
    """The output gate's NPU load and requests: every window event and the skel, before the repetitions."""
    return [line(time_ms + index, event) for index, event in enumerate((GX_RUNTIME, GX_ARCH, SKEL, GX_GRAPHS,
                                                                        GX_PROMPT))]


class GenieXPatternTests(unittest.TestCase):
    def test_each_line_is_its_event_and_sanitizes_to_one(self):
        for kind, event in (("htp-runtime", GX_RUNTIME), ("htp-arch", GX_ARCH), ("htp-graphs", GX_GRAPHS),
                            ("prompt", GX_PROMPT), ("htp-skel-cdsp", SKEL)):
            with self.subTest(kind):
                self.assertEqual(backend_log.classify(*event, rule=GX), kind)
                clean = backend_log.sanitize(event[1])
                self.assertEqual(backend_log.classify(event[0], clean, rule=GX), kind)
                self.assertNotIn("/data/", clean)
                self.assertEqual(evidence.privacy_findings({"line": clean}), [])
        self.assertEqual(backend_log.sanitize(GX_RUNTIME[1]),
                         "HTP runtime path: _ (auto-resolved from geniex_core directory (flat layout))")
        self.assertIsNone(backend_log.classify(*GX_OTHER, rule=GX))

    def test_debug_builds_prefix_and_other_variants(self):
        prefixed = ("GenieXSdk", "[core/include/runtime.h:350:resolveHtpPaths] HTP arch v81 detected.")
        self.assertEqual(backend_log.classify(*prefixed, rule=GX), "htp-arch")
        multicore = ("GenieXSdk", "HTP multicore enabled: graphs will execute on 2 cores")
        self.assertEqual(backend_log.classify(*multicore, rule=GX), "htp-graphs")
        # A runtime the app did not package (an override) is not the event.
        override = ("GenieXSdk", f"HTP runtime path: {INSTALL} (overridden via GENIEX_QAIRT_LIB)")
        self.assertIsNone(backend_log.classify(*override, rule=GX))

    def test_rules_keep_their_own_events(self):
        for event in GX_WINDOW:
            self.assertIsNone(backend_log.classify(*event))  # not a LiteRT-LM event
            self.assertIsNone(backend_log.classify("litert", event[1], rule=GX))  # another tag
        for event in (DISPATCH, HTP, CONTEXT):
            self.assertIsNone(backend_log.classify(*event, rule=GX))
        self.assertEqual(backend_log.rule_for_engines({"genie-x", "llama.cpp"}), GX)
        self.assertEqual(backend_log.rule_for_engines({"litert-lm"}), backend_log.RULE)
        self.assertEqual(backend_log.rule_for_engines(set()), backend_log.RULE)
        text = "\n".join([line(T, GX_ARCH), line(T, CONTEXT), line(T, GX_OTHER), line(T, GX_ARCH, pid=999)])
        self.assertEqual(backend_log.matching_lines(text, 123), [line(T, GX_ARCH), line(T, CONTEXT)])
        event = backend_log.events_from_lines([line(T, GX_GRAPHS)], GX)[0]
        self.assertTrue(backend_log.valid_event(event, GX))
        self.assertFalse(backend_log.valid_event(event))  # not a litert-npu-log-v1 event
        self.assertFalse(backend_log.valid_event({**event, "kind": "prompt"}, GX))
        with self.assertRaises(ValueError):
            backend_log.events_from_lines([line(T, CONTEXT)], GX)


class GenieXConfirmationTests(unittest.TestCase):
    def test_every_window_event_and_the_skel_confirm_every_repetition(self):
        record = gx_record()
        result = backend_log.confirmation(record, gx_log(gx_gate_lines() + repetition_lines(record, GX_WINDOW)))
        self.assertEqual((result["rule"], result["status"]), (GX, "confirmed"))
        self.assertEqual(len(result["repetitions"]), record["protocol"]["nColdRuns"])

    def test_each_missing_event_is_not_confirmed(self):
        record = gx_record()
        complete = gx_gate_lines() + repetition_lines(record, GX_WINDOW)
        for kind, event in (("htp-runtime", GX_RUNTIME), ("htp-arch", GX_ARCH), ("htp-graphs", GX_GRAPHS),
                            ("prompt", GX_PROMPT), ("htp-skel-cdsp", SKEL)):
            with self.subTest(kind):
                lines = [item for item in complete if event[1] not in item]
                result = backend_log.confirmation(record, gx_log(lines))
                self.assertEqual(result["status"], "unconfirmed")
                self.assertTrue(all(item["missing"] == [kind] for item in result["repetitions"]))

    def test_the_gate_load_alone_confirms_no_repetition(self):
        result = backend_log.confirmation(gx_record(), gx_log(gx_gate_lines()))
        self.assertEqual(result["status"], "unconfirmed")
        self.assertEqual({tuple(item["missing"]) for item in result["repetitions"]},
                         {("htp-runtime", "htp-arch", "htp-graphs", "prompt")})

    def test_the_rule_applies_to_genie_x_npu_sessions_only(self):
        lines = gx_gate_lines()
        self.assertIsNone(backend_log.confirmation(copy.deepcopy(ACCEL[GX_CPU]), gx_log(lines)))
        self.assertIsNone(backend_log.confirmation(npu_record(), gx_log(lines)))  # a LiteRT-LM session
        self.assertIsNone(backend_log.confirmation(gx_record(), log_of(gate_lines())))  # the LiteRT-LM rule

    def test_the_rule_is_pending_device_confirmation(self):
        self.assertIn(GX, backend_log.PENDING_DEVICE_CONFIRMATION)
        self.assertNotIn(backend_log.RULE, backend_log.PENDING_DEVICE_CONFIRMATION)


class GenieXCaptureTests(unittest.TestCase):
    def test_a_genie_x_capture_keeps_its_rule_and_only_its_events(self):
        npu, cpu = fx.accel_record(GX_NPU, "1767225600000-0-npu"), fx.accel_record(GX_CPU, "1767225600000-1-cpu")
        lines = gx_gate_lines() + [line(T, CONTEXT), line(T, DISPATCH), line(T, GX_OTHER)]
        with tempfile.TemporaryDirectory() as root:
            directory = make_capture(Path(root) / "results", [npu, cpu], backend_log="\n".join(lines))
            manifest = json.loads((directory / "manifest.json").read_text())
            kept = (directory / "backend-log.txt").read_text().splitlines()
        self.assertEqual(manifest["backendLog"]["rule"], GX)
        self.assertEqual(kept, gx_gate_lines())


class GenieXBundleTests(unittest.TestCase):
    def bundle(self, root, lines):
        npu, cpu = fx.accel_record(GX_NPU, "1767225600000-0-npu"), fx.accel_record(GX_CPU, "1767225600000-1-cpu")
        directory = make_capture(Path(root) / "results", [npu, cpu], device_info={**HOST_DEVICE, "model": "Fixture"},
                                 backend_log="\n".join(lines))
        report = evidence.export([directory], Path(root) / "bundles")
        return Path(report["bundle"]), report["evidence"]["bundleSha256"], npu

    def test_the_bundle_reports_the_rule_but_a_pending_rule_confirms_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            npu = fx.accel_record(GX_NPU, "1767225600000-0-npu")
            bundle, sha, _ = self.bundle(root, gx_gate_lines() + repetition_lines(npu, GX_WINDOW))
            self.assertTrue(evidence.verify(bundle)["valid"])
            report = policy.bundle_report(bundle, sha)
            self.assertTrue(report["valid"], report["errors"])
            session = {item["sessionId"]: item for item in report["sessions"]}["1767225600000-0-npu"]
            self.assertEqual(session["facts"]["backendLog"]["status"], "confirmed")
            self.assertEqual(session["facts"]["backendEvidence"], "unknown")
            backend = next(c for c in session["decision"]["checks"] if c["check"] == "backend")
            self.assertEqual(backend["outcome"], "unknown")
            self.assertIn(f"rule {GX} is pending device confirmation", backend["detail"])

    def test_once_confirmed_on_a_device_the_rule_confirms_the_npu_backend(self):
        with tempfile.TemporaryDirectory() as root, \
                unittest.mock.patch.object(policy.backend_log, "PENDING_DEVICE_CONFIRMATION", frozenset()):
            npu = fx.accel_record(GX_NPU, "1767225600000-0-npu")
            bundle, sha, _ = self.bundle(root, gx_gate_lines() + repetition_lines(npu, GX_WINDOW))
            session = {item["sessionId"]: item for item in policy.bundle_report(bundle, sha)["sessions"]}[
                "1767225600000-0-npu"]
            self.assertEqual(session["facts"]["backendEvidence"], "confirmed")
            backend = next(c for c in session["decision"]["checks"] if c["check"] == "backend")
            self.assertEqual(backend["outcome"], "pass")
            self.assertIn(GX, backend["detail"])

    def test_verify_refuses_an_event_moved_to_another_rule(self):
        with tempfile.TemporaryDirectory() as root:
            npu = fx.accel_record(GX_NPU, "1767225600000-0-npu")
            bundle, _, _ = self.bundle(root, gx_gate_lines() + repetition_lines(npu, GX_WINDOW))
            path = next((bundle / "captures").iterdir())
            document = json.loads(path.read_text())
            self.assertEqual(document["backendLog"]["rule"], GX)
            self.assertNotIn("/data/app", path.read_text())
            document["backendLog"]["rule"] = backend_log.RULE
            data = evidence.canonical(document)
            path.write_bytes(data)
            manifest = json.loads((bundle / "manifest.json").read_text())
            for item in manifest["files"]:
                if item["path"] == f"captures/{path.name}":
                    item.update(sha256=evidence.sha256(data), bytes=len(data))
            (bundle / "manifest.json").write_bytes(evidence.canonical(manifest))
            result = evidence.verify(bundle)
            self.assertFalse(result["valid"])
            self.assertTrue(any("backend log event" in error for error in result["errors"]), result["errors"])


if __name__ == "__main__":
    unittest.main()
