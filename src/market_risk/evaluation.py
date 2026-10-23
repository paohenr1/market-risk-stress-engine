"""Chronological retrospective conditional reconstruction, never real-time forecasts.

Selection uses validation only. Holdout is evaluated after choices are frozen;
expanding/rolling fits may use earlier, sequentially revealed holdout targets.
Every horizon's labels stay entirely inside its own partition.
"""

from dataclasses import asdict
import json
import math
from pathlib import Path
import platform
import tomllib

from market_risk.config import ConfigurationError, EvaluationRules, _require_keys
from market_risk.data import DataError, canonical, digest
from market_risk.models import (DATASET, MACROS, METHODS, TARGETS, ModelConfig,
                               finite, fit_at_origin, label, load_prepared, project)


def load_evaluation_config(path: Path, rules: EvaluationRules) -> dict:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        raise ConfigurationError("Cannot read evaluation configuration") from None
    validate_settings(raw, rules)
    return raw


def validate_settings(raw: dict, rules: EvaluationRules) -> None:
    _require_keys(raw, {"schema_version", "selection_horizon", "selection_metric", "rolling_calendar_quarters"}, "evaluation")
    if (type(raw["schema_version"]) is not int or raw["schema_version"] != 1
            or type(raw["selection_horizon"]) is not int or raw["selection_horizon"] != 1
            or raw["selection_metric"] != "mae"):
        raise ConfigurationError("Evaluation v1 selects on one-quarter validation MAE")
    window = raw["rolling_calendar_quarters"]
    if type(window) is not int or window < rules.minimum_training_quarters + 1:
        raise ConfigurationError("Rolling window must include training minimum and lag warmup")


def quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = probability * (len(values) - 1)
    low = int(position)
    high = min(low + 1, len(values) - 1)
    return ordered[low] + (position - low) * (ordered[high] - ordered[low])


def error_summary(values: list[float]) -> dict:
    """Empirical historical errors; descriptive ranges are not prediction intervals."""
    if not values:
        return {"observations": 0, "status": "unavailable"}
    return {"observations": len(values), "status": "available",
            "mae": math.fsum(abs(v) for v in values) / len(values),
            "rmse": math.sqrt(math.fsum(v * v for v in values) / len(values)),
            "bias": math.fsum(values) / len(values), "maximum_absolute_error": max(abs(v) for v in values),
            "error_q10": quantile(values, .1), "error_median": quantile(values, .5), "error_q90": quantile(values, .9)}


def validate_rules(rules: EvaluationRules) -> None:
    rules.validate_counts()
    horizons = rules.horizons_quarters
    if (not horizons or any(type(h) is not int or not 1 <= h <= 8 for h in horizons)
            or list(horizons) != sorted(set(horizons)) or 1 not in horizons):
        raise ConfigurationError("Evaluation horizons must be unique increasing 1-8 quarters and include 1")


