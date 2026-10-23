"""Scenario conventions, alignment and executable CLI tests using synthetic data."""

from copy import deepcopy
import contextlib
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest

from market_risk.cli import main
from market_risk.config import load_foundation
from market_risk.data import DataError, canonical, digest
from market_risk.models import DATASET, MACROS, TARGETS, fit_at_origin, label, load_model_config, load_prepared
from market_risk.preparation import quarter_end
from market_risk.scenarios import (UNITS, construct_scenario, factor_changes, load_scenarios, validate_scenario)
from test_models import ROOT, synthetic_rows


class ScenarioTests(unittest.TestCase):
    def setUp(self):
        self.definitions = load_scenarios(ROOT / "configs/scenarios.json")
        self.rows = synthetic_rows()
        for row in self.rows:
            row["unemployment_pct"] = 4.0
            row["equity_price_usd"] = 100.0
            row[TARGETS[3]] += 2.0
        self.models = load_model_config(ROOT / "configs/models.toml")
        self.rules = load_foundation(ROOT / "configs/foundation.toml").evaluation

    def test_compounding_and_rate_basis_points(self):
        origin = self.rows[0]
        path = [deepcopy(r) for r in self.rows[1:3]]
        path[0][TARGETS[0]], path[1][TARGETS[0]] = -20, 10
        path[1][TARGETS[1]] = origin[TARGETS[1]] + .5
        result = factor_changes(origin, path)
        self.assertAlmostEqual(result[-1]["changes"]["equity_price_change"], -12)
        self.assertAlmostEqual(result[-1]["changes"]["yield_2y_change"], 50)

    def test_historical_windows_share_endpoints_and_preserve_gaps(self):
        scenario = self.definitions[0]
        result = construct_scenario(scenario, self.rows, self.models, self.rules)
        self.assertEqual(result["effective_origin"], scenario["origin"])
        self.assertEqual(result["effective_end"], scenario["end"])
        self.assertEqual(len(result["factor_path"]), 6)
        self.assertEqual(result["conditional_reconstruction"]["status"], "available")
        for comparison in result["conditional_reconstruction"]["errors"]:
            self.assertEqual(comparison["horizon_errors"]["4"], comparison["path_errors"][3])
            self.assertNotIn("8", comparison["horizon_errors"])
        missing = deepcopy(self.rows)
        missing[65][TARGETS[1]] = None  # 2009Q2 within financial crisis window
        with self.assertRaises(DataError):
            construct_scenario(scenario, missing, self.models, self.rules)

    def test_hypothetical_unemployment_levels_and_direct_units(self):
        hypothetical = self.definitions[3]
        result = construct_scenario(hypothetical, self.rows, self.models, self.rules)
        self.assertEqual(len(result["implied_unemployment_pct"]), 8)
        self.assertAlmostEqual(result["implied_unemployment_pct"][0], 4.3)
        bad = deepcopy(hypothetical)
        bad["quarters"][0]["unemployment_change_pp"] = 99
        with self.assertRaises(DataError):
            construct_scenario(bad, self.rows, self.models, self.rules)
        direct = construct_scenario(self.definitions[-1], self.rows, self.models, self.rules)
        self.assertEqual(direct["changes"]["yield_2y_change"], 200)
        self.assertEqual(direct["reference"], "baseline")
        self.assertNotIn("projections", direct)

    def test_ambiguous_schema_units_and_malformed_json_rejected(self):
        for key, value in (("mode", "forecast"), ("reference", "unknown"), ("horizon_quarters", True), ("units", {}), ("extra", 1)):
            record = deepcopy(self.definitions[-1])
            record[key] = value
            with self.assertRaises(DataError):
                validate_scenario(record)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scenarios.json"
            path.write_text('{"schema_version":1,"schema_version":1,"dataset_id":"spy_gz_monthly_average_v1","scenarios":[]}')
            with self.assertRaises(DataError):
                load_scenarios(path)

    def test_insufficient_event_training_is_disclosed_without_blocking_replay(self):
        rows = [r for r in self.rows if r["date"].year >= 2005]
        result = construct_scenario(self.definitions[0], rows, self.models, self.rules)
        self.assertEqual(result["conditional_reconstruction"]["status"], "unavailable")
        self.assertEqual(len(result["factor_path"]), 6)

    def test_observed_baselines_do_not_require_incoming_returns(self):
        historical = deepcopy(self.definitions[0])
        historical.update(origin=label(self.rows[90]["date"]), end=label(self.rows[91]["date"]), horizon_quarters=1)
        direct = deepcopy(self.definitions[-1])
        direct["origin"] = historical["origin"]
        self.rows[90][TARGETS[0]] = None
        replay = construct_scenario(historical, self.rows, self.models, self.rules)
        self.assertEqual(len(replay["factor_path"]), 1)
        self.assertEqual(replay["conditional_reconstruction"]["status"], "unavailable")
        self.assertEqual(construct_scenario(direct, self.rows, self.models, self.rules)["changes"], direct["shocks"])
        with self.assertRaises(DataError):
            fit_at_origin(self.rows, self.models, self.rules, self.rows[90]["date"])
        complete = deepcopy(self.rows)
        complete[90][TARGETS[0]] = 1.0
        fitted = fit_at_origin(self.rows, self.models, self.rules, self.rows[92]["date"])
        full = fit_at_origin(complete, self.models, self.rules, self.rows[92]["date"])
        self.assertEqual(fitted["eligible_training_observations"], full["eligible_training_observations"] - 2)
        for record in (historical, direct):
            for key, invalids in (("equity_price_usd", (None, 0, -1, float("inf"), float("nan"))),
                                  (TARGETS[1], (None, -200, float("inf"))),
                                  (TARGETS[2], (None, -200, float("nan"))),
                                  (TARGETS[3], (None, -1, float("inf")))):
                for value in invalids:
                    with self.subTest(mode=record["mode"], key=key, value=value):
                        bad = deepcopy(self.rows)
                        bad[90][key] = value
                        with self.assertRaisesRegex(DataError, "Scenario origin requires"):
                            construct_scenario(record, bad, self.models, self.rules)
                bad = deepcopy(self.rows)
                del bad[90][key]
                with self.assertRaises(DataError):
                    construct_scenario(record, bad, self.models, self.rules)
        for key in TARGETS:
            bad = deepcopy(self.rows)
            bad[91][key] = None
            with self.assertRaises(DataError):
                construct_scenario(historical, bad, self.models, self.rules)
        with self.assertRaisesRegex(DataError, "calendar coverage"):
            construct_scenario(historical, self.rows[:91] + self.rows[92:], self.models, self.rules)
        self.rows[-1][TARGETS[0]] = None
        with self.assertRaises(DataError):
            construct_scenario(self.definitions[3], self.rows, self.models, self.rules)

    def test_scenario_cli_reproduces_and_redacts(self):
        self.rows[59][TARGETS[0]] = None
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            data.mkdir()
            records = [{"quarter": label(r["date"]), "quarter_end": quarter_end(r["date"]).isoformat(),
                        "dataset_id": DATASET, "market_alignment": "mean_of_three_monthly_means",
                        "original_index_ig_oas_ready": False, "unemployment_pct": 4.0, "equity_price_usd": r["equity_price_usd"],
                        **{k: r[k] for k in (*TARGETS, *MACROS)}} for r in self.rows]
            buffer = io.StringIO(newline="")
            writer = csv.DictWriter(buffer, fieldnames=list(records[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(records)
            body = buffer.getvalue().encode()
            (data / "quarterly.csv").write_bytes(body)
            (data / "manifest.json").write_bytes(canonical({"dataset_id": DATASET, "use_scope": "local_internal_only",
                "temporal_use": "retrospective_proxy_conditioning", "original_index_ig_oas_ready": False,
                "csv_sha256": digest(body), "calendar_quarters": len(records)}))
            hashes = []
            for name in ("first", "repeat"):
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    code = main(["construct-scenarios", "--scenarios", str(ROOT / "configs/scenarios.json"),
                                 "--config", str(ROOT / "configs/models.toml"), "--foundation", str(ROOT / "configs/foundation.toml"),
                                 "--data", str(data), "--output", str(root / name)])
                self.assertEqual(code, 0)
                result = json.loads(stdout.getvalue())
                self.assertEqual(set(result), {"status", "output", "scenarios_sha256", "constructed", "unavailable"})
                self.assertEqual(result["constructed"], 6)
                hashes.append(result["scenarios_sha256"])
            self.assertEqual(*hashes)
            # Exercise the evaluation CLI on the same unrestricted prepared fixture.
            evaluation_hashes = []
            for name in ("evaluation", "evaluation-repeat"):
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    code = main(["evaluate-models", "--evaluation-config", str(ROOT / "configs/evaluation.toml"),
                                 "--config", str(ROOT / "configs/models.toml"), "--foundation", str(ROOT / "configs/foundation.toml"),
                                 "--data", str(data), "--output", str(root / name)])
                self.assertEqual(code, 0)
                report = json.loads(stdout.getvalue())
                self.assertEqual(report["selected_targets"], 4)
                self.assertNotIn("selection", report)
                self.assertNotIn("mae", report)
                evaluation_hashes.append(report["evaluation_sha256"])
                manifest = json.loads((root / name / "manifest.json").read_bytes())
                self.assertEqual(manifest["markdown_sha256"], digest((root / name / "evaluation.md").read_bytes()))
            self.assertEqual(*evaluation_hashes)
            loaded, _ = load_prepared(data)
            self.assertEqual(loaded[59]["equity_price_usd"], 100.0)
            self.assertIsNone(loaded[59][TARGETS[0]])
            for price in ("", "0", "-1", "nan", "inf", "invalid"):
                records[0]["equity_price_usd"] = price
                buffer = io.StringIO(newline="")
                writer = csv.DictWriter(buffer, fieldnames=list(records[0]), lineterminator="\n")
                writer.writeheader()
                writer.writerows(records)
                body = buffer.getvalue().encode()
                (data / "quarterly.csv").write_bytes(body)
                manifest = json.loads((data / "manifest.json").read_bytes())
                manifest["csv_sha256"] = digest(body)
                (data / "manifest.json").write_bytes(canonical(manifest))
                with self.subTest(price=price):
                    if price == "":
                        loaded, _ = load_prepared(data)
                        self.assertIsNone(loaded[0]["equity_price_usd"])
                    else:
                        with self.assertRaises(DataError):
                            load_prepared(data)
