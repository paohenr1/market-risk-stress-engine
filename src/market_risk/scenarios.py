"""Explicit quarterly proxy scenarios and factor changes; no portfolio pricing.

Historical windows share quarter-average states across factors. They are not
simultaneous endpoint quotes or realized holding returns. Missing states fail;
there is no nearest-date substitution or factor-specific worst-move selection.
"""

import json
import math
from dataclasses import asdict
from pathlib import Path
import platform

from market_risk.config import EvaluationRules
from market_risk.data import DataError, canonical, digest
from market_risk.models import (DATASET, MACROS, TARGETS, ModelConfig, _unique_macro_object,
                               finite, fit_at_origin, label, load_prepared, parse_quarter, project)
from market_risk.preparation import quarter_end, shift_quarter
from market_risk.evaluation import error_summary

UNITS = {"equity_price_change": "percent", "yield_2y_change": "basis_points",
         "yield_10y_change": "basis_points", "credit_spread_proxy_change": "basis_points"}
MACRO_UNITS = {"gdp_growth_annualized_pct": "percent_annualized_qoq_real",
               "unemployment_change_pp": "percentage_points_previous_quarter",
               "policy_rate_pct": "percent_quarterly_mean", "inflation_yoy_pct": "percent_yoy_quarterly_cpi"}


def load_scenarios(path: Path) -> list[dict]:
    try:
        raw = json.loads(path.read_bytes(), object_pairs_hook=_unique_macro_object)
        if not isinstance(raw, dict) or set(raw) != {"schema_version", "dataset_id", "scenarios"}:
            raise DataError("Scenario collection schema mismatch")
        if type(raw["schema_version"]) is not int or raw["schema_version"] != 1 or raw["dataset_id"] != DATASET:
            raise DataError("Unsupported scenario schema or dataset")
        if not isinstance(raw["scenarios"], list) or not raw["scenarios"]:
            raise DataError("Scenario collection must be nonempty")
        identities = set()
        for scenario in raw["scenarios"]:
            validate_scenario(scenario)
            if scenario["id"] in identities:
                raise DataError("Scenario IDs must be unique")
            identities.add(scenario["id"])
        return raw["scenarios"]
    except DataError:
        raise
    except (OSError, ValueError, TypeError, KeyError):
        raise DataError("Cannot read valid scenario collection") from None


def validate_scenario(record: dict) -> None:
    base = {"id", "version", "mode", "input_type", "narrative", "rationale", "origin", "horizon_quarters", "units", "convention", "reference"}
    if not isinstance(record, dict):
        raise DataError("Scenario must be a record")
    mode = record.get("mode")
    extras = {"historical_replay": {"end"}, "conditional_projection": {"quarters"}, "direct_shock": {"shocks"}}
    if not isinstance(mode, str) or mode not in extras or set(record) != base | extras[mode]:
        raise DataError("Unknown scenario mode or ambiguous scenario fields")
    if any(not isinstance(record[k], str) or not record[k].strip() for k in ("id", "narrative", "rationale")):
        raise DataError("Scenario identity, narrative and rationale must be nonempty")
    if type(record["version"]) is not int or record["version"] < 1:
        raise DataError("Scenario version must be a positive integer")
    origin = parse_quarter(record["origin"])
    h = record["horizon_quarters"]
    if type(h) is not int or not 1 <= h <= 8:
        raise DataError("Scenario forward horizon must be 1-8 quarters")
    if mode == "historical_replay":
        if (record["input_type"] != "observed_quarter_average_states" or record["units"] != UNITS
                or record["convention"] != "cumulative_changes" or record["reference"] != "historical_origin"
                or parse_quarter(record["end"]) != shift_quarter(origin, h)):
            raise DataError("Historical scenario dates, units or conventions mismatch")
    elif mode == "conditional_projection":
        if (record["input_type"] != "macro_path" or record["units"] != MACRO_UNITS
                or record["convention"] != "macro_levels_and_incremental_unemployment_changes"
                or record["reference"] != "previous_quarter"):
            raise DataError("Conditional scenario units or conventions mismatch")
        quarters = record["quarters"]
        if not isinstance(quarters, list) or len(quarters) != h:
            raise DataError("Conditional path does not match declared horizon")
        for i, row in enumerate(quarters, 1):
            if (not isinstance(row, dict) or set(row) != {"quarter", *MACROS}
                    or parse_quarter(row["quarter"]) != shift_quarter(origin, i)
                    or any(not finite(row[k]) for k in MACROS)):
                raise DataError("Conditional scenario needs consecutive finite macro conditions")
            if row["gdp_growth_annualized_pct"] <= -100 or row["inflation_yoy_pct"] <= -100 or abs(row["unemployment_change_pp"]) > 100:
                raise DataError("Conditional macro transformation domain invalid")
    else:
        if (record["input_type"] != "factor_shocks" or record["units"] != UNITS
                or record["convention"] != "cumulative_changes" or record["reference"] != "baseline"):
            raise DataError("Direct shocks need explicit cumulative baseline conventions")
        if not isinstance(record["shocks"], dict) or set(record["shocks"]) != set(UNITS) or any(not finite(v) for v in record["shocks"].values()):
            raise DataError("Direct shock factors must be known and finite")
        if record["shocks"]["equity_price_change"] <= -100:
            raise DataError("Direct equity shock must exceed -100 percent")