def partition_errors(rows: list[dict], config: ModelConfig, rules: EvaluationRules,
                     start: int, stop: int, rolling_window: int) -> dict:
    """Score identical origin/label sets across every fixed candidate and window.

    Missing intermediate macros or actuals exclude an entire origin/horizon.
    Failed fits/projections remain in an exclusion ledger, never silently vanish.
    """
    validate_rules(rules)
    records, exclusions, fit_diagnostics = [], [], []
    cache = {}
    windows = {"expanding": None, "rolling": rolling_window}
    for h in rules.horizons_quarters:
        for i in range(start - 1, stop - h):
            origin, future = rows[i], rows[i + 1:i + h + 1]
            key = {"origin": label(origin["date"]), "endpoint": label(future[-1]["date"]), "horizon": h}
            if any(not finite(r[k]) for r in future for k in (*TARGETS, *MACROS)):
                exclusions.append({**key, "reason": "missing_actual_or_condition_in_path", "target": "all"})
                continue
            projected, failure = {}, None
            for window, length in windows.items():
                if (i, window) not in cache:
                    try:
                        bundle = fit_at_origin(rows[:i + 1], config, rules, origin["date"], length)
                        cache[i, window] = bundle
                        fit_diagnostics.append({"origin": key["origin"], "window": window,
                            "training_observations": bundle["eligible_training_observations"],
                            "training_start": bundle["training_start"],
                            "models": bundle["models"]})
                    except (DataError, OverflowError, ValueError) as exc:
                        cache[i, window] = None
                bundle = cache[i, window]
                if bundle is None:
                    failure = "insufficient_training_or_unavailable_fit"
                    break
                path = {"schema_version": 1, "dataset_id": DATASET, "origin": key["origin"],
                        "quarters": [{"quarter": label(r["date"]), **{k: r[k] for k in MACROS}} for r in future]}
                try:
                    projected[window] = project(bundle, path)["paths"]
                except (DataError, OverflowError, ValueError):
                    failure = "projection_failed_numerical_or_input_domain"
                    break
            if failure:
                exclusions.append({**key, "reason": failure, "target": "all"})
                continue
            for target in TARGETS:
                methods = (*METHODS, "dynamic") if target == TARGETS[3] else METHODS
                target_paths = {w: {p["model_id"].split(":")[-2]: p for p in paths if p["target"] == target} for w, paths in projected.items()}
                if any(set(paths) != set(methods) for paths in target_paths.values()):
                    exclusions.append({**key, "reason": "candidate_unavailable_common_comparison_required", "target": target})
                    continue
                for window in windows:
                    for method in methods:
                        path = target_paths[window][method]
                        value = path["values"][-1]["value"]
                        actual = future[-1][target]
                        multiplier = 1 if target == TARGETS[0] else 100
                        record = {**key, "target": target, "method": method, "window": window,
                                  "predicted": value, "actual": actual, "error": (value - actual) * multiplier,
                                  "error_units": "percentage_points_of_average_price_change" if multiplier == 1 else "basis_points",
                                  "warning_quarters": sum(bool(r["warnings"]) for r in path["values"]),
                                  "invalid_quarters": sum(any(w.startswith("invalid_for_pricing") for w in r["warnings"]) for r in path["values"])}
                        if target == TARGETS[0]:
                            predicted_gross = math.prod(1 + r["value"] / 100 for r in path["values"])
                            actual_gross = math.prod(1 + r[target] / 100 for r in future)
                            record["cumulative_average_price_change_error_pp"] = 100 * (predicted_gross - actual_gross)
                            if not finite(record["cumulative_average_price_change_error_pp"]):
                                raise DataError("Cumulative equity error exceeds numerical domain")
                        if not all(finite(record[k]) for k in ("predicted", "actual", "error")):
                            raise DataError("Evaluation produced a nonfinite error")
                        records.append(record)
    summaries = []
    for target in TARGETS:
        methods = (*METHODS, "dynamic") if target == TARGETS[3] else METHODS
        for h in rules.horizons_quarters:
            for window in windows:
                for method in methods:
                    selected = [r for r in records if (r["target"], r["horizon"], r["window"], r["method"]) == (target, h, window, method)]
                    summary = {"target": target, "horizon": h, "window": window, "method": method,
                               "error_units": "percentage_points_of_average_price_change" if target == TARGETS[0] else "basis_points",
                               **error_summary([r["error"] for r in selected]),
                               "warning_quarters": sum(r["warning_quarters"] for r in selected),
                               "invalid_quarters": sum(r["invalid_quarters"] for r in selected),
                               "origins": [r["origin"] for r in selected]}
                    if target == TARGETS[0]:
                        summary["cumulative_equity_error"] = error_summary([r["cumulative_average_price_change_error_pp"] for r in selected])
                    summaries.append(summary)
    return {"records": records, "summaries": summaries, "exclusions": exclusions,
            "origin_fit_diagnostics": fit_diagnostics,
            "calendar_quarters": stop - start, "start": label(rows[start]["date"]), "end": label(rows[stop - 1]["date"])}


def choose_models(validation: dict, rules: EvaluationRules) -> dict:
    """One-quarter MAE, ties prefer simpler methods then expanding windows."""
    choices = {}
    order = {name: i for i, name in enumerate((*METHODS, "dynamic"))}
    for target in TARGETS:
        candidates = [s for s in validation["summaries"] if s["target"] == target and s["horizon"] == 1
                      and s["observations"] >= rules.minimum_validation_quarters]
        if not candidates:
            choices[target] = {"status": "unavailable", "reason": "fewer_than_required_validation_observations"}
            continue
        best = min(candidates, key=lambda s: (s["mae"], order[s["method"]], s["window"] != "expanding"))
        choices[target] = {"status": "selected", "method": best["method"], "window": best["window"],
                           "validation_observations": best["observations"], "selection_mae": best["mae"],
                           "selection_horizon": 1, "selection_metric": "mae"}
    return choices


