"""Fictional equity, fixed-time replay and reconciled nonlinear attribution."""

import contextlib
from copy import deepcopy
from dataclasses import replace
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest

from market_risk.cli import main
from market_risk.config import load_foundation
from market_risk.data import DataError, canonical, digest
from market_risk.models import DATASET, MACROS, TARGETS, label
from market_risk.preparation import quarter_end
from market_risk.pricing import build_holdings, load_portfolio, mark_portfolio, shocked, sensitivity
from market_risk.scenarios import load_scenarios
from market_risk.valuation import observed_baseline, price_scenarios, scenario_changes, value_state
from test_models import ROOT, synthetic_rows


class ValuationTests(unittest.TestCase):
    def setUp(self):
        self.spec = load_portfolio(ROOT / "configs/portfolio.json")
        self.foundation = load_foundation(ROOT / "configs/foundation.toml")
        self.definitions = load_scenarios(ROOT / "configs/scenarios.json")
        self.rows = synthetic_rows()
        for r in self.rows:
            r["equity_price_usd"] = 100.0
            r["unemployment_pct"] = 4.0
            r[TARGETS[3]] += 2  # keep synthetic replay applications in a valid spread domain
        self.baseline, _ = observed_baseline(self.rows, self.spec)
        self.positions = build_holdings(self.spec, self.baseline)
        self.risks = {p.identifier: sensitivity(p, self.baseline, self.spec.valuation_date, 1) for p in self.positions}

    def value(self, **changes):
        shocks = dict(equity_price_change=0, yield_2y_change=0, yield_10y_change=0, credit_spread_proxy_change=0)
        shocks.update(changes)
        return value_state(self.positions, self.baseline, shocked(self.baseline, shocks), self.spec, self.foundation, self.risks)

    def test_sizing_equity_hand_calculation_cash_and_zero_identity(self):
        base = mark_portfolio(self.positions, self.baseline, self.spec.valuation_date)
        self.assertAlmostEqual(base["total"], 1e6)
        zero = self.value()
        self.assertEqual(zero["pnl_usd"], 0)
        loss = self.value(equity_price_change=-20)
        self.assertAlmostEqual(loss["pnl_usd"], -80000)
        self.assertEqual(loss["position_pnl_usd"]["cash"], 0)
        self.assertEqual(loss["position_pnl_usd"]["short_government"], 0)
        self.assertTrue(all(loss["checks"].values()))
        shifted = shocked(self.baseline, dict(equity_price_change=-20, yield_2y_change=100, yield_10y_change=-50, credit_spread_proxy_change=25))
        self.assertAlmostEqual(shifted.equity_price, 80)
        self.assertAlmostEqual(shifted.yield_2y - self.baseline.yield_2y, .01)
        self.assertAlmostEqual(shifted.yield_10y - self.baseline.yield_10y, -.005)
        self.assertAlmostEqual(shifted.credit_spread - self.baseline.credit_spread, .0025)

    def test_nonlinear_ordered_attribution_and_interactions_reconcile(self):
        value = self.value(equity_price_change=-25, yield_2y_change=100, yield_10y_change=-100, credit_spread_proxy_change=200)
        self.assertTrue(all(value["checks"].values()))
        self.assertAlmostEqual(sum(value["factor_pnl_usd"].values()), value["pnl_usd"])
        self.assertAlmostEqual(sum(value["reverse_factor_pnl_usd"].values()), value["pnl_usd"])
        self.assertNotAlmostEqual(value["factor_pnl_usd"]["credit_spread"], value["reverse_factor_pnl_usd"]["credit_spread"])
        self.assertGreater(abs(value["isolated_interaction_residual_usd"]), 1)

    def test_small_shock_quadratic_accuracy_and_nonparallel_response(self):
        value = self.value(yield_2y_change=1, yield_10y_change=1)
        self.assertLess(abs(value["second_order_error_usd"]), .01)
        self.assertLess(abs(value["second_order_error_usd"]), abs(value["first_order_error_usd"]))
        parallel = self.value(yield_2y_change=100, yield_10y_change=100)
        twist = self.value(yield_2y_change=100, yield_10y_change=-100)
        self.assertNotAlmostEqual(parallel["pnl_usd"], twist["pnl_usd"])
        self.assertIsNone(self.value()["error_relative_to_full_pnl"])

    def test_replay_compounding_and_fixed_time_state_to_state(self):
        report = price_scenarios(self.rows, self.spec, self.definitions, self.foundation)
        self.assertTrue(report["zero_shock_identity_passed"])
        priced = [r for r in report["results"] if r["status"] == "priced"]
        self.assertEqual(len(priced), 4)
        self.assertEqual(sum(len(r["path"]) for r in priced), 13)
        self.assertEqual(sum(r["status"] == "deferred" for r in report["results"]), 2)
        for result in priced:
            self.assertAlmostEqual(sum(r["state_to_state_pnl_usd"] for r in result["path"]), result["path"][-1]["pnl_usd"])
            self.assertTrue(all(r["position_pnl_usd"]["cash"] == 0 for r in result["path"]))
            self.assertTrue(all(r["baseline_value_usd"] == report["baseline_value_usd"] for r in result["path"]))
        self.assertEqual(len(report["zero_proxy_overlay_sensitivity"]), 2)
        self.assertTrue(any(abs(r["baseline_difference_usd"]) > 1 for r in report["zero_proxy_overlay_sensitivity"]))

    def test_missing_incoming_return_allowed_but_forward_gap_rejected(self):
        rows = deepcopy(self.rows)
        origin_index = next(i for i, r in enumerate(rows) if label(r["date"]) == "2007Q4")
        rows[origin_index][TARGETS[0]] = None
        changes = scenario_changes(self.definitions[0], rows, self.baseline, self.spec)
        self.assertEqual(len(changes), 6)
        rows[origin_index + 1][TARGETS[0]] = None
        with self.assertRaises(DataError):
            scenario_changes(self.definitions[0], rows, self.baseline, self.spec)
        with self.assertRaises(DataError):
            self.value(equity_price_change=-100)
        with self.assertRaises(DataError):
            self.value(credit_spread_proxy_change=-100000)

    def test_direct_python_engine_rejects_ambiguous_scenario_conventions(self):
        definitions = deepcopy(self.definitions)
        definitions[-1]["reference"] = "previous_quarter"
        with self.assertRaises(DataError):
            price_scenarios(self.rows, self.spec, definitions, self.foundation)

    def test_real_cli_roundtrip_redaction_and_integrity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = [{"quarter": label(r["date"]), "quarter_end": quarter_end(r["date"]).isoformat(),
                        "dataset_id": DATASET, "market_alignment": "mean_of_three_monthly_means", "original_index_ig_oas_ready": False,
                        "equity_price_usd": r["equity_price_usd"], "unemployment_pct": r["unemployment_pct"],
                        **{k: r[k] for k in (*TARGETS, *MACROS)}} for r in self.rows]
            buffer = io.StringIO(newline="")
            writer = csv.DictWriter(buffer, fieldnames=list(records[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(records)
            content = buffer.getvalue().encode()
            (root / "quarterly.csv").write_bytes(content)
            (root / "manifest.json").write_bytes(canonical({"dataset_id": DATASET, "use_scope": "local_internal_only",
                "temporal_use": "retrospective_proxy_conditioning", "original_index_ig_oas_ready": False,
                "csv_sha256": digest(content), "calendar_quarters": len(records)}))
            hashes = []
            args = ["price-scenarios", "--portfolio", str(ROOT / "configs/portfolio.json"), "--foundation", str(ROOT / "configs/foundation.toml"),
                    "--data", str(root), "--scenarios", str(ROOT / "configs/scenarios.json"), "--output"]
            for name in ("first", "repeat"):
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    self.assertEqual(main(args + [str(root / name)]), 0)
                summary = json.loads(stdout.getvalue())
                self.assertNotIn("pnl_usd", summary)
                self.assertEqual(summary["priced_states"], 13)
                hashes.append(summary["valuation_sha256"])
            self.assertEqual(*hashes)
            self.assertEqual((root / "first/valuation.md").read_bytes(), (root / "repeat/valuation.md").read_bytes())
            (root / "quarterly.csv").write_bytes(content + b" ")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(args + [str(root / "invalid")]), 2)
            self.assertFalse((root / "invalid").exists())
