"""Integrated execution and saved-run contracts using unrestricted synthetic data."""

import contextlib
from copy import deepcopy
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from market_risk.cli import main
from market_risk.config import load_foundation
from market_risk.data import DataError, canonical, digest
from market_risk.evaluation import evaluate, evaluation_markdown, load_evaluation_config
from market_risk.execution import (anchored_changes, assert_equivalent, integrated_calculation,
                                   load_selection, read_json, reproduce_run, save_run)
from market_risk.models import DATASET, MACROS, TARGETS, label, load_model_config, load_prepared, parse_quarter
from market_risk.preparation import quarter_end
from market_risk.pricing import MarketState, shocked
from test_models import ROOT, synthetic_rows


class ExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = synthetic_rows()
        for row in cls.rows:
            row.update(equity_price_usd=100.0, unemployment_pct=4.0)
            row[TARGETS[3]] += 2.0
        cls.config = load_model_config(ROOT / "configs/models.toml")
        cls.foundation = load_foundation(ROOT / "configs/foundation.toml")
        cls.evaluation = evaluate(cls.rows, cls.config, cls.foundation.evaluation,
                                  load_evaluation_config(ROOT / "configs/evaluation.toml", cls.foundation.evaluation))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.inputs = self.root / "inputs"
        (self.inputs / "data").mkdir(parents=True)
        (self.inputs / "evaluation").mkdir()
        records = [{"quarter": label(r["date"]), "quarter_end": quarter_end(r["date"]).isoformat(),
                    "dataset_id": DATASET, "market_alignment": "mean_of_three_monthly_means", "original_index_ig_oas_ready": False,
                    "equity_price_usd": r["equity_price_usd"], "unemployment_pct": r["unemployment_pct"],
                    **{k: r[k] for k in (*TARGETS, *MACROS)}} for r in self.rows]
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=list(records[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(records)
        content = buffer.getvalue().encode()
        (self.inputs / "data/quarterly.csv").write_bytes(content)
        prepared = {"dataset_id": DATASET, "use_scope": "local_internal_only", "temporal_use": "retrospective_proxy_conditioning",
                    "original_index_ig_oas_ready": False, "csv_sha256": digest(content), "calendar_quarters": len(records)}
        (self.inputs / "data/manifest.json").write_bytes(canonical(prepared))
        self.saved_evaluation = deepcopy(self.evaluation)
        self.saved_evaluation["prepared_csv_sha256"] = digest(content)
        self.write_evaluation()
        for name in ("models.toml", "foundation.toml", "portfolio.json", "scenarios.json"):
            (self.inputs / name).write_bytes((ROOT / "configs" / name).read_bytes())

    def write_evaluation(self):
        body = canonical(self.saved_evaluation)
        markdown = evaluation_markdown(self.saved_evaluation).encode()
        (self.inputs / "evaluation/evaluation.json").write_bytes(body)
        (self.inputs / "evaluation/evaluation.md").write_bytes(markdown)
        (self.inputs / "evaluation/manifest.json").write_bytes(canonical({"schema_version": 1,
            "evaluation_sha256": digest(body), "markdown_sha256": digest(markdown)}))

    def run_saved(self, name="run", ids=None):
        return save_run(self.inputs / "data", self.inputs / "evaluation", self.inputs / "models.toml",
                        self.inputs / "foundation.toml", self.inputs / "portfolio.json", self.inputs / "scenarios.json",
                        self.root / name, ids)

    def test_level_anchoring_and_incremental_equity_against_hand_calculation(self):
        baseline = MarketState(100, .04, .05, .02)
        origin = dict(zip(TARGETS, [7, 2, 3, 1]))
        rows = [{"date": parse_quarter(q), **dict(zip(TARGETS, v))} for q, v in
                (("2026Q3", [10, 3, 2, 1.5]), ("2026Q4", [-10, 4, 2.5, 1.25]))]
        changes, raw = anchored_changes(baseline, origin, rows)
        first, second = (shocked(baseline, r["changes"]) for r in changes)
        self.assertAlmostEqual(first.equity_price, 110)
        self.assertAlmostEqual(second.equity_price, 99)
        self.assertAlmostEqual(first.yield_2y, .05)
        self.assertAlmostEqual(first.yield_10y, .04)
        self.assertAlmostEqual(first.credit_spread, .025)
        self.assertAlmostEqual(second.yield_2y, .06)
        self.assertAlmostEqual(raw[0].yield_2y, .03)
        rows[0][TARGETS[0]] = -100
        with self.assertRaises(DataError):
            anchored_changes(baseline, origin, rows)

    def test_all_modes_selected_refits_reconciliations_and_unchanged_baseline(self):
        report, models = integrated_calculation(self.inputs)
        self.assertEqual(len(report["results"]), 6)
        self.assertTrue(report["zero_shock_identity_passed"])
        conditional = [r for r in report["results"] if r.get("mode") == "conditional_projection"]
        self.assertEqual(len(conditional), 2)
        for result in conditional:
            self.assertEqual(result["status"], "priced")
            self.assertEqual(result["origin_state"]["pnl_usd"], 0)
            self.assertEqual(result["origin_state"]["market_state"], report["baseline_market"])
            self.assertEqual(len(result["path"]), 8)
            for target in TARGETS:
                selected = result["selected_models"][target]
                self.assertEqual(selected["window"], report["selection"][target]["window"])
                self.assertEqual(selected["model"]["method"], report["selection"][target]["method"])
                self.assertEqual(selected["training_end"], "2026Q2")
            for state, raw in zip(result["path"], result["unanchored_comparison"]):
                self.assertTrue(all(state["checks"].values()))
                self.assertEqual(state["position_pnl_usd"]["cash"], 0)
                # Here model origin equals baseline, so level anchoring is the identity.
                self.assertAlmostEqual(state["pnl_usd"], raw["pnl_usd"])
            self.assertAlmostEqual(sum(s["state_to_state_pnl_usd"] for s in result["path"]), result["path"][-1]["pnl_usd"])
        self.assertTrue(models)

    def test_holdout_scores_do_not_change_selected_specs_or_paths(self):
        before = integrated_calculation(self.inputs)
        for summary in self.saved_evaluation["holdout"]["summaries"]:
            summary.update(mae=999999, rmse=999999, bias=-999999)
        self.saved_evaluation["selected_holdout_summaries"] = []
        self.write_evaluation()
        self.assertEqual(before, integrated_calculation(self.inputs))

    def test_selection_data_configuration_and_hash_mismatches_rejected(self):
        rows, prepared = load_prepared(self.inputs / "data")
        for mutation in (lambda r: r.update(selection_sha256="wrong"),
                         lambda r: r.update(prepared_csv_sha256="wrong"),
                         lambda r: r["model_config"].update(schema_version=999),
                         lambda r: r["selection"][TARGETS[0]].update(method="no_change")):
            self.saved_evaluation = deepcopy(self.evaluation)
            self.saved_evaluation["prepared_csv_sha256"] = prepared["csv_sha256"]
            mutation(self.saved_evaluation)
            self.write_evaluation()
            with self.assertRaises(DataError):
                load_selection(self.inputs / "evaluation", prepared, rows, self.config, self.foundation.evaluation)
        (self.inputs / "evaluation/evaluation.json").write_bytes(b"{}")
        with self.assertRaises(DataError):
            integrated_calculation(self.inputs)

    def test_saved_run_offline_reproduction_and_no_overwrite(self):
        result = self.run_saved()
        run = self.root / "run"
        first = read_json(run / "valuation.json")
        # Original source inputs may disappear; reproduction uses only the saved copies.
        (self.inputs / "data/quarterly.csv").unlink()
        with patch("socket.create_connection", side_effect=AssertionError("Network forbidden")):
            repeated = reproduce_run(run, self.root / "repeat")
        self.assertEqual(repeated["status"], "reproduced")
        self.assertNotEqual(result["run_id"], repeated["run_id"])
        second = read_json(self.root / "repeat/valuation.json")
        first.pop("run_id")
        second.pop("run_id")
        self.assertEqual(first, second)
        manifest = read_json(self.root / "repeat/manifest.json")
        self.assertTrue(manifest["checks"]["offline_numerical_reproduction"])
        self.assertEqual(manifest["environment"]["runtime_dependencies"], [])
        with self.assertRaises(FileExistsError):
            reproduce_run(run, self.root / "repeat")

    def test_tampered_artifact_unknown_file_and_source_mismatch_fail_closed(self):
        self.run_saved()
        run = self.root / "run"
        manifest_path = run / "manifest.json"
        original = manifest_path.read_bytes()
        manifest = json.loads(original)
        manifest["source"]["sha256"] = "different"
        manifest_path.write_bytes(canonical(manifest))
        with self.assertRaises(DataError):
            reproduce_run(run, self.root / "bad-source")
        manifest = json.loads(original)
        manifest["environment"]["python_version"] = "0.0.0"
        manifest_path.write_bytes(canonical(manifest))
        with self.assertRaises(DataError):
            reproduce_run(run, self.root / "bad-runtime")
        manifest_path.write_bytes(original)
        stray = run / "unexpected.txt"
        stray.write_text("stray")
        with self.assertRaises(DataError):
            reproduce_run(run, self.root / "bad-file")
        stray.unlink()
        (run / "valuation.json").write_bytes(b"{}")
        with self.assertRaises(DataError):
            reproduce_run(run, self.root / "bad-hash")
        self.assertFalse((self.root / "bad-hash").exists())

    def test_macro_domain_and_origin_fail_as_explicit_unavailable_cases(self):
        definitions = read_json(self.inputs / "scenarios.json")
        conditional = [r for r in definitions["scenarios"] if r["mode"] == "conditional_projection"]
        conditional[0]["quarters"][0]["unemployment_change_pp"] = 100
        conditional[1]["origin"] = "2026Q1"
        from market_risk.preparation import shift_quarter
        for i, row in enumerate(conditional[1]["quarters"], 1):
            row["quarter"] = label(shift_quarter(parse_quarter("2026Q1"), i))
        (self.inputs / "scenarios.json").write_bytes(canonical(definitions))
        result, _ = integrated_calculation(self.inputs)
        self.assertEqual(sum(r["status"] == "unavailable" for r in result["results"]), 2)
        self.assertEqual(sum(r["status"] == "priced" for r in result["results"]), 4)

    def test_invalid_selected_forecast_is_unavailable_without_fallback_or_clipping(self):
        from market_risk.execution import project

        def invalid(bundle, path):
            result = project(bundle, path)
            if result["paths"][0]["target"] == TARGETS[0]:
                result["paths"][0]["values"][0]["value"] = -100.0
            return result

        with patch("market_risk.execution.project", side_effect=invalid):
            report, _ = integrated_calculation(self.inputs, ["illustrative_recession"])
        self.assertEqual(report["results"][0]["status"], "unavailable")
        self.assertNotIn("path", report["results"][0])

    def test_individual_batch_cli_redaction_and_unknown_ids(self):
        args = ["run-stress", "--data", str(self.inputs / "data"), "--evaluation", str(self.inputs / "evaluation"),
                "--config", str(self.inputs / "models.toml"), "--foundation", str(self.inputs / "foundation.toml"),
                "--portfolio", str(self.inputs / "portfolio.json"), "--scenarios", str(self.inputs / "scenarios.json")]
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = main(args + ["--scenario", "illustrative_recession", "--output", str(self.root / "single")])
        self.assertEqual(code, 0)
        summary = json.loads(stdout.getvalue())
        self.assertEqual(summary["priced_scenarios"], 1)
        self.assertEqual(summary["priced_states"], 8)
        self.assertNotIn("pnl_usd", summary)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(args + ["--scenario", "unknown", "--output", str(self.root / "bad")]), 2)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["reproduce-run", "--run", str(self.root / "single"), "--output", str(self.root / "single-repeat")]), 0)

    def test_numeric_comparator_checks_nonfinancial_fields_and_usd_tolerance(self):
        tolerance = {"absolute_usd": .01, "relative": 1e-10}
        assert_equivalent({"pnl_usd": {"equity": 1.0}}, {"pnl_usd": {"equity": 1.005}}, tolerance)
        for a, b in (({"pnl_usd": 1.0}, {"pnl_usd": 1.02}), ({"rate": .02}, {"rate": .02001}),
                     ({"n": 2}, {"n": 3}), ({"status": "priced"}, {"status": "unavailable"})):
            with self.assertRaises(DataError):
                assert_equivalent(a, b, tolerance)