def evaluate(rows: list[dict], config: ModelConfig, rules: EvaluationRules, settings: dict) -> dict:
    validate_rules(rules)
    validate_settings(settings, rules)
    reserve = rules.minimum_validation_quarters + rules.final_holdout_quarters
    if len(rows) <= reserve or not rows:
        raise DataError("Insufficient history for chronological evaluation")
    validation_start, holdout_start = len(rows) - reserve, len(rows) - rules.final_holdout_quarters
    validation = partition_errors(rows, config, rules, validation_start, holdout_start, settings["rolling_calendar_quarters"])
    # The chosen specification/window and hash exist before holdout errors are computed.
    choices = choose_models(validation, rules)
    selection_hash = digest(canonical(choices))
    holdout = partition_errors(rows, config, rules, holdout_start, len(rows), settings["rolling_calendar_quarters"])
    selected_results = [s for s in holdout["summaries"] if choices[s["target"]]["status"] == "selected"
                        and (s["method"], s["window"]) == (choices[s["target"]]["method"], choices[s["target"]]["window"])]
    return {"schema_version": 1, "dataset_id": DATASET, "use_scope": "local_internal_only",
            "interpretation": "retrospective_realized_macro_conditioned_reconstruction",
            "model_config": asdict(config), "evaluation_rules": asdict(rules), "settings": settings,
            "validation": validation, "selection": choices, "selection_sha256": selection_hash,
            "holdout": holdout, "selected_holdout_summaries": selected_results,
            "warnings": ["Revised data and realized future macros do not establish release-time forecasting accuracy",
                         "Earlier holdout observations become available for subsequent refits, never for specification selection",
                         "Overlapping multi-quarter errors are dependent; quantiles are descriptive, not prediction intervals",
                         "Missing paths remain excluded; calendar reservations are not claims of equally many usable errors",
                         "SPY average-state price changes and GZ nonfinancial spreads retain proxy and redistribution limits",
                         "Developer validation only; no independent financial validation or scenario probabilities"]}


def save_evaluation(data: Path, config: ModelConfig, rules: EvaluationRules, settings: dict, output: Path) -> dict:
    rows, manifest = load_prepared(data)
    try:
        result = evaluate(rows, config, rules, settings)
    except (OverflowError, ZeroDivisionError):
        raise DataError("Evaluation exceeds numerical domain") from None
    result.update(prepared_csv_sha256=manifest["csv_sha256"], python_version=platform.python_version(),
                  code_sha256=digest(canonical({name: digest(Path(__file__).with_name(name).read_bytes()) for name in ("models.py", "evaluation.py")})))
    content = canonical(result)
    output.mkdir(parents=True, exist_ok=False)
    (output / "evaluation.json").write_bytes(content)
    markdown = evaluation_markdown(result).encode()
    (output / "evaluation.md").write_bytes(markdown)
    (output / "manifest.json").write_bytes(canonical({"schema_version": 1, "evaluation_sha256": digest(content), "markdown_sha256": digest(markdown)}))
    return {"status": "evaluated", "output": str(output.resolve()), "evaluation_sha256": digest(content),
            "selection_sha256": result["selection_sha256"], "validation_calendar_quarters": result["validation"]["calendar_quarters"],
            "holdout_calendar_quarters": result["holdout"]["calendar_quarters"],
            "selected_targets": sum(c["status"] == "selected" for c in result["selection"].values()),
            "excluded_paths": len(result["validation"]["exclusions"]) + len(result["holdout"]["exclusions"])}


def evaluation_markdown(result: dict) -> str:
    """Local human-readable metrics; never emit subscribed derivatives to stdout."""
    lines = ["# Conditional reconstruction results", "", "Local internal use only. Do not redistribute subscribed-data derivatives.", "",
             "Selection uses one-quarter validation MAE. Holdout results do not change the selection.", "",
             "| Target | Selected method | Window | Validation observations |", "|---|---|---|---|"]
    for target, choice in result["selection"].items():
        lines.append(f"| {target} | {choice.get('method', 'unavailable')} | {choice.get('window', 'unavailable')} | {choice.get('validation_observations', 0)} |")
    for stage in ("validation", "holdout"):
        lines += ["", f"## {stage.capitalize()}", "", f"{result[stage]['start']} through {result[stage]['end']}; {result[stage]['calendar_quarters']} calendar quarters.", "",
                  "Errors are percentage points of average-price change for equity and basis points for yields/spread.", "",
                  "| Target | h | Window | Method | n | MAE | RMSE | Bias | Error q10 | Error q90 | Invalid path quarters |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        for s in result[stage]["summaries"]:
            numbers = [f"{s[k]:.5g}" if k in s else "unavailable" for k in ("mae", "rmse", "bias", "error_q10", "error_q90")]
            lines.append(f"| {s['target']} | {s['horizon']} | {s['window']} | {s['method']} | {s['observations']} | {' | '.join(numbers)} | {s['invalid_quarters']} |")
        lines += ["", f"Excluded origin/horizon/target groups: {len(result[stage]['exclusions'])}. Full dates, reasons, cumulative equity errors and fit diagnostics are in evaluation.json."]
    lines += ["", "## Limitations", ""] + [f"- {warning}" for warning in result["warnings"]]
    return "\n".join(lines) + "\n"
