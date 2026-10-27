"""Build the public dashboard exclusively from the deterministic test fixtures."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from market_risk.dashboard import save_dashboard
from market_risk.data import canonical
from test_execution import ExecutionTests


def build_demo(output: Path) -> dict:
    """Fit, evaluate and reprice invented data, never accepting an external data path."""
    if output.exists():
        raise FileExistsError("Use a new demo output directory")
    ExecutionTests.setUpClass()
    fixture = ExecutionTests()
    fixture.setUp()
    try:
        scenarios_path = fixture.inputs / "scenarios.json"
        definitions = json.loads(scenarios_path.read_bytes())
        historical = 0
        for scenario in definitions["scenarios"]:
            if scenario["mode"] == "historical_replay":
                historical += 1
                scenario["id"] = f"synthetic_replay_{historical}"
                scenario["narrative"] = (
                    "Replay of fictional observations over the labelled dates. "
                    "This is not a reconstruction of an actual historical event."
                )
        scenarios_path.write_bytes(canonical(definitions))
        fixture.run_saved()
        return save_dashboard(fixture.root / "run", output, synthetic=True)
    finally:
        fixture.doCleanups()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True,
                        help="New directory for index.html and its integrity manifest")
    print(json.dumps(build_demo(parser.parse_args().output)))
