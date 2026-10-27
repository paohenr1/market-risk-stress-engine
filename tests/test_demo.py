"""Public demo generation must use fictional inputs and the normal calculation path."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from test_dashboard import DashboardHTML


class DemoTests(unittest.TestCase):
    def test_complete_demo_is_synthetic_and_refuses_overwrite(self):
        source = Path(__file__).resolve().parents[1] / "tools/build_demo.py"
        spec = importlib.util.spec_from_file_location("build_demo", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "demo"
            result = module.build_demo(output)
            parsed = DashboardHTML((output / "index.html").read_bytes())
            payload = json.loads(parsed.scripts["dashboard-data"])
            receipt = json.loads((output / "dashboard_manifest.json").read_bytes())
            self.assertEqual(payload["data_kind"], "synthetic")
            self.assertEqual(receipt["data_kind"], "synthetic")
            self.assertEqual(receipt["use_scope"], "synthetic_demonstration")
            self.assertEqual(result["states"], 29)
            self.assertEqual(len(payload["scenarios"]), 6)
            self.assertEqual(payload["findings"]["reconciliation_failures"], 0)
            for row in payload["scenarios"]:
                self.assertEqual(row["status"], "priced")
                if row["mode"] == "historical_replay":
                    self.assertTrue(row["id"].startswith("synthetic_replay_"))
                    self.assertIn("fictional", row["narrative"])
            before = (output / "index.html").read_bytes()
            with self.assertRaises(FileExistsError):
                module.build_demo(output)
            self.assertEqual((output / "index.html").read_bytes(), before)
