"""Observable projection behavior using synthetic, unrestricted observations."""

from copy import deepcopy
from dataclasses import replace
from datetime import date
import contextlib
import csv
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from market_risk.cli import main
from market_risk.config import ConfigurationError, load_foundation
from market_risk.data import DataError, canonical, digest
from market_risk.models import (DATASET, MACROS, TARGETS, diagnostics, estimate, fit_models,
                                label, load_model_config, project, regress, save_projection)
from market_risk.preparation import quarter_end, shift_quarter

ROOT = Path(__file__).resolve().parents[1]


def synthetic_rows(count=134):
    import math
    rows = []
    for i in range(count):
        row = {"date": shift_quarter(date(1993, 1, 1), i),
               "gdp_growth_annualized_pct": 2 + math.sin(i * .7),
               "unemployment_change_pp": .2 * math.cos(i * .3),
               "policy_rate_pct": 3 + math.sin(i * .11),
               "inflation_yoy_pct": 2 + math.cos(i * .17)}
        prior = rows[-1][TARGETS[3]] if rows else 1.0
        row.update(equity_price_return_pct=1 + .5 * row[MACROS[0]] + math.cos(i),
                   yield_2y_pct=1 + .7 * row[MACROS[2]] + .1 * row[MACROS[3]],
                   yield_10y_pct=2 + .3 * row[MACROS[2]] + .4 * row[MACROS[3]],
                   credit_spread_proxy_pct=.3 + .6 * prior - .1 * row[MACROS[0]] + .5 * row[MACROS[1]])
        rows.append(row)
    return rows


