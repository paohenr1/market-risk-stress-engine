"""Offline dashboard output and integrity contracts using fictional saved runs."""

import base64
import contextlib
from copy import deepcopy
import hashlib
from html.parser import HTMLParser
import io
import json
import unittest
from unittest.mock import patch

from market_risk.cli import main
from market_risk.dashboard import dashboard_payload, render_dashboard, save_dashboard
from market_risk.data import DataError, canonical
from market_risk.execution import load_saved_run, read_json
import test_execution as fixtures


class DashboardHTML(HTMLParser):
    """Read emitted HTML as the output contract, not implementation source."""
    def __init__(self, body):
        super().__init__()
        self.scripts = {}
        self.policy = None
        self.active_script = None
        self.feed(body.decode())

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "meta" and attributes.get("http-equiv") == "Content-Security-Policy":
            self.policy = attributes["content"]
        if tag == "script":
            self.active_script = attributes["id"]
            self.scripts[self.active_script] = ""

    def handle_data(self, data):
        if self.active_script:
            self.scripts[self.active_script] += data

    def handle_endtag(self, tag):
        if tag == "script":
            self.active_script = None


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ExecutionTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.ExecutionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.run_saved()
        self.run = self.fixture.root / "run"
        self.output = self.fixture.root / "dashboard"

    def test_saved_values_scope_and_safe_cli_summary(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(main(["generate-dashboard", "--run", str(self.run), "--output", str(self.output)]), 0)
        summary = json.loads(stdout.getvalue())
        self.assertEqual(summary["scenarios"], 6)
        self.assertEqual(summary["states"], 29)
        self.assertNotIn("pnl_usd", summary)
        self.assertNotIn("mae", summary)
        parsed = DashboardHTML((self.output / "index.html").read_bytes())
        payload = json.loads(parsed.scripts["dashboard-data"])
        valuation = read_json(self.run / "valuation.json")
        self.assertEqual(payload["run"]["id"], summary["run_id"])
        self.assertEqual(payload["baseline_value_usd"], valuation["baseline_value_usd"])
        for record, saved in zip(payload["scenarios"], valuation["results"]):
            self.assertEqual(record["id"], saved["id"])
            for shown, state in zip(record["path"], saved["path"]):
                for field, value in shown.items():
                    self.assertEqual(value, state[field])
        self.assertEqual(payload["data_kind"], "saved_results")
        self.assertEqual(set(payload["scenarios"][0]), {"id", "name", "mode", "status", "reason", "origin", "end", "horizon", "narrative", "macro_inputs", "path"})

    def test_no_refit_network_or_repricing_and_deterministic_bytes(self):
        with patch("market_risk.execution.fit_at_origin", side_effect=AssertionError("No refit")), patch(
                "market_risk.valuation.value_state", side_effect=AssertionError("No repricing")), patch(
                "socket.create_connection", side_effect=AssertionError("No network")):
            save_dashboard(self.run, self.output, synthetic=True)
            save_dashboard(self.run, self.fixture.root / "repeat", synthetic=True)
        self.assertEqual((self.output / "index.html").read_bytes(), (self.fixture.root / "repeat/index.html").read_bytes())
        receipt = read_json(self.output / "dashboard_manifest.json")
        self.assertTrue(receipt["checks"]["saved_run_integrity"])
        self.assertFalse(receipt["checks"]["revaluation"])
        self.assertEqual(receipt["data_kind"], "synthetic")
        self.assertEqual(receipt["files"]["index.html"], hashlib.sha256((self.output / "index.html").read_bytes()).hexdigest())

    def test_integrity_rejection_no_overwrite_and_no_saved_bundle_mutation(self):
        before = {p.relative_to(self.run): p.read_bytes() for p in self.run.rglob("*") if p.is_file()}
        with self.assertRaises(DataError):
            save_dashboard(self.run, self.run / "dashboard")
        self.assertEqual(before, {p.relative_to(self.run): p.read_bytes() for p in self.run.rglob("*") if p.is_file()})
        save_dashboard(self.run, self.output)
        with self.assertRaises(FileExistsError):
            save_dashboard(self.run, self.output)
        (self.run / "valuation.json").write_bytes(b"{}")
        with self.assertRaises(DataError):
            save_dashboard(self.run, self.fixture.root / "tampered")
        self.assertFalse((self.fixture.root / "tampered").exists())

    def test_archived_executable_is_displayed_without_a_reproduction_claim(self):
        manifest = read_json(self.run / "manifest.json")
        manifest["source"]["sha256"] = "archived_source"
        (self.run / "manifest.json").write_bytes(canonical(manifest))
        save_dashboard(self.run, self.output)
        self.assertEqual(read_json(self.output / "dashboard_manifest.json")["saved_run_source_sha256"], "archived_source")

    def test_single_and_unavailable_scenarios_are_not_invented_as_zero(self):
        definitions = read_json(self.fixture.inputs / "scenarios.json")
        recession = next(r for r in definitions["scenarios"] if r["id"] == "illustrative_recession")
        recession["quarters"][0]["unemployment_change_pp"] = 100
        (self.fixture.inputs / "scenarios.json").write_bytes(canonical(definitions))
        self.fixture.run_saved("unavailable", ["illustrative_recession"])
        save_dashboard(self.fixture.root / "unavailable", self.output)
        payload = json.loads(DashboardHTML((self.output / "index.html").read_bytes()).scripts["dashboard-data"])
        self.assertEqual(len(payload["scenarios"]), 1)
        self.assertEqual(payload["findings"]["priced_scenarios"], 0)
        self.assertEqual(payload["findings"]["unavailable_scenarios"], 1)
        self.assertEqual(payload["scenarios"][0]["status"], "unavailable")
        self.assertEqual(payload["scenarios"][0]["path"], [])
        self.assertIsNone(payload["findings"]["conditional_paths_identical"])

    def test_json_markup_escaping_and_script_policy_bind_the_generated_output(self):
        manifest, valuation, _ = load_saved_run(self.run)
        evaluation = read_json(self.run / "inputs/evaluation/evaluation.json")
        definitions = read_json(self.run / "inputs/scenarios.json")["scenarios"]
        definitions[0]["narrative"] = '</script><img src=x onerror="alert(1)"> & test'
        payload = dashboard_payload(manifest, valuation, evaluation, definitions)
        parsed = DashboardHTML(render_dashboard(payload))
        self.assertEqual(set(parsed.scripts), {"dashboard-data", "dashboard-app"})
        self.assertEqual(json.loads(parsed.scripts["dashboard-data"])["scenarios"][0]["narrative"], definitions[0]["narrative"])
        self.assertIn("connect-src 'none'", parsed.policy)
        expected = base64.b64encode(hashlib.sha256(parsed.scripts["dashboard-app"].encode()).digest()).decode()
        self.assertIn("'sha256-" + expected + "'", parsed.policy)

    def test_macro_findings_come_from_saved_selection_and_actual_path_equality(self):
        manifest, valuation, _ = load_saved_run(self.run)
        evaluation = read_json(self.run / "inputs/evaluation/evaluation.json")
        definitions = read_json(self.run / "inputs/scenarios.json")["scenarios"]
        # A fictional presentation case with two equal saved paths and no macro specifications.
        for choice in evaluation["selection"].values():
            choice["method"] = "ar1"
        conditional = [r for r in valuation["results"] if r.get("mode") == "conditional_projection"]
        conditional[1]["path"] = deepcopy(conditional[0]["path"])
        payload = dashboard_payload(manifest, valuation, evaluation, definitions)
        self.assertFalse(payload["findings"]["selected_models_use_macros"])
        self.assertTrue(payload["findings"]["conditional_paths_identical"])
        conditional[1]["path"][-1]["pnl_usd"] += 1
        self.assertFalse(dashboard_payload(manifest, valuation, evaluation, definitions)["findings"]["conditional_paths_identical"])


if __name__ == "__main__":
    unittest.main()
