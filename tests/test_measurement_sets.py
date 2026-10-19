"""Measurement sets (configs/board.json, scripts/lib/sets.mjs; docs/RUN-CONDITIONS.md).

Each engine's board rows come from one declared set; a new set is displayed only once every
displayed key of the engine has a session in it or is retired; a failed row is never
displayed; everything that leaves the board stays in data/sessions/index.json.

Workspace tests run against a temporary copy of scripts/ + data/ with a fixture board
(tests/bundle_fixtures.py board_config); CheckedIn tests read the repository's own files.
"""
import copy
import json
from pathlib import Path
import subprocess
import unittest

import bundle_fixtures as fx
from test_bundle_publication import LEGACY_KEY, Workspace, key_of, load

ROOT = Path(__file__).resolve().parents[1]
PHONE = "llama.cpp|cpu|android|Google Fixture Phone|Llama 3.2 1B|Q4_K_M|measured"
TABLET = "llama.cpp|cpu|android|Google Fixture Tablet|Llama 3.2 1B|Q4_K_M|measured"


def next_version(record):
    """The record as run with the next llama.rn (the binding the app reports and its distribution)."""
    record["runtimeVersions"]["llamaRn"] = "0.0.1-fixture"
    record["effectiveSettings"]["distribution"]["version"] = "0.0.1-fixture"
    record["engineVersion"] = "llama.rn 0.0.1-fixture"
    return record

# The month of every July 2026 row without versions and what it rests on (scripts/date_legacy_rows.mjs).
JULY_ROWS = {
    "executorch|cpu|android|Google Pixel 9|Llama 3.2 1B|SpinQuant 4-bit|measured": "measured",
    "litert-lm|cpu|android|Google Pixel 9|Qwen2.5 1.5B|Q8|measured": "measured",
    "llama.cpp|cpu|android|Google Pixel 9|Qwen2.5 1.5B|Q8_0|measured": "measured",
    "executorch|cpu|android|OnePlus 6|Llama 3.2 1B|SpinQuant 4-bit|measured": "measured",
    "llama.cpp|cpu|android|OnePlus 6|Llama 3.2 1B|Q4_K_M|measured": "measured",
    "llama.cpp|cpu|android|OnePlus 6|Qwen2.5 1.5B|Q8_0|measured": "measured",
    "litert-lm|cpu|android|POCO F8 Ultra|Gemma 3 1B|int4|measured": "measured",
    "llama.cpp|cpu|android|POCO F8 Ultra|Gemma 3 1B|Q4_0 (QAT)|measured": "measured",
    "executorch|cpu|android|POCO F8 Ultra|Llama 3.2 1B|SpinQuant 4-bit|measured": "measured",
    "genie-x|npu|android|POCO F8 Ultra|Llama 3.2 1B|W4A16|measured": "measured",
    "litert-lm|cpu|android|POCO F8 Ultra|Qwen2.5 1.5B|Q8|measured": "measured",
    "llama.cpp|cpu|android|POCO F8 Ultra|Qwen2.5 1.5B|Q8_0|measured": "measured",
    "llama.cpp|gpu|android|POCO F8 Ultra|Qwen2.5 1.5B|Q8_0|measured": "measured",
    "litert-lm|cpu|android|Samsung Galaxy S23|Gemma 3 1B|int4|measured": "first-published",
    "llama.cpp|cpu|android|Samsung Galaxy S23|Gemma 3 1B|Q4_0 (QAT)|measured": "first-published",
    "executorch|cpu|android|Samsung Galaxy S23|Llama 3.2 1B|SpinQuant 4-bit|measured": "measured",
    "genie-x|cpu|android|Samsung Galaxy S23|Qwen2.5 1.5B|Q8_0|measured": "measured",
    "litert-lm|cpu|android|Samsung Galaxy S23|Qwen2.5 1.5B|Q8|measured": "measured",
    "llama.cpp|cpu|android|Samsung Galaxy S23|Qwen2.5 1.5B|Q8_0|measured": "measured",
    "llama.cpp|gpu|android|Samsung Galaxy S23|Qwen2.5 1.5B|Q8_0|measured": "measured",
}