class ModelsTests(unittest.TestCase):
    def setUp(self):
        self.config = load_model_config(ROOT / "configs/models.toml")
        self.rules = load_foundation(ROOT / "configs/foundation.toml").evaluation
        self.rows = synthetic_rows()

    def path(self, bundle, horizon=8):
        from market_risk.models import parse_quarter
        start = parse_quarter(bundle["training_end"])
        return {"schema_version": 1, "dataset_id": DATASET, "origin": bundle["training_end"],
                "quarters": [{"quarter": label(shift_quarter(start, i)), **dict(zip(MACROS, [2.0, .1, 3.0, 2.0]))}
                             for i in range(1, horizon + 1)]}

    def test_qr_recovers_known_coefficients_and_predictions(self):
        x = [[i, (i % 3) - 1] for i in range(50)]
        y = [4 + 2 * a - 3 * b for a, b in x]
        model = regress(x, y, 1e-10)
        self.assertAlmostEqual(estimate(model, [51, -1]), 109, places=10)
        self.assertTrue(all(v >= 1 - 1e-10 for v in model["vif"]))
        for bad in ([[i, 2 * i] for i in range(50)], [[1, i] for i in range(50)]):
            with self.assertRaises(DataError):
                regress(bad, y, 1e-10)

    def test_reserved_observations_cannot_change_fit_or_scaling(self):
        first = fit_models(self.rows, self.config, self.rules)
        altered = deepcopy(self.rows)
        for row in altered[-32:]:
            for key in (*TARGETS, *MACROS):
                row[key] = 9999999.0
        self.assertEqual(first, fit_models(altered, self.config, self.rules))
        self.assertEqual(first["training_end"], "2018Q2")
        self.assertEqual(first["validation_start"], "2018Q3")
        self.assertEqual(first["final_holdout_start"], "2021Q3")
        with self.assertRaises(DataError):
            fit_models(self.rows, self.config, self.rules, "2018Q3")
        with self.assertRaises(DataError):
            fit_models(self.rows, self.config, self.rules, "1999Q1")

    def test_calendar_missing_target_removes_current_and_next_lag(self):
        full = fit_models(self.rows, self.config, self.rules)
        self.rows[30][TARGETS[0]] = None
        missing = fit_models(self.rows, self.config, self.rules)
        self.assertEqual(full["eligible_training_observations"] - missing["eligible_training_observations"], 2)
        self.assertEqual(missing["excluded_training_quarters"], 3)
        d = diagnostics([1, 2, 3], [date(2000, 1, 1), date(2000, 7, 1), date(2000, 10, 1)])
        self.assertEqual(d["adjacent_residual_pairs"], 1)

    def test_direct_fit_rejects_invalid_evaluation_counts(self):
        for field, minimum in (("minimum_training_quarters", 40),
                               ("minimum_validation_quarters", 12),
                               ("final_holdout_quarters", 20)):
            for value in (True, False, 0, -1, minimum - 1, float(minimum), str(minimum), None):
                with self.subTest(field=field, value=value):
                    rules = replace(self.rules, **{field: value})
                    with self.assertRaisesRegex(ConfigurationError, f"{field} must be an integer >= {minimum}"):
                        fit_models(self.rows, self.config, rules)

    def test_boundary_and_larger_counts_control_samples_and_reservations(self):
        for training, validation, holdout in ((40, 12, 20), (41, 13, 21)):
            with self.subTest(training=training, validation=validation, holdout=holdout):
                rules = replace(self.rules, minimum_training_quarters=training,
                                minimum_validation_quarters=validation, final_holdout_quarters=holdout)
                rows = synthetic_rows(training + 1 + validation + holdout)
                bundle = fit_models(rows, self.config, rules)
                self.assertEqual(bundle["eligible_training_observations"], training)
                self.assertEqual(bundle["training_end"], label(rows[training]["date"]))
                self.assertEqual(bundle["validation_start"], label(rows[training + 1]["date"]))
                self.assertEqual(bundle["final_holdout_start"], label(rows[-holdout]["date"]))
                self.assertEqual(sum(m["status"] == "fitted" for m in bundle["models"]), 17)
                for model in bundle["models"]:
                    if model["features"]:
                        self.assertEqual(model["window_sensitivity"]["recent_minimum"]["observations"], training)
                        self.assertEqual(model["window_sensitivity"]["early_half"]["status"], "insufficient_sample")
                altered = deepcopy(rows)
                for row in altered[-(validation + holdout):]:
                    for key in (*TARGETS, *MACROS):
                        row[key] = 9999999.0
                self.assertEqual(bundle, fit_models(altered, self.config, rules))
                with self.assertRaisesRegex(DataError, "Insufficient eligible training"):
                    fit_models(rows[1:], self.config, rules)
                with self.assertRaisesRegex(DataError, "overlaps reserved"):
                    fit_models(rows, self.config, rules, bundle["validation_start"])

    def test_dynamic_projection_recurses_from_predicted_target(self):
        bundle = fit_models(self.rows, self.config, self.rules)
        path = self.path(bundle)
        result = project(bundle, path)
        self.assertEqual(len(result["paths"]), 17)
        model = next(m for m in bundle["models"] if m["method"] == "dynamic")
        actual = next(p for p in result["paths"] if p["model_id"] == model["id"])["values"]
        previous = bundle["origin_targets"][TARGETS[3]]
        for row, emitted in zip(path["quarters"], actual):
            previous = .3 + .6 * previous - .1 * row[MACROS[0]] + .5 * row[MACROS[1]]
            self.assertAlmostEqual(previous, emitted["value"], places=10)
        # Future realized targets are not accepted as conditioning data.
        path["quarters"][0][TARGETS[3]] = 999.0
        with self.assertRaises(DataError):
            project(bundle, path)

    def test_baselines_and_extreme_outputs_are_explicit(self):
        bundle = fit_models(self.rows, self.config, self.rules)
        path = self.path(bundle)
        path["quarters"][0][MACROS[0]] = 1000.0
        result = project(bundle, path)
        for item in result["paths"]:
            if ":no_change:" in item["model_id"]:
                expected = 0.0 if item["target"] == TARGETS[0] else bundle["origin_targets"][item["target"]]
                self.assertTrue(all(r["value"] == expected for r in item["values"]))
        dynamic = next(p for p in result["paths"] if ":dynamic:" in p["model_id"])
        self.assertLess(dynamic["values"][0]["value"], 0)
        self.assertTrue(any("invalid_for_pricing" in w for w in dynamic["values"][0]["warnings"]))
        self.assertTrue(any("outside_training_range" in w for w in dynamic["values"][0]["warnings"]))

    def test_unavailable_regression_does_not_masquerade_as_fit(self):
        for row in self.rows:
            row[MACROS[0]] = 2.0
        bundle = fit_models(self.rows, self.config, self.rules)
        unavailable = [m for m in bundle["models"] if m["status"] == "unavailable"]
        self.assertEqual(len(unavailable), 3)  # equity static, spread static and dynamic
        self.assertEqual(len(project(bundle, self.path(bundle))["paths"]), 14)

    def test_forward_path_bounds_preserve_historical_origins(self):
        for cutoff in (None, "2005Q4"):
            bundle = fit_models(self.rows, self.config, self.rules, cutoff)
            if cutoff:
                self.assertEqual(bundle["training_end"], cutoff)
            full = project(bundle, self.path(bundle, 8))
            for horizon in range(1, 9):
                with self.subTest(cutoff=cutoff, horizon=horizon):
                    path = self.path(bundle, horizon)
                    result = project(bundle, path)
                    self.assertEqual(result["origin"], bundle["training_end"])
                    self.assertEqual(result["horizon_quarters"], horizon)
                    self.assertEqual(len(result["paths"]), 17)
                    for actual, expected in zip(result["paths"], full["paths"]):
                        self.assertEqual(actual["values"], expected["values"][:horizon])
                        self.assertEqual([row["quarter"] for row in actual["values"]],
                                         [row["quarter"] for row in path["quarters"]])
            for horizon in (0, 9, 40):
                with self.subTest(cutoff=cutoff, horizon=horizon):
                    with self.assertRaisesRegex(DataError, "Macro path requires 1 to 8 consecutive quarters"):
                        project(bundle, self.path(bundle, horizon))

    def test_projection_cli_forward_path_bounds(self):
        bundle = fit_models(self.rows, self.config, self.rules, "2005Q4")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            body = canonical(bundle)
            (root / "models.json").write_bytes(body)
            (root / "manifest.json").write_bytes(canonical({"schema_version": 1, "models_sha256": digest(body)}))
            macro = root / "path.json"
            for horizon in (1, 4, 8, 0, 9, 40):
                with self.subTest(horizon=horizon):
                    macro.write_bytes(canonical(self.path(bundle, horizon)))
                    output = root / f"projection-{horizon}"
                    result = subprocess.run(
                        [sys.executable, "-m", "market_risk", "project-models", "--models", str(root),
                         "--macro-path", str(macro), "--output", str(output)],
                        capture_output=True, text=True, check=False,
                    )
                    if horizon in (1, 4, 8):
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(json.loads(result.stdout)["horizon_quarters"], horizon)
                        saved = json.loads((output / "projections.json").read_bytes())
                        self.assertEqual(saved["origin"], "2005Q4")
                        self.assertEqual(len(saved["paths"]), 17)
                        self.assertTrue(all(len(p["values"]) == horizon for p in saved["paths"]))
                    else:
                        self.assertEqual(result.returncode, 2)
                        self.assertEqual(result.stdout, "")
                        self.assertEqual(json.loads(result.stderr), {
                            "status": "invalid", "error": "Macro path requires 1 to 8 consecutive quarters"})
                        self.assertFalse(output.exists())

    def test_macro_path_rejects_gaps_nonfinite_and_wrong_origin(self):
        bundle = fit_models(self.rows, self.config, self.rules)
        for field, value in (("quarter", "2019Q1"), (MACROS[0], float("nan")), (MACROS[0], -100), (MACROS[1], True)):
            path = self.path(bundle)
            path["quarters"][0][field] = value
            with self.assertRaises(DataError):
                project(bundle, path)
        path = self.path(bundle)
        path["origin"] = "2018Q1"
        with self.assertRaises(DataError):
            project(bundle, path)

    def test_projection_roundtrip_integrity_and_cli_redaction(self):
        bundle = fit_models(self.rows, self.config, self.rules)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            body = canonical(bundle)
            (root / "models.json").write_bytes(body)
            (root / "manifest.json").write_bytes(canonical({"schema_version": 1, "models_sha256": digest(body)}))
            path = root / "path.json"
            path.write_bytes(canonical(self.path(bundle)))
            first = save_projection(root, path, root / "a")
            second = save_projection(root, path, root / "b")
            self.assertEqual(first["projection_sha256"], second["projection_sha256"])
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(main(["project-models", "--models", str(root), "--macro-path", str(path), "--output", str(root / "c")]), 0)
            emitted = json.loads(stdout.getvalue())
            self.assertEqual(set(emitted), {"status", "output", "projection_sha256", "horizon_quarters", "paths", "warning_rows"})
            (root / "models.json").write_bytes(body + b" ")
            with self.assertRaises(DataError):
                save_projection(root, path, root / "d")

    def test_projection_cli_rejects_duplicate_keys_and_preserves_valid_paths(self):
        bundle = fit_models(self.rows, self.config, self.rules)
        path = self.path(bundle)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            body = canonical(bundle)
            (root / "models.json").write_bytes(body)
            (root / "manifest.json").write_bytes(canonical({"schema_version": 1, "models_sha256": digest(body)}))
            macro = root / "path.json"

            def run(content, name):
                macro.write_text(content, encoding="utf-8")
                output = root / name
                result = subprocess.run(
                    [sys.executable, "-m", "market_risk", "project-models", "--models", str(root),
                     "--macro-path", str(macro), "--output", str(output)],
                    capture_output=True, text=True, check=False,
                )
                return result, output

            valid = json.dumps(path)
            cases = []
            for record in (path, path["quarters"][0]):
                for key, value in record.items():
                    token = json.dumps(key) + ": " + json.dumps(value)
                    for first in (value, "private-input-sentinel"):
                        duplicate = json.dumps(key) + ": " + json.dumps(first) + ", " + token
                        cases.append(valid.replace(token, duplicate, 1))
            cases.append(valid.replace('"policy_rate_pct":', '"policy_rate_pct": 2, "policy_rate_pct":', 1))
            cases.append(valid.replace('"policy_rate_pct":', '"policy_rate_pct": 2, "policy_rate_\\u0070ct":', 1))
            cases.append(valid.replace('"schema_version": 1', '"schema_version": {"private-key": 1, "private-key": 1}', 1))
            for index, content in enumerate(cases):
                with self.subTest(case=index):
                    result, output = run(content, f"invalid-{index}")
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stdout, "")
                    self.assertEqual(json.loads(result.stderr), {
                        "status": "invalid", "error": "Macro path JSON contains duplicate object keys"})
                    self.assertFalse(output.exists())
            artifacts = []
            for name in ("valid-first", "valid-second"):
                result, output = run(valid, name)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                report = json.loads(result.stdout)
                self.assertEqual(report["paths"], 17)
                self.assertEqual(report["horizon_quarters"], 8)
                content = (output / "projections.json").read_bytes()
                self.assertEqual(report["projection_sha256"], digest(content))
                artifacts.append(content)
            self.assertEqual(*artifacts)

    def test_real_fit_cli_reproduces_and_rejects_tampered_calendar(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            data.mkdir()
            records = [{"quarter": label(r["date"]), "quarter_end": quarter_end(r["date"]).isoformat(),
                        "dataset_id": DATASET, "market_alignment": "mean_of_three_monthly_means",
                        "original_index_ig_oas_ready": False, **{k: r[k] for k in (*TARGETS, *MACROS)}} for r in self.rows]

            def write_data(records):
                buffer = io.StringIO(newline="")
                writer = csv.DictWriter(buffer, fieldnames=list(records[0]), lineterminator="\n")
                writer.writeheader()
                writer.writerows(records)
                content = buffer.getvalue().encode()
                (data / "quarterly.csv").write_bytes(content)
                (data / "manifest.json").write_bytes(canonical({"dataset_id": DATASET, "use_scope": "local_internal_only",
                    "temporal_use": "retrospective_proxy_conditioning", "original_index_ig_oas_ready": False,
                    "csv_sha256": digest(content), "calendar_quarters": len(records)}))

            write_data(records)
            base = ["fit-models", "--config", str(ROOT / "configs/models.toml"), "--foundation", str(ROOT / "configs/foundation.toml"),
                    "--data", str(data), "--output"]
            hashes = []
            for name in ("first", "second"):
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    self.assertEqual(main(base + [str(root / name)]), 0)
                report = json.loads(stdout.getvalue())
                hashes.append(report["models_sha256"])
                self.assertNotIn("coefficients", report)
            self.assertEqual(*hashes)
            # Rewriting a hash cannot disguise an omitted calendar quarter.
            write_data(records[:10] + records[11:])
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(base + [str(root / "invalid")]), 2)
            self.assertFalse((root / "invalid").exists())

    def test_model_configuration_rejects_unsupported_choices(self):
        for replacement in ('rank_tolerance = nan', 'rank_tolerance = true', 'rank_tolerance = 0.1'):
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "models.toml"
                path.write_text((ROOT / "configs/models.toml").read_text().replace('rank_tolerance = 1e-10', replacement))
                with self.assertRaises(ConfigurationError):
                    load_model_config(path)
