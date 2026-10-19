"""The lab-book gate (scripts/check_public_text.mjs, docs/STYLE.md Board text).

It passes on the checked-in board text and fails on lab-book words and hashes planted in any
field the site renders. A lab row's asStated is not rendered, so it is exempt; a vendor row's
asStated is rendered and is scanned.
"""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check_public_text.mjs"
HASH64 = "c5116d95007ba6e68748d46e313728d978a891f72c12652f541c12679361a972"
HASH40 = "0b24d8c4e7986226227afd721985a86110dee16c"


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class PublicTextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.benchmarks = load(ROOT / "data/benchmarks.json")
        self.findings = load(ROOT / "data/findings.json")
        self.allowlist = load(ROOT / "configs/public-text-allowlist.json")

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, benchmarks=None, findings=None, allowlist=None):
        paths = {}
        for name, value, default in (("benchmarks", benchmarks, self.benchmarks), ("findings", findings, self.findings),
                                     ("allowlist", allowlist, self.allowlist)):
            path = self.dir / f"{name}.json"
            path.write_text(json.dumps(default if value is None else value))
            paths[name] = path
        return subprocess.run(["node", str(SCRIPT), "--benchmarks", str(paths["benchmarks"]), "--findings",
                               str(paths["findings"]), "--allowlist", str(paths["allowlist"])],
                              capture_output=True, text=True)

    def row(self, provenance):
        return next(row for row in self.benchmarks["rows"] if row["provenance"] == provenance)

    def test_the_checked_in_board_text_passes(self):
        result = subprocess.run(["node", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("board text is clean", result.stderr)

    def test_planted_terms_fail_with_where_and_why(self):
        finding_id = self.findings["findings"][0]["id"]
        cases = {
            "pending in a deck": ("findings", lambda f, b: f["findings"][0].update(deck="A new measurement is pending."),
                                  f"{finding_id}.deck: \"pending\""),
            "legacy in a headline": ("findings", lambda f, b: f["findings"][0].update(headline="Legacy row kept"),
                                     f"{finding_id}.headline: \"Legacy\""),
            "history in a deck": ("findings", lambda f, b: f["findings"][0].update(deck="We previously said otherwise."),
                                  "\"previously\" (history narration)"),
            "marked failed": ("findings", lambda f, b: f["findings"][0].update(headline="One result marked failed"),
                              "\"marked failed\""),
            "the gate": ("findings", lambda f, b: f["findings"][0].update(deck="Rows that pass the gate."), "\"gate\""),
            "a 64-hex hash": ("findings", lambda f, b: f["findings"][0].update(deck=f"Bundle {HASH64}."), "inline hash"),
            "harness in a device": ("benchmarks", lambda f, b: self.row_of(b, "measured").update(device="harness phone"),
                                    ".device: \"harness\""),
            "a 40-hex commit in a version": ("benchmarks", lambda f, b: self.row_of(b, "measured").setdefault("build", {}).update(
                engineVersion=f"llama.cpp {HASH40}"), ".build.engineVersion: \"" + HASH40),
            "a vendor note": ("benchmarks", lambda f, b: self.row_of(b, "vendor").update(asStated="superseded figure"),
                              ".asStated: \"superseded\""),
        }
        for name, (kind, change, expected) in cases.items():
            with self.subTest(name):
                findings, benchmarks = copy.deepcopy(self.findings), copy.deepcopy(self.benchmarks)
                change(findings, benchmarks)
                result = self.check(benchmarks=benchmarks, findings=findings)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(expected, result.stderr)
                self.assertIn("docs/STYLE.md", result.stderr)

    @staticmethod
    def row_of(benchmarks, provenance):
        return next(row for row in benchmarks["rows"] if row["provenance"] == provenance)

    def test_lab_row_notes_are_exempt(self):
        benchmarks = copy.deepcopy(self.benchmarks)
        self.row_of(benchmarks, "measured")["asStated"] = "legacy harness note; gate pending"
        result = self.check(benchmarks=benchmarks)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_removed_hexagon_finding_fails(self):
        old = subprocess.run(["git", "show", "8bb6ae5:data/findings.json"], capture_output=True, text=True, cwd=ROOT)
        if old.returncode != 0:
            self.skipTest("history not available")
        result = self.check(findings=json.loads(old.stdout))
        self.assertEqual(result.returncode, 1)
        self.assertIn("llama-cpp-hexagon-output.deck: \"harness\"", result.stderr)

    def test_the_allowlist_takes_justified_exceptions_and_no_stale_ones(self):
        findings = copy.deepcopy(self.findings)
        finding = findings["findings"][0]
        finding["deck"] = "Output stays correct past the gate of 128 tokens."
        entry = {"file": "data/findings.json", "where": f"{finding['id']}.deck", "match": "gate",
                 "reason": "a chunk boundary, not a lab label"}
        allowlist = {**self.allowlist, "entries": [entry]}
        self.assertEqual(self.check(findings=findings, allowlist=allowlist).returncode, 0)
        stale = self.check(allowlist=allowlist)  # the checked-in text has no such term
        self.assertEqual(stale.returncode, 1)
        self.assertIn("matches nothing; remove it", stale.stderr)
        for broken in ({**allowlist, "entries": [{**entry, "reason": ""}]}, {"entries": []},
                       {**allowlist, "entries": [{**entry, "extra": 1}]}):
            with self.subTest(broken=broken):
                self.assertEqual(self.check(findings=findings, allowlist=broken).returncode, 1)

    def test_usage(self):
        result = subprocess.run(["node", str(SCRIPT), "--everything"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
