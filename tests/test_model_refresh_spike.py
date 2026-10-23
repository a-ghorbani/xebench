"""The model-refresh spike bundles (docs/experiments/model-refresh.md), decided from the bundles alone.

These are committed evidence under evidence/, decided with this repository's reference data:
spike sessions are never publishable, the policy states each session's decode length, and the
GENIE-X NPU capture confirms its backend with geniex-npu-log-v1.
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


policy = load("publication_policy_spike", ROOT / "scripts/publication_policy.py")

NPU_CHECK = "49a216f37945edcc80b5d31a981206c1984e3293f16e4e47abd78adad2fe69e8"
SPIKE = "c12fb048be9bfbf75361b33b57c3fd158510a84c45c369d4c35b07410c7b3880"
GENIEX = "8c41c4e258ff4dd1fc149f165b8c45c7fe2cc3bb91f1db13b2db2acf078befb5"
GENIEX_NPU = "1792708796218-0-n00n5fcqhyj"  # the rerun after the staging fix
GENIEX_NPU_REFUSED = "1792708576907-0-b3373v2h66w"  # setup-failed: bundle directory unreadable
GEMMA3_OPENCL = "1792707379931-0-c1o069al88u"


def check(session, name):
    return next(item for item in session["decision"]["checks"] if item["check"] == name)


class ModelRefreshSpikeBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reports = {}
        for sha in (NPU_CHECK, SPIKE, GENIEX):
            # A fresh reference read from this checkout (policy.ROOT is the repository root).
            report = policy.bundle_report(ROOT / "evidence" / sha, sha)
            assert report["valid"], report["errors"]
            cls.reports[sha] = {item["sessionId"]: item for item in report["sessions"]}

    def test_spike_sessions_are_never_publishable(self):
        sessions = {**self.reports[SPIKE], **self.reports[GENIEX]}
        self.assertEqual(len(sessions), 17)
        for session_id, session in sessions.items():
            with self.subTest(session_id):
                self.assertTrue(session["facts"]["configurationFile"]["id"].startswith("spike-refresh-"))
                self.assertFalse(session["decision"]["publishable"])
                self.assertIn("spike-* configuration id", session["decision"]["refusal"])

    def test_the_board_configuration_sessions_of_the_npu_check_stay_publishable(self):
        for session_id, session in self.reports[NPU_CHECK].items():
            with self.subTest(session_id):
                self.assertEqual(session["facts"]["configurationFile"]["id"], "litertlm-gemma3-sm8850-npu-v2")
                self.assertTrue(session["decision"]["publishable"])
                self.assertEqual(session["decision"]["validation"], "verified", session["decision"]["checks"])

    def test_the_genie_x_npu_session_confirms_its_backend_from_the_bundle_alone(self):
        session = self.reports[GENIEX][GENIEX_NPU]
        facts = session["facts"]
        self.assertEqual((facts["engine"], facts["backend"]), ("genie-x", "npu"))
        self.assertEqual(facts["backendLog"]["rule"], "geniex-npu-log-v1")
        self.assertEqual(facts["backendLog"]["status"], "confirmed")
        self.assertTrue(all(not item["missing"] for item in facts["backendLog"]["repetitions"]))
        self.assertEqual(facts["backendEvidence"], "confirmed")
        backend = check(session, "backend")
        self.assertEqual(backend["outcome"], "pass")
        self.assertEqual(backend["detail"], "confirmed for npu by the app log (geniex-npu-log-v1)")
        # Every other check passes too; only the spike configuration keeps it off the board.
        self.assertEqual(session["decision"]["validation"], "verified", session["decision"]["checks"])
        self.assertFalse(session["decision"]["publishable"])

    def test_the_refused_first_genie_x_attempt_has_no_measured_repetition(self):
        session = self.reports[GENIEX][GENIEX_NPU_REFUSED]
        self.assertEqual(session["facts"]["measured"], 0)
        self.assertEqual(check(session, "decode")["detail"], "no measured repetition")

    def test_a_16_token_llama_cpp_session_states_its_decode_length(self):
        session = self.reports[SPIKE][GEMMA3_OPENCL]
        self.assertEqual((session["facts"]["engine"], session["facts"]["backend"]), ("llama.cpp", "gpu"))
        self.assertEqual(session["facts"]["decode"]["counts"], [16])
        decode = check(session, "decode")
        self.assertEqual(decode["detail"], "every repetition decoded 16 of 128 tokens, stopped at the end of turn")
        # The row statement a session gives (scripts/lib/bundles.mjs) says the same, never "decode 128 tok".
        doc = json.loads((ROOT / "evidence" / SPIKE / "sessions" / f"{GEMMA3_OPENCL}.json").read_text())
        script = ("import {decodeStated} from './scripts/lib/bundles.mjs';"
                  f"console.log(decodeStated({json.dumps(doc['record'])}));")
        result = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True,
                                cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "decode 16 of 128 tok, stopped at the end of turn")

    def test_fixed_decode_sessions_decoded_the_budget_in_every_repetition(self):
        sessions = [session for report in self.reports.values() for session in report.values()
                    if session["facts"]["decode"]["decodeMode"] == "fixed"]
        self.assertEqual(len(sessions), 6)
        for session in sessions:
            with self.subTest(session["sessionId"]):
                self.assertEqual(check(session, "decode"),
                                 {"check": "decode", "outcome": "pass",
                                  "detail": "fixed: every repetition decoded 128 tokens"})


if __name__ == "__main__":
    unittest.main()
