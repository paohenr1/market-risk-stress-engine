"""Exercise the public CLI in a separate process, including invalid input paths."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FoundationCLITests(unittest.TestCase):
    def run_config(self, path: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "market_risk", "check-config", "--config", str(path)],
            capture_output=True, text=True, check=False,
        )

    def test_valid_configuration_returns_typed_values(self):
        result = self.run_config(ROOT / "configs" / "foundation.toml")
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["status"], "valid")
        self.assertEqual(output["configuration"]["evaluation"]["horizons_quarters"], [1, 4, 8])
        self.assertEqual(output["configuration"]["currency"], "USD")

    def test_missing_file_is_reported_without_a_traceback(self):
        result = self.run_config(ROOT / "does-not-exist.toml")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)["status"], "invalid")
        self.assertEqual(result.stdout, "")

    def test_evaluation_count_bounds(self):
        original = (ROOT / "configs/foundation.toml").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.toml"
            for field, minimum in (("minimum_training_quarters", 40),
                                   ("minimum_validation_quarters", 12),
                                   ("final_holdout_quarters", 20)):
                for value in ("true", "false", "0", "-1", str(minimum - 1),
                              str(float(minimum)), '"40"', str(minimum), str(minimum + 1)):
                    with self.subTest(field=field, value=value):
                        path.write_text(original.replace(f"{field} = {minimum}", f"{field} = {value}"), encoding="utf-8")
                        result = self.run_config(path)
                        if value in (str(minimum), str(minimum + 1)):
                            self.assertEqual(result.returncode, 0, result.stderr)
                            self.assertEqual(json.loads(result.stdout)["configuration"]["evaluation"][field], int(value))
                        else:
                            self.assertEqual(result.returncode, 2, result.stdout)
                            self.assertIn(f"{field} must be an integer >= {minimum}", result.stderr)
                            self.assertEqual(result.stdout, "")

    def test_invalid_inputs_are_rejected(self):
        # These are supplied input contracts, not inspection of implementation source.
        original = (ROOT / "configs" / "foundation.toml").read_text(encoding="utf-8")
        invalid = {
            "unknown_field": original + '\n[tolerances.typo]\nvalue = 1\n',
            "boolean_count": original.replace("minimum_training_quarters = 40", "minimum_training_quarters = true"),
            "invalid_units": original.replace('currency = "USD"', 'currency = "CAD"'),
            "nan_tolerance": original.replace("absolute_usd = 0.01", "absolute_usd = nan"),
            "duplicate_horizons": original.replace("[1, 4, 8]", "[1, 4, 4]"),
            "credit_approval_disabled": original.replace("firecrawl_requires_credit_approval = true", "firecrawl_requires_credit_approval = false"),
            "malformed_toml": "currency = [",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.toml"
            for label, content in invalid.items():
                with self.subTest(label=label):
                    path.write_text(content, encoding="utf-8")
                    result = self.run_config(path)
                    self.assertEqual(result.returncode, 2, result.stdout)
                    self.assertEqual(json.loads(result.stderr)["status"], "invalid")
                    self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
