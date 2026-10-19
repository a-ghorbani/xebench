"""Contract 1.3: dateBasis on rows without versions (data/benchmarks.schema.json).

Each consumer fixture under tests/fixtures/contract-1.3/ is accepted or refused by
scripts/validate_data.mjs exactly as its index entry says.
"""
import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/contract-1.3"
REASONS = {
    "date-basis-in-1.2.json": "dateBasis needs schemaVersion 1.3",
    "date-basis-on-verified-row.json": "dateBasis is only valid on legacy rows",
    "date-basis-on-vendor-row.json": "dateBasis is only valid on legacy rows",
    "date-basis-without-source-date.json": "dateBasis requires sourceDate",
    "unknown-date-basis.json": "is not one of",
    "quant-bits-in-1.2.json": "quantBits needs schemaVersion 1.3",
    "wrong-quant-bits.json": "is not the bit width of quant",
}


class Contract13Tests(unittest.TestCase):
    def test_consumer_fixtures(self):
        index = json.loads((FIXTURES / "index.json").read_text())
        self.assertEqual({entry["file"] for entry in index["fixtures"]},
                         {path.name for path in FIXTURES.glob("*.json")} - {"index.json"})
        for entry in index["fixtures"]:
            with self.subTest(fixture=entry["file"]):
                result = subprocess.run(["node", str(ROOT / "scripts/validate_data.mjs"), str(FIXTURES / entry["file"])],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 1 if entry["producerMustNotEmit"] else 0, result.stderr)
                if entry["producerMustNotEmit"]:
                    self.assertIn(REASONS[entry["file"]], result.stderr)

    def test_the_contract_version_follows_the_rows(self):
        script = ("import {contractVersionFor} from './scripts/lib/publication.mjs';"
                  "console.log(JSON.stringify([contractVersionFor([{dateBasis: 'measured'}]),"
                  " contractVersionFor([{build: {runtimes: []}}]), contractVersionFor([{}]), contractVersionFor([{}], '1.3')]));")
        result = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), ["1.3", "1.2", "1.1", "1.3"])


if __name__ == "__main__":
    unittest.main()
