"""Chronological evaluation and selection tested on unrestricted synthetic data."""

from copy import deepcopy
from dataclasses import replace
import unittest
from unittest.mock import patch

from market_risk.config import ConfigurationError, load_foundation
from market_risk.evaluation import choose_models, error_summary, evaluate, load_evaluation_config, partition_errors
from market_risk.models import MACROS, TARGETS, fit_at_origin, label, load_model_config, parse_quarter, project
from market_risk.preparation import shift_quarter
from test_models import ROOT, synthetic_rows


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.rows = synthetic_rows()
        self.models = load_model_config(ROOT / "configs/models.toml")
        self.rules = load_foundation(ROOT / "configs/foundation.toml").evaluation
        self.settings = load_evaluation_config(ROOT / "configs/evaluation.toml", self.rules)

    def test_partitions_never_cross_and_comparisons_share_dates(self):
        with patch("market_risk.evaluation.fit_at_origin", wraps=fit_at_origin) as fits, patch(
                "market_risk.evaluation.project", wraps=project) as projections:
            result = evaluate(self.rows, self.models, self.rules, self.settings)
        for stage, start, stop in (("validation", 102, 114), ("holdout", 114, 134)):
            partition = result[stage]
            for h in (1, 4, 8):
                expected_origins = [label(r["date"]) for r in self.rows[start - 1:stop - h]]
                summaries = [s for s in partition["summaries"] if s["horizon"] == h]
                self.assertEqual(len(summaries), 34)
                for summary in summaries:
                    self.assertEqual(summary["observations"], stop - start - h + 1)
                    self.assertEqual(summary["origins"], expected_origins)
            for record in partition["records"]:
                origin = parse_quarter(record["origin"])
                self.assertGreaterEqual(origin, self.rows[start - 1]["date"])
                for step in range(1, record["horizon"] + 1):
                    future = shift_quarter(origin, step)
                    self.assertGreaterEqual(future, self.rows[start]["date"])
                    self.assertLessEqual(future, self.rows[stop - 1]["date"])
                self.assertEqual(parse_quarter(record["endpoint"]), future)
            for row in partition["origin_fit_diagnostics"]:
                self.assertGreaterEqual(row["training_observations"], 40)
                if row["window"] == "rolling":
                    self.assertEqual(row["training_observations"], 59)
        self.assertTrue(fits.call_args_list)
        for call in fits.call_args_list:
            history, _, _, origin, _ = call.args
            self.assertEqual(history[-1]["date"], origin)
            self.assertTrue(all(row["date"] <= origin for row in history))
        self.assertTrue(projections.call_args_list)
        for call in projections.call_args_list:
            bundle, path = call.args
            self.assertEqual(bundle["training_end"], path["origin"])
            for row in path["quarters"]:
                self.assertEqual(set(row), {"quarter", *MACROS})

    def test_future_market_targets_cannot_change_origin_predictions(self):
        for start, stop in ((102, 114), (114, 134)):
            with self.subTest(start=start):
                first = partition_errors(self.rows, self.models, self.rules, start, stop, 60)
                altered = deepcopy(self.rows)
                for row in altered[start:]:
                    for target in TARGETS:
                        row[target] += 10
                second = partition_errors(altered, self.models, self.rules, start, stop, 60)
                origin = label(self.rows[start - 1]["date"])
                before = [r for r in first["records"] if r["origin"] == origin]
                after = [r for r in second["records"] if r["origin"] == origin]
                self.assertEqual(len(before), 102)
                self.assertEqual(len(after), len(before))
                for a, b in zip(before, after):
                    self.assertEqual(a["predicted"], b["predicted"])
                    self.assertNotEqual(a["actual"], b["actual"])
                    self.assertNotEqual(a["error"], b["error"])

    def test_holdout_changes_cannot_change_selection(self):
        first = evaluate(self.rows, self.models, self.rules, self.settings)
        altered = deepcopy(self.rows)
        for row in altered[-20:]:
            for k in TARGETS:
                row[k] *= 2
        second = evaluate(altered, self.models, self.rules, self.settings)
        self.assertEqual(first["selection"], second["selection"])
        self.assertEqual(first["selection_sha256"], second["selection_sha256"])
        self.assertEqual(first["validation"], second["validation"])
        self.assertNotEqual(first["holdout"], second["holdout"])
        self.assertTrue(all(c["status"] == "selected" for c in first["selection"].values()))

    def test_missing_conditions_remove_whole_common_paths_without_imputation(self):
        self.rows[105][MACROS[0]] = None
        result = partition_errors(self.rows, self.models, self.rules, 102, 114, 60)
        one_step = [s for s in result["summaries"] if s["horizon"] == 1]
        self.assertTrue(all(s["observations"] == 11 for s in one_step))
        self.assertTrue(any(e["reason"] == "missing_actual_or_condition_in_path" for e in result["exclusions"]))
        choices = choose_models(result, self.rules)
        self.assertTrue(all(c["status"] == "unavailable" for c in choices.values()))

    def test_units_and_historical_error_summaries(self):
        summary = error_summary([-2., 0., 2.])
        self.assertEqual(summary["mae"], 4 / 3)
        self.assertEqual(summary["bias"], 0)
        self.assertAlmostEqual(summary["error_q10"], -1.6)
        result = partition_errors(self.rows, self.models, self.rules, 102, 104, 60)
        for r in result["records"]:
            multiplier = 1 if r["target"] == TARGETS[0] else 100
            self.assertAlmostEqual(r["error"], (r["predicted"] - r["actual"]) * multiplier)

    def test_direct_fit_only_reads_history_and_enforces_minimum(self):
        end = self.rows[90]["date"]
        first = fit_at_origin(self.rows, self.models, self.rules, end)
        altered = deepcopy(self.rows)
        for row in altered[91:]:
            for key in (*TARGETS, *MACROS):
                row[key] = 1e6
        self.assertEqual(first, fit_at_origin(altered, self.models, self.rules, end))
        with self.assertRaises(ConfigurationError):
            fit_at_origin(self.rows, self.models, replace(self.rules, minimum_training_quarters=10), end)

    def test_bad_horizons_and_selection_settings_fail(self):
        for horizons in ((1, 9), (4, 8), (1, 1), (True, 4)):
            with self.assertRaises(ConfigurationError):
                evaluate(self.rows, self.models, replace(self.rules, horizons_quarters=horizons), self.settings)
        with self.assertRaises(ConfigurationError):
            evaluate(self.rows, self.models, self.rules, {**self.settings, "selection_horizon": 4})