def factor_changes(origin: dict, path: list[dict]) -> list[dict]:
    """Compound incremental average-price changes; rate/spread differences in bp."""
    gross, result = 1.0, []
    for row in path:
        if (any(not finite(row.get(k)) for k in TARGETS) or row[TARGETS[0]] <= -100
                or any(row[k] <= -200 for k in TARGETS[1:3]) or row[TARGETS[3]] < 0):
            raise DataError("Historical or projected market state invalid for factor changes")
        gross *= 1 + row[TARGETS[0]] / 100
        changes = {"equity_price_change": 100 * (gross - 1),
                   "yield_2y_change": 100 * (row[TARGETS[1]] - origin[TARGETS[1]]),
                   "yield_10y_change": 100 * (row[TARGETS[2]] - origin[TARGETS[2]]),
                   "credit_spread_proxy_change": 100 * (row[TARGETS[3]] - origin[TARGETS[3]])}
        if any(not finite(v) for v in changes.values()):
            raise DataError("Cumulative factor changes exceed numerical domain")
        result.append({"quarter": label(row["date"]), "changes": changes})
    return result


def construct_scenario(record: dict, rows: list[dict], config: ModelConfig, rules: EvaluationRules) -> dict:
    validate_scenario(record)
    by_date = {r["date"]: r for r in rows}
    origin_date = parse_quarter(record["origin"])
    origin = by_date.get(origin_date)
    if origin is None:
        raise DataError("Scenario origin lacks a common observed market state")
    if record["mode"] in ("historical_replay", "direct_shock"):
        price = origin.get("equity_price_usd")
        if (not finite(price) or price <= 0 or any(not finite(origin.get(k)) for k in TARGETS[1:])
                or any(origin[k] <= -200 for k in TARGETS[1:3]) or origin[TARGETS[3]] < 0):
            raise DataError("Scenario origin requires a positive finite equity price and valid yield/spread levels")
    result = {"schema_version": 1, "scenario": record, "dataset_id": DATASET, "use_scope": "local_internal_only",
              "requested_origin": record["origin"], "effective_origin": record["origin"],
              "observation_convention": "quarterly_average_of_monthly_states_not_endpoint_quotes",
              "warnings": ["SPY price proxy excludes dividends; GZ is nonfinancial spread, not IG-only OAS",
                           "No probabilities or portfolio P&L; later pricing must validate factor domains"]}
    if record["mode"] == "direct_shock":
        shocks = record["shocks"]
        if (origin[TARGETS[1]] + shocks["yield_2y_change"] / 100 <= -200
                or origin[TARGETS[2]] + shocks["yield_10y_change"] / 100 <= -200
                or origin[TARGETS[3]] + shocks["credit_spread_proxy_change"] / 100 < 0):
            raise DataError("Direct shock produces a mathematically invalid rate or spread")
        result.update(changes=shocks, units=UNITS, reference="baseline", convention="cumulative_changes")
        result["warnings"].append("Direct shocks bypass models and have no statistical validation range")
        return result
    bundle = None
    try:
        bundle = fit_at_origin(rows, config, rules, origin_date)
    except DataError:
        if record["mode"] == "conditional_projection":
            raise
    if record["mode"] == "historical_replay":
        dates = [shift_quarter(origin_date, i) for i in range(1, record["horizon_quarters"] + 1)]
        if any(d not in by_date for d in dates):
            raise DataError("Historical scenario calendar coverage unavailable")
        future = [by_date[d] for d in dates]
        result.update(effective_end=record["end"], requested_end=record["end"],
                      origin_calendar_end=quarter_end(origin_date).isoformat(),
                      end_calendar_end=quarter_end(dates[-1]).isoformat(),
                      factor_path=factor_changes(origin, future), units=UNITS,
                      reference="historical_origin", convention="cumulative_changes")
        if bundle is None or any(not finite(r[k]) for r in future for k in MACROS):
            result["conditional_reconstruction"] = {"status": "unavailable", "reason": "insufficient_pre_event_training_or_missing_conditions"}
            return result
        macro_path = {"schema_version": 1, "dataset_id": DATASET, "origin": record["origin"],
                      "quarters": [{"quarter": label(r["date"]), **{k: r[k] for k in MACROS}} for r in future]}
        projections = project(bundle, macro_path)
        errors = []
        for p in projections["paths"]:
            multiplier = 1 if p["target"] == TARGETS[0] else 100
            errors.append({"model_id": p["model_id"], "target": p["target"],
                           "units": "percentage_points" if multiplier == 1 else "basis_points",
                           "path_errors": [(v["value"] - r[p["target"]]) * multiplier for v, r in zip(p["values"], future)],
                           "observed": [r[p["target"]] for r in future],
                           "warning_quarters": sum(bool(v["warnings"]) for v in p["values"])})
        for item in errors:
            item["summary"] = error_summary(item["path_errors"])
            item["horizon_errors"] = {str(h): item["path_errors"][h-1] for h in rules.horizons_quarters if h <= len(future)}
        result["conditional_reconstruction"] = {"status": "available", "pre_event_training_observations": bundle["eligible_training_observations"],
            "interpretation": "pre_event_fit_realized_macro_reconstruction_no_validation_selected_winner_or_release_time_claim",
            "available_evaluation_horizons": [h for h in rules.horizons_quarters if h <= len(future)],
            "unavailable_evaluation_horizons": [h for h in rules.horizons_quarters if h > len(future)],
            "paths": projections, "errors": errors, "training_diagnostics": bundle["models"]}
        return result
    unemployment = origin.get("unemployment_pct")
    if not finite(unemployment):
        raise DataError("Conditional scenario requires an observed unemployment origin level")
    implied_unemployment = []
    for row in record["quarters"]:
        unemployment += row["unemployment_change_pp"]
        if not 0 <= unemployment <= 100:
            raise DataError("Conditional scenario implies an invalid unemployment level")
        implied_unemployment.append(unemployment)
    path = {"schema_version": 1, "dataset_id": DATASET, "origin": record["origin"], "quarters": record["quarters"]}
    result.update(projections=project(bundle, path), implied_unemployment_pct=implied_unemployment,
                  training_diagnostics=bundle["models"],
                  interpretation="illustrative_conditional_macro_path_not_a_forecast")
    return result


