"""Generated report/CSV contracts checked against synthetic saved-run calculations."""

import contextlib
import csv
import io
import json
import unittest
from unittest.mock import patch

from market_risk.cli import main
from market_risk.data import DataError
from market_risk.execution import read_json
from market_risk.reporting import save_report
import test_execution as fixtures


class ReportingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ExecutionTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.ExecutionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.run_saved()
        self.root = self.fixture.root
        self.run = self.root / "run"
        self.output = self.root / "report"

    def test_csv_endpoints_and_all_states_match_saved_engine_values(self):
        summary = save_report(self.run, self.output)
        self.assertEqual(summary["pages"], 14)
        self.assertEqual(summary["csv_exhibits"], 7)
        valuation = read_json(self.run / "valuation.json")
        with (self.output / "endpoints.csv").open(newline="") as stream:
            endpoints = list(csv.DictReader(stream))
        self.assertEqual(len(endpoints), 6)
        for row, result in zip(endpoints, valuation["results"]):
            self.assertEqual(row["run_id"], valuation["run_id"])
            self.assertEqual(float(row["pnl_usd"]), result["path"][-1]["pnl_usd"])
            self.assertEqual(json.loads(row["position_pnl_usd"]), result["path"][-1]["position_pnl_usd"])
        with (self.output / "paths.csv").open(newline="") as stream:
            paths = list(csv.DictReader(stream))
        self.assertEqual(len(paths), 29)
        for row, (result, state) in zip(paths, [(r, s) for r in valuation["results"] for s in r["path"]]):
            self.assertEqual(row["scenario"], result["id"])
            self.assertEqual(float(row["state_to_state_pnl_usd"]), state["state_to_state_pnl_usd"])
        document = read_json(self.output / "document.json")
        self.assertEqual(document["charts"]["endpoint_pnl"]["series"][0]["values"], [r["path"][-1]["pnl_usd"] for r in valuation["results"]])

    def test_report_manifest_provenance_deterministic_source_and_no_reestimation(self):
        with patch("market_risk.execution.fit_at_origin", side_effect=AssertionError("No new fit")), patch(
                "socket.create_connection", side_effect=AssertionError("No network")):
            save_report(self.run, self.output)
            save_report(self.run, self.root / "repeat")
        from market_risk.data import digest
        manifest = read_json(self.output / "report_manifest.json")
        self.assertEqual(manifest["saved_run_manifest_sha256"], digest((self.run / "manifest.json").read_bytes()))
        for name, sha in manifest["files"].items():
            self.assertEqual(digest((self.output / name).read_bytes()), sha)
            self.assertEqual((self.output / name).read_bytes(), (self.root / "repeat" / name).read_bytes())
        self.assertEqual(manifest["checks"]["pdf_visual_review"], "pending")
        self.assertFalse(manifest["checks"]["report_is_revaluation"])

    def test_tampered_saved_data_and_no_overwrite_rejected(self):
        save_report(self.run, self.output)
        with self.assertRaises(FileExistsError):
            save_report(self.run, self.output)
        (self.run / "valuation.json").write_bytes(b"{}")
        with self.assertRaises(DataError):
            save_report(self.run, self.root / "bad")
        self.assertFalse((self.root / "bad").exists())

    def test_archived_run_can_be_reported_but_not_claimed_as_current_reproduction(self):
        from market_risk.data import canonical
        from market_risk.execution import reproduce_run
        manifest = read_json(self.run / "manifest.json")
        manifest["source"]["sha256"] = "archived_source"
        (self.run / "manifest.json").write_bytes(canonical(manifest))
        save_report(self.run, self.output)
        self.assertEqual(read_json(self.output / "report_manifest.json")["saved_run_source_sha256"], "archived_source")
        with self.assertRaises(DataError):
            reproduce_run(self.run, self.root / "must-not-rerun")

    def test_cli_summary_is_safe_and_expected_horizon_tables_preserved(self):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(main(["generate-report", "--run", str(self.run), "--output", str(self.output)]), 0)
        result = json.loads(stdout.getvalue())
        self.assertNotIn("pnl_usd", result)
        self.assertNotIn("mae", result)
        with (self.output / "selected_holdout.csv").open(newline="") as stream:
            errors = list(csv.DictReader(stream))
        self.assertEqual(len(errors), 12)
        self.assertEqual({int(r["horizon"]) for r in errors}, {1, 4, 8})
        self.assertEqual({r["run_id"] for r in errors}, {result["run_id"]})

    def test_unavailable_only_run_is_reported_without_invented_endpoint_chart(self):
        from market_risk.data import canonical
        definitions = read_json(self.fixture.inputs / "scenarios.json")
        recession = next(r for r in definitions["scenarios"] if r["id"] == "illustrative_recession")
        recession["quarters"][0]["unemployment_change_pp"] = 100
        (self.fixture.inputs / "scenarios.json").write_bytes(canonical(definitions))
        self.fixture.run_saved("unavailable", ["illustrative_recession"])
        summary = save_report(self.root / "unavailable", self.output)
        document = read_json(self.output / "document.json")
        self.assert_report_membership(self.root / "unavailable", self.output, {"illustrative_recession"})
        self.assertEqual(document["pages"][4]["tables"][0]["rows"][0][-1], "unavailable")
        self.assertNotIn("endpoint_pnl", document["charts"])
        self.assertIsNone(document["pages"][7]["chart"])
        self.assertEqual(summary["charts"], 1)
        with (self.output / "endpoints.csv").open(newline="") as stream:
            self.assertEqual(list(csv.DictReader(stream)), [])

    def test_edited_configuration_drives_report_descriptions_and_chart_identity(self):
        import tomllib
        from market_risk.config import load_foundation
        from market_risk.data import canonical
        from market_risk.evaluation import load_evaluation_config, save_evaluation
        from market_risk.execution import save_run
        from market_risk.models import load_model_config

        def edit_toml(name, edits):
            path = self.fixture.inputs / name
            raw = tomllib.loads(path.read_text())
            for key, value in edits.items():
                raw[key] = value
            lines = [f"{k} = {json.dumps(v)}" for k, v in raw.items() if not isinstance(v, dict)]
            for key, record in raw.items():
                if isinstance(record, dict):
                    lines += [f"[{key}]", *[f"{k} = {json.dumps(v)}" for k, v in record.items()]]
            path.write_text("\n".join(lines) + "\n")

        edit_toml("models.toml", {"equity_predictors": ["inflation_yoy_pct"]})
        edit_toml("foundation.toml", {"evaluation": {"minimum_training_quarters": 44, "minimum_validation_quarters": 13,
                    "final_holdout_quarters": 21, "horizons_quarters": [1, 2, 4]}, "tolerances": {"absolute_usd": .02, "relative": 2e-10}})
        settings = self.fixture.inputs / "edited-evaluation.toml"
        settings.write_text('schema_version = 1\nselection_horizon = 1\nselection_metric = "mae"\nrolling_calendar_quarters = 55\n')
        prepared_path = self.fixture.inputs / "data/manifest.json"
        prepared = read_json(prepared_path)
        prepared["config"] = {"minimum_common_days_per_month": 17, "maximum_excluded_union_day_fraction": .05}
        prepared_path.write_bytes(canonical(prepared))
        portfolio_path = self.fixture.inputs / "portfolio.json"
        portfolio = read_json(portfolio_path)
        portfolio["positions"][0]["weight"] = .3
        portfolio["positions"][-1]["weight"] = .15
        corporate = next(p for p in portfolio["positions"] if p["kind"] == "corporate_bond")
        # A later corporate maturity must not acquire a government chart label.
        corporate["coupon_dates"] = [f"{year}-{month}-30" for year in range(2026, 2042) for month in ("06", "12") if (year, month) > (2026, "06")]
        portfolio["sensitivity"]["parallel_shifts_bp"] = [-.75, 0, .75, 12.5]
        portfolio_path.write_bytes(canonical(portfolio))
        scenario_path = self.fixture.inputs / "scenarios.json"
        scenarios = read_json(scenario_path)
        conditional = [r for r in scenarios["scenarios"] if r["mode"] == "conditional_projection"]
        for record, horizon in zip(conditional, (3, 2)):
            record["horizon_quarters"] = horizon
            record["quarters"] = record["quarters"][:horizon]
        scenario_path.write_bytes(canonical(scenarios))
        foundation = load_foundation(self.fixture.inputs / "foundation.toml")
        evaluation_folder = self.fixture.inputs / "edited-evaluation"
        save_evaluation(self.fixture.inputs / "data", load_model_config(self.fixture.inputs / "models.toml"), foundation.evaluation,
                        load_evaluation_config(settings, foundation.evaluation), evaluation_folder)
        altered = self.root / "altered"
        save_run(self.fixture.inputs / "data", evaluation_folder, self.fixture.inputs / "models.toml", self.fixture.inputs / "foundation.toml",
                 portfolio_path, scenario_path, altered)
        save_report(altered, self.output)
        document = read_json(self.output / "document.json")
        self.assertIn("30% broad equity", document["pages"][5]["paragraphs"][0])
        self.assertIn("15% cash", document["pages"][5]["paragraphs"][0])
        self.assertIn("17 common dates", document["pages"][2]["paragraphs"][0])
        self.assertIn("5%", document["pages"][2]["paragraphs"][0])
        self.assertIn("equity: year-on-year inflation", document["pages"][3]["paragraphs"][1])
        for text in ("44 eligible", "13 usable", "21 calendar", "55-calendar"):
            self.assertIn(text, document["pages"][6]["paragraphs"][0])
        self.assertIn("max(USD 0.02, 2e-10", document["pages"][8]["paragraphs"][0])
        self.assertIn("other floats use absolute 1e-12 or relative 2e-10", document["pages"][11]["paragraphs"][2])
        self.assertEqual(document["assumptions"]["tolerances"], {"absolute_usd": .02, "relative": 2e-10})
        self.assertIn("other floats use absolute 1e-12 or relative 2e-10", (self.output / "report.md").read_text())
        self.assertIn("(3, 2 quarters)", document["pages"][8]["paragraphs"][2])
        self.assertEqual(document["charts"]["bond_approximations"]["labels"], ["-0.75", "0", "0.75", "12.5"])
        valuation = read_json(altered / "valuation.json")
        expected = [r["full_pnl_usd"] for r in sorted(valuation["approximation_diagnostics"], key=lambda r: r["short_bp"])
                    if r["position"] == "long_government" and r["shift_kind"] == "parallel"]
        self.assertEqual(document["charts"]["bond_approximations"]["series"][0]["values"], expected)
        self.assertEqual(document["assumptions"]["evaluation_rules"]["horizons_quarters"], [1, 2, 4])

    def assert_report_membership(self, saved, output, expected):
        valuation = read_json(saved / "valuation.json")
        document = read_json(output / "document.json")
        run_id = valuation["run_id"]
        self.assertEqual(document["run_id"], run_id)
        self.assertEqual(read_json(output / "report_manifest.json")["run_id"], run_id)
        self.assertEqual(set(document["scenario_labels"]), expected)
        definitions = document["assumptions"]["scenarios"]
        self.assertEqual({r["id"] for r in definitions}, expected)
        table_rows = document["pages"][4]["tables"][0]["rows"]
        self.assertEqual({r[0].split(": ", 1)[1] for r in table_rows}, expected)
        markdown = (output / "report.md").read_text()
        self.assertIn(run_id, markdown)
        for row in table_rows:
            self.assertIn("| " + " | ".join(row) + " |", markdown)
        for path in output.glob("*.csv"):
            with path.open(newline="") as stream:
                records = list(csv.DictReader(stream))
            self.assertTrue(all(r["run_id"] == run_id for r in records))
            for record in records:
                for key in ("scenario", "scenario_id"):
                    if key in record:
                        self.assertIn(record[key], expected)
        with (output / "macro_conditions.csv").open(newline="") as stream:
            macro = list(csv.DictReader(stream))
        expected_macro = [{"run_id": run_id, "scenario": r["id"], **{k: str(v) for k, v in q.items()}}
                          for r in definitions if r["mode"] == "conditional_projection" for q in r["quarters"]]
        self.assertEqual(macro, expected_macro)

    def test_selected_scenarios_bound_all_report_consumers(self):
        definitions = read_json(self.fixture.inputs / "scenarios.json")["scenarios"]
        direct = next(r["id"] for r in definitions if r["mode"] == "direct_shock")
        for scenario in ("illustrative_recession", direct):
            with self.subTest(scenario=scenario):
                self.fixture.run_saved(scenario, [scenario])
                saved = self.root / scenario
                output = self.root / (scenario + "-report")
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(["generate-report", "--run", str(saved), "--output", str(output)]), 0)
                self.assert_report_membership(saved, output, {scenario})
