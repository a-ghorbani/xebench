"""The board before the first bundle publication, as a fixed starting state for tests.

tests/fixtures/board-before-publication/ holds data/benchmarks.json at contract 1.1 (legacy,
vendor and one failed row) and the empty data/sessions/index.json, byte for byte as on main at
2adb00a. Producer, migration and correction tests start from it, so they test those paths
independently of the rows published since. Tests of the checked-in data read data/ itself.
"""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "tests/fixtures/board-before-publication"
BENCHMARKS = DIR / "benchmarks.json"
INDEX = DIR / "index.json"


def seed(root, dated=False):
    """Replace a workspace's data/benchmarks.json and data/sessions/index.json with the fixture.
    dated=True also gives its rows without versions their month (scripts/date_legacy_rows.mjs,
    run from the workspace), as the measurement-set policy needs."""
    root = Path(root)
    shutil.copyfile(BENCHMARKS, root / "data/benchmarks.json")
    shutil.copyfile(INDEX, root / "data/sessions/index.json")
    if dated:
        result = subprocess.run(["node", str(root / "scripts/date_legacy_rows.mjs")], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(result.stderr)