def save_scenarios(path: Path, data: Path, config: ModelConfig, rules: EvaluationRules, output: Path) -> dict:
    definitions = load_scenarios(path)
    rows, manifest = load_prepared(data)
    results = []
    for record in definitions:
        try:
            results.append({"id": record["id"], "status": "constructed", "result": construct_scenario(record, rows, config, rules)})
        except (DataError, OverflowError):
            if record["mode"] != "historical_replay":
                raise DataError("Conditional or direct scenario cannot be constructed: check origin, training and transformed domains") from None
            # Valid definitions with unavailable data remain explicit batch results.
            results.append({"id": record["id"], "status": "unavailable", "reason": "missing_state_conditions_training_or_invalid_transformed_domain"})
    report = {"schema_version": 1, "dataset_id": DATASET, "use_scope": "local_internal_only",
              "model_config": asdict(config), "evaluation_rules": asdict(rules), "python_version": platform.python_version(),
              "prepared_csv_sha256": manifest["csv_sha256"], "scenario_config_sha256": digest(path.read_bytes()),
              "code_sha256": digest(canonical({name: digest(Path(__file__).with_name(name).read_bytes()) for name in ("scenarios.py", "models.py", "evaluation.py")})),
              "results": results}
    body = canonical(report)
    output.mkdir(parents=True, exist_ok=False)
    (output / "scenarios.json").write_bytes(body)
    (output / "manifest.json").write_bytes(canonical({"schema_version": 1, "scenarios_sha256": digest(body)}))
    return {"status": "constructed", "output": str(output.resolve()), "scenarios_sha256": digest(body),
            "constructed": sum(r["status"] == "constructed" for r in results),
            "unavailable": sum(r["status"] != "constructed" for r in results)}