class CheckedInSetTests(unittest.TestCase):
    """The repository's board: one set per engine, July rows dated, the Hexagon row retired."""

    def setUp(self):
        self.data = load(ROOT / "data/benchmarks.json")
        self.index = load(ROOT / "data/sessions/index.json")
        self.board = load(ROOT / "configs/board.json")

    def test_july_rows_carry_their_month_and_its_basis(self):
        july = {key_of(row): row for row in self.data["rows"] if row.get("validation") == "legacy"}
        self.assertEqual(set(july), set(JULY_ROWS))
        for key, basis in JULY_ROWS.items():
            with self.subTest(key):
                self.assertEqual((july[key]["sourceDate"], july[key]["dateBasis"]), ("2026-07", basis))
                self.assertNotIn("build", july[key], "no version is backfilled")
        self.assertEqual(self.data["schemaVersion"], "1.3")
        result = subprocess.run(["node", str(ROOT / "scripts/date_legacy_rows.mjs"), "--check"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_every_displayed_row_is_in_its_engines_set(self):
        llama = self.board["engines"]["llama.cpp"]
        carried = set(llama["carryover"]["keys"])
        for row in self.data["rows"]:
            if row["provenance"] != "measured":
                continue
            with self.subTest(key_of(row)):
                self.assertIn("sourceDate", row, "every measured row shows its month")
                self.assertNotEqual(row.get("validation"), "failed")
                engine_set = self.board["engines"][row["engine"]]
                if "evidence" in row:
                    self.assertEqual(engine_set["set"], "sessions")
                    self.assertEqual((row["build"]["engineVersion"], row["build"]["bindingVersion"]),
                                     (engine_set["engineVersion"], engine_set["bindingVersion"]))
                    allowed = {item["id"]: item["backends"] for item in engine_set["configurations"]}
                    self.assertIn(row["backend"], allowed[row["configurationId"]])
                    self.assertGreaterEqual(row["sourceDate"], engine_set["measuredFrom"][:7])
                elif engine_set["set"] == "sessions":
                    self.assertIn(key_of(row), carried)
                else:
                    self.assertEqual(row["sourceDate"], engine_set["measured"])

    def test_the_llama_cpp_campaign_state(self):
        """Nine rc.7 CPU rows displayed; nine July rows carried over until the campaign lands."""
        llama = [row for row in self.data["rows"] if row["engine"] == "llama.cpp"]
        versions = {(row.get("build") or {}).get("bindingVersion") for row in llama}
        self.assertEqual(versions, {"llama.rn 0.13.0-rc.7", None})
        self.assertEqual(len([row for row in llama if "evidence" in row]), 9)
        self.assertEqual(sorted(key_of(row) for row in llama if "evidence" not in row),
                         self.board["engines"]["llama.cpp"]["carryover"]["keys"])

    def test_board_labels(self):
        """Lab rows say "xebench" and name their chip from configs/devices.json; vendor rows name a
        chip only where the device ships with one."""
        devices = {entry["name"]: entry["chipName"] for entry in load(ROOT / "configs/devices.json")["devices"]}
        rows = self.data["rows"] + [s["row"] for entry in self.index["keys"] for s in entry["sessions"]]
        for row in rows:
            with self.subTest(key_of(row)):
                if row["provenance"] == "measured":
                    self.assertEqual(row["sourceName"], "xebench")
                    self.assertEqual(row["chipName"], devices[row["device"]])
                elif row["device"] in ("Samsung Galaxy S22", "Samsung Galaxy S24", "Samsung Galaxy S26 Ultra"):
                    self.assertNotIn("chipName", row, "the chip depends on the market")
                else:
                    self.assertIn("chipName", row)
        self.assertNotIn("MacBook Pro (M4 Max)", {row["device"] for row in self.data["rows"]})
        bits = {"Q4_0": 4, "Q4_K_M": 4, "Q4_0 (QAT)": 4, "Q8_0": 8, "Q8": 8, "int4": 4, "W4A16": 4, "SpinQuant 4-bit": 4,
                "4-bit groupwise": 4, "4-bit groupwise + 4-bit embedding": 4, "4-bit QAT": 4, "4-bit QAT + LoRA": 4, "BF16": 16}
        for row in rows:
            with self.subTest(key_of(row), field="quantBits"):
                self.assertEqual(row.get("quantBits"), bits.get(row.get("quant")))
        result = subprocess.run(["node", str(ROOT / "scripts/migrate_board_labels.mjs"), "--check"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_hexagon_row_is_retired_into_the_history(self):
        self.assertNotIn(fx.NPU_KEY, {key_of(row) for row in self.data["rows"]})
        self.assertEqual([item["key"] for item in self.board["retiredKeys"]], [fx.NPU_KEY])
        entry = next(entry for entry in self.index["keys"] if entry["key"] == fx.NPU_KEY)
        self.assertEqual(entry["replacedRows"][0]["row"]["validation"], "failed")
        findings = load(ROOT / "data/findings.json")
        self.assertNotIn("llama-cpp-hexagon-output", {finding["id"] for finding in findings["findings"]})

    def test_applying_the_board_changes_nothing(self):
        result = subprocess.run(["node", str(ROOT / "scripts/aggregate.mjs"), "--apply-board", "--dry-run"],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no change", result.stdout)


class SetWorkspace(Workspace):
    IDS = ["cpu-reference-v2", "f8-gemma3-accelerators-v1"]

    def setUp(self):
        super().setUp()
        self.board()
        result = self.aggregate("--apply-board", "--update")  # the seeded board, applied: the failed row retired
        self.assertEqual(result.returncode, 0, result.stderr)

    def board(self, **options):
        return fx.write_board(self.root, self.IDS, **options)

    def next_set(self, board):
        """The board with llama.cpp's set moved to the next binding version."""
        board = copy.deepcopy(board)
        board["engines"]["llama.cpp"]["bindingVersion"] = "llama.rn 0.0.1-fixture"
        board["engines"]["llama.cpp"]["carryover"]["bindingVersion"] = "llama.rn 0.0.1-fixture"
        return board

    def save(self, board):
        (self.root / "configs/board.json").write_text(json.dumps(board, indent=2) + "\n")

    def rows_text(self):
        return json.dumps(load(self.canonical)["rows"])


class StagingAndCompletenessTests(SetWorkspace):
    def setUp(self):
        super().setUp()
        self.bundle(fx.cpu_record("complete", "1767225600000-0-phone", device="Fixture Phone"),
                    fx.cpu_record("complete", "1767225600000-1-tablet", device="Fixture Tablet"))
        self.bundle(next_version(fx.cpu_record("complete", "1767312000000-0-next", timestamp="2026-01-02T00:00:00.000Z",
                                               device="Fixture Phone")))
        self.publish("1767225600000-0-phone", "1767225600000-1-tablet")

    def test_a_session_of_another_version_is_staged_only_with_the_flag(self):
        rows = self.rows_text()
        self.assert_rejected("binding version llama.rn 0.0.1-fixture is not llama.cpp's set", "--from-bundles",
                             self.root / "evidence", "--update", "--session", "1767312000000-0-next")
        result = self.publish("1767312000000-0-next", flags=["--stage"])
        self.assertIn("--stage: publishing 1767312000000-0-next into the history only", result.stderr)
        self.assertEqual(self.rows_text(), rows, "a staged session changes nothing on the board")
        entry = self.entry(PHONE)
        self.assertEqual(entry["displayed"], "1767225600000-0-phone")
        self.assertEqual([s["sessionId"] for s in entry["sessions"]], ["1767225600000-0-phone", "1767312000000-0-next"])
        self.assert_valid_data()

    def test_an_incomplete_new_set_is_refused_and_a_complete_one_displayed(self):
        self.publish("1767312000000-0-next", flags=["--stage"])
        board = self.next_set(load(self.root / "configs/board.json"))
        self.save(board)
        # The tablet is displayed from the old set and has no session in the new one: refused.
        result = self.assert_rejected(f"row {TABLET} is displayed but has no verified or unverified session in llama.cpp",
                                      "--apply-board", "--update")
        self.assertIn("list the key in configs/board.json retiredKeys", result.stderr)
        self.assertNotEqual(self.node("validate_data.mjs").returncode, 0, "the edited board is not applied yet")
        # Retiring the tablet completes the set; one run applies it.
        board["retiredKeys"] = sorted(board["retiredKeys"] + [
            {"key": TABLET, "reason": "fixture: not re-measured", "retiredAt": "2026-10-19"}], key=lambda item: item["key"])
        self.save(board)
        result = self.aggregate("--apply-board", "--update")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"{TABLET}: retired: the row leaves the board", result.stderr)
        rows = self.rows()
        self.assertEqual(rows[PHONE]["build"]["bindingVersion"], "llama.rn 0.0.1-fixture")
        self.assertNotIn(TABLET, rows)
        # The history keeps everything: the old phone session and the tablet session.
        self.assertEqual(len(self.entry(PHONE)["sessions"]), 2)
        tablet = self.entry(TABLET)
        self.assertEqual((tablet["displayed"], tablet["rule"], len(tablet["sessions"])), (None, "retired", 1))
        self.assert_valid_data()


class CampaignTests(SetWorkspace):
    """The llama.cpp campaign: July rows carried over, then re-measured or retired in one publication."""

    def test_the_campaign_finishes_in_one_publication(self):
        board = load(self.root / "configs/board.json")
        carried = board["engines"]["llama.cpp"]["carryover"]["keys"]
        self.assertIn(LEGACY_KEY, carried)
        self.bundle(fx.cpu_record("complete", "1767225600000-0-pixel", device="Pixel 9"))
        # The campaign PR: drop the carryover, retire every July row not re-measured, publish the rest.
        del board["engines"]["llama.cpp"]["carryover"]
        self.save(board)
        result = self.assert_rejected("has no version (measured 2026-07) and is not in llama.cpp", "--from-bundles",
                                      self.root / "evidence", "--update", "--replace-legacy", "--session", "1767225600000-0-pixel")
        retired = [{"key": key, "reason": "fixture: not re-measured", "retiredAt": "2026-10-19"}
                   for key in carried if key != LEGACY_KEY]
        board["retiredKeys"] = sorted(board["retiredKeys"] + retired, key=lambda item: item["key"])
        self.save(board)
        before = {key: row for key, row in self.rows().items() if key in carried}
        result = self.publish("1767225600000-0-pixel", flags=["--replace-legacy"])
        rows = self.rows()
        llama = {key: row for key, row in rows.items() if row["engine"] == "llama.cpp"}
        self.assertEqual(set(llama), {LEGACY_KEY})
        self.assertEqual(llama[LEGACY_KEY]["build"]["bindingVersion"], "llama.rn 0.0.0-fixture")
        index = load(self.index)
        kept = {entry["key"]: entry["replacedRows"] for entry in index["keys"] if entry["replacedRows"]}
        for key, row in before.items():
            with self.subTest(key):
                reason = "replace-legacy" if key == LEGACY_KEY else "retired"
                self.assertEqual([(item["reason"], item["row"]) for item in kept[key]], [(reason, row)])
        self.assert_valid_data()

    def test_a_carried_over_row_is_replaced_by_a_session_of_the_set(self):
        self.bundle(fx.cpu_record("complete", "1767225600000-0-pixel", device="Pixel 9"))
        result = self.publish("1767225600000-0-pixel", flags=["--replace-legacy"])
        self.assertIn(f"{LEGACY_KEY}: replaces the legacy row", result.stderr)
        self.assert_valid_data()


class BackendGuardTests(SetWorkspace):
    """A displayed configuration lists the backends it displays: an accelerator configuration's
    CPU reference run (same row key as a CPU board row) never reaches the board."""

    IDS = [{"id": "cpu-reference-v2", "backends": ["cpu"]}, {"id": "f8-gemma3-accelerators-v1", "backends": ["npu"]}]

    def test_a_run_on_an_unlisted_backend_is_history_only(self):
        self.bundle(fx.accel_record("f8-llama-cpu", "1767225600000-0-ref", device="Fixture Phone"),
                    fx.accel_record("f8-llama-hexagon", "1767225600000-1-npu", device="Fixture Phone"))
        self.assert_rejected("backend cpu is not displayed for configuration f8-gemma3-accelerators-v1 (npu only",
                             "--from-bundles", self.root / "evidence", "--update", "--session", "1767225600000-0-ref")
        result = self.publish("1767225600000-0-ref", "1767225600000-1-npu", flags=["--allow-unlisted-configuration"])
        self.assertIn("--allow-unlisted-configuration: publishing 1767225600000-0-ref into the history only", result.stderr)
        rows = self.rows()
        cpu_key = "llama.cpp|cpu|android|Google Fixture Phone|Gemma 3 1B|Q4_0 (GGUF)|measured"
        self.assertNotIn(cpu_key, rows)
        self.assertEqual(self.entry(cpu_key)["rule"], "no-session-in-set")
        self.assertEqual(rows["llama.cpp|npu|android|Google Fixture Phone|Gemma 3 1B|Q4_0 (GGUF)|measured"]["validation"],
                         "verified")
        self.assert_valid_data()

    def test_selection_never_takes_a_reference_run_over_a_board_row(self):
        """A newer verified CPU session of an accelerator configuration, same key as a displayed CPU
        row of the CPU configuration: the CPU configuration's session stays displayed."""
        script = (
            "import {normalizeBoard} from './scripts/lib/board.mjs';"
            "import {selectSession} from './scripts/lib/history.mjs';"
            "const board = normalizeBoard({engines: {'llama.cpp': {set: 'sessions', engineVersion: 'v', measuredFrom: '2026-01-01',"
            " configurations: [{id: 'cpu-reference-v3', backends: ['cpu']}, {id: 'llamacpp-llama32-q4km-hexagon-v1', backends: ['npu']}]}},"
            " retiredKeys: []});"
            "const row = (id) => ({engine: 'llama.cpp', backend: 'cpu', configurationId: id, build: {engineVersion: 'v'}});"
            "const sessions = [{sessionId: 'board', timestampIso: '2026-10-18T00:00:00Z', validation: 'verified', row: row('cpu-reference-v3')},"
            " {sessionId: 'gate-reference', timestampIso: '2026-10-19T00:00:00Z', validation: 'verified', row: row('llamacpp-llama32-q4km-hexagon-v1')}];"
            "console.log(JSON.stringify(selectSession(sessions, board, 'k')));")
        result = subprocess.run(["node", "--input-type=module", "-e", script], capture_output=True, text=True, cwd=self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"sessionId": "board", "rule": "newest-verified"})

    def test_backends_are_validated(self):
        board = load(self.root / "configs/board.json")
        for name, backends in (("empty", []), ("unknown", ["tpu"]), ("duplicate", ["cpu", "cpu"])):
            with self.subTest(name):
                broken = copy.deepcopy(board)
                broken["engines"]["llama.cpp"]["configurations"][0]["backends"] = backends
                self.save(broken)
                result = self.node("validate_data.mjs")
                self.assertEqual(result.returncode, 1)
                self.assertIn("backends must be a non-empty list of distinct backends", result.stderr)
        broken = copy.deepcopy(board)
        broken["engines"]["llama.cpp"]["configurations"] = ["cpu-reference-v2"]
        self.save(broken)
        self.assertIn("must be an object {id, backends}", self.node("validate_data.mjs").stderr)


class DisplayRuleTests(SetWorkspace):
    def test_a_set_of_another_month_displays_no_july_row(self):
        board = load(self.root / "configs/board.json")
        board["engines"]["executorch"]["measured"] = "2026-06"
        self.save(board)
        result = self.node("validate_data.mjs")
        self.assertEqual(result.returncode, 1)
        self.assertIn("is not in executorch: rows without versions, measured 2026-06", result.stderr)

    def test_rows_without_versions_are_frozen(self):
        """Only the dated July 2026 rows, unchanged, are displayed without versions."""
        data = load(self.canonical)
        july = next(row for row in data["rows"] if row["engine"] == "executorch" and row["provenance"] == "measured")
        made_up = {**july, "device": "Fake Phone", "ppTps": 999.0}
        cases = {
            "an unknown row": (made_up, "is not one of the July 2026 rows without versions"),
            "an unknown row without validation": ({k: v for k, v in made_up.items() if k not in ("validation", "dateBasis")},
                                                  "is not one of the July 2026 rows without versions"),
            "unverified without evidence": ({k: v for k, v in {**made_up, "validation": "unverified"}.items() if k != "dateBasis"},
                                            "is unverified but no published session backs it"),
            "changed numbers": ({**july, "tgTps": 99.0}, "are not the dated"),
            "changed run count": ({**july, "sampleCount": 5}, "are not the published"),
            "changed time to first token": ({**july, "ttftMs": 1.0}, "are not the published"),
            "a memory figure": ({**july, "memoryMb": 512.0}, "are not the published"),
            "no month basis": ({k: v for k, v in july.items() if k != "dateBasis"}, "does not carry its month"),
        }
        for name, (row, reason) in cases.items():
            with self.subTest(name):
                changed = copy.deepcopy(data)
                rows = [r for r in changed["rows"] if key_of(r) != key_of(july)] if key_of(row) == key_of(july) else changed["rows"]
                changed["rows"] = rows + [row]
                changed["rowCount"] = len(changed["rows"])
                self.canonical.write_text(json.dumps(changed, indent=2) + "\n")
                result = self.node("validate_data.mjs")
                self.assertEqual(result.returncode, 1, name)
                self.assertIn(reason, result.stderr)

    def test_an_engine_without_a_set_is_refused(self):
        board = load(self.root / "configs/board.json")
        del board["engines"]["genie-x"]
        self.save(board)
        result = self.assert_rejected("configs/board.json declares no measurement set for genie-x", "--apply-board", "--update")
        self.assertIn("not modified", result.stderr)

    def test_retiring_moves_a_row_to_the_history_byte_for_byte(self):
        key = next(key for key in self.rows() if key.startswith("genie-x|npu|"))
        row = self.rows()[key]
        board = load(self.root / "configs/board.json")
        board["retiredKeys"] = sorted(board["retiredKeys"] + [{"key": key, "reason": "fixture", "retiredAt": "2026-10-19"}],
                                      key=lambda item: item["key"])
        self.save(board)
        result = self.aggregate("--apply-board", "--update")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(key, self.rows())
        [kept] = self.entry(key)["replacedRows"]
        self.assertEqual((kept["reason"], kept["note"], kept["retiredAt"]), ("retired", "fixture", "2026-10-19"))
        self.assertEqual(json.dumps(kept["row"]), json.dumps(row))
        self.assert_valid_data()
        again = self.aggregate("--apply-board", "--update")
        self.assertIn("nothing to write", again.stderr)

    def test_a_retired_key_that_names_nothing_is_refused(self):
        """A typo in retiredKeys would retire nothing and leave the intended row displayed."""
        key = next(key for key in self.rows() if key.startswith("genie-x|npu|"))
        board = load(self.root / "configs/board.json")
        typo = key.replace("W4A16", "W4A61")
        board["retiredKeys"] = sorted(board["retiredKeys"] + [{"key": typo, "reason": "fixture", "retiredAt": "2026-10-19"}],
                                      key=lambda item: item["key"])
        self.save(board)
        result = self.node("validate_data.mjs")
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"retiredKeys: {typo} names no published row or session", result.stderr)
        self.assert_rejected("names no published row or session", "--apply-board", "--update")

    def test_a_malformed_board_is_refused(self):
        good = load(self.root / "configs/board.json")
        llama = good["engines"]["llama.cpp"]
        cases = {
            "unknown set": lambda b: b["engines"]["executorch"].update(set="latest"),
            "bad month": lambda b: b["engines"]["executorch"].update(measured="July 2026"),
            "no engine version": lambda b: b["engines"]["llama.cpp"].pop("engineVersion"),
            "bad measuredFrom": lambda b: b["engines"]["llama.cpp"].update(measuredFrom="2026-02-30"),
            "retired and carried": lambda b: b["retiredKeys"].append(
                {"key": llama["carryover"]["keys"][-1], "reason": "x", "retiredAt": "2026-10-19"}),
            "retired without reason": lambda b: b["retiredKeys"][0].pop("reason"),
            "carryover of another engine": lambda b: b["engines"]["llama.cpp"]["carryover"]["keys"].insert(
                0, "executorch|cpu|android|OnePlus 6|Llama 3.2 1B|SpinQuant 4-bit|measured"),
            "schema 1": lambda b: b.update(schemaVersion=1),
            "carryover of another set": lambda b: b["engines"]["llama.cpp"]["carryover"].update(
                bindingVersion="llama.rn 9.9.9"),
        }
        for name, change in cases.items():
            with self.subTest(name):
                board = copy.deepcopy(good)
                change(board)
                self.save(board)
                result = self.node("validate_data.mjs")
                self.assertEqual(result.returncode, 1, name)
                self.assertIn("configs/board.json", result.stderr)

    def test_usage(self):
        for args in (["--apply-board"], ["--apply-board", "--update", "--dry-run"],
                     ["--apply-board", "--update", "--stage"], ["--apply-board", "--dry-run", "--session", "x"]):
            with self.subTest(args=args):
                self.assertEqual(self.aggregate(*args).returncode, 2)


if __name__ == "__main__":
    unittest.main()
