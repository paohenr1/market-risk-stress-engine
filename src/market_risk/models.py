"""Small conditional projection models; all fitted artifacts are local-use data.

Percent-valued targets remain in percentage units (one unit is 100 basis points
for yields/spreads). Regressions use training-only centering/scaling and QR,
never normal equations. Calendar gaps are retained when constructing AR lags.
"""

import csv
from dataclasses import dataclass, asdict
from datetime import date
import io
import json
import math
from pathlib import Path
import platform
import re
import tomllib

from market_risk.config import ConfigurationError, EvaluationRules, _require_keys
from market_risk.data import DataError, canonical, digest
from market_risk.preparation import quarter_end, shift_quarter

DATASET = "spy_gz_monthly_average_v1"
TARGETS = ("equity_price_return_pct", "yield_2y_pct", "yield_10y_pct", "credit_spread_proxy_pct")
MACROS = ("gdp_growth_annualized_pct", "unemployment_change_pp", "policy_rate_pct", "inflation_yoy_pct")
METHODS = ("no_change", "historical_mean", "ar1", "static")


@dataclass(frozen=True)
class ModelConfig:
    schema_version: int
    dataset_id: str
    credit_predictors: list[str]
    equity_predictors: list[str]
    yield_predictors: list[str]
    rank_tolerance: float


def load_model_config(path: Path) -> ModelConfig:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ConfigurationError("Cannot read model configuration") from None
    _require_keys(raw, set(ModelConfig.__annotations__), "models")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1 or raw["dataset_id"] != DATASET:
        raise ConfigurationError("Unsupported model schema or proxy dataset")
    for key in ("credit_predictors", "equity_predictors", "yield_predictors"):
        values = raw[key]
        if not isinstance(values, list) or not values or any(type(v) is not str or v not in MACROS for v in values) or len(set(values)) != len(values):
            raise ConfigurationError("Predictors must be unique supported macro names")
    tolerance = raw["rank_tolerance"]
    if type(tolerance) not in (int, float) or not math.isfinite(tolerance) or not 0 < tolerance <= 1e-6:
        raise ConfigurationError("rank_tolerance must be finite in (0, 1e-6]")
    return ModelConfig(**raw)


def parse_quarter(value: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}Q[1-4]", value):
        raise DataError("Quarter must use YYYYQn")
    try:
        return date(int(value[:4]), 1 + 3 * (int(value[-1]) - 1), 1)
    except ValueError:
        raise DataError("Quarter is outside the supported calendar") from None


def label(quarter: date) -> str:
    return f"{quarter.year:04d}Q{1 + (quarter.month - 1) // 3}"


def finite(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def load_prepared(folder: Path) -> tuple[list[dict], dict]:
    """Verify the prepared CSV hash and proxy definitions before fitting."""
    try:
        content = (folder / "quarterly.csv").read_bytes()
        manifest = json.loads((folder / "manifest.json").read_bytes())
        if (manifest["dataset_id"] != DATASET or manifest["use_scope"] != "local_internal_only"
                or manifest["temporal_use"] != "retrospective_proxy_conditioning"
                or manifest["original_index_ig_oas_ready"] is not False
                or manifest["csv_sha256"] != digest(content)):
            raise DataError("Prepared data provenance mismatch")
        reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
        required = {"quarter", "quarter_end", "dataset_id", "market_alignment", "original_index_ig_oas_ready", *TARGETS, *MACROS}
        if not required <= set(reader.fieldnames or ()):
            raise DataError("Prepared proxy columns are missing")
        rows = []
        for record in reader:
            quarter = parse_quarter(record["quarter"])
            if rows and quarter != shift_quarter(rows[-1]["date"], 1):
                raise DataError("Prepared calendar must be ordered, unique and uncompressed")
            if (record["quarter_end"] != quarter_end(quarter).isoformat() or record["dataset_id"] != DATASET
                    or record["market_alignment"] != "mean_of_three_monthly_means"
                    or record["original_index_ig_oas_ready"] != "False"):
                raise DataError("Prepared proxy row contract mismatch")
            row = {"date": quarter}
            for name in (*TARGETS, *MACROS):
                value = None if record[name] == "" else float(record[name])
                if value is not None and not finite(value):
                    raise DataError("Prepared factors must be finite or missing")
                row[name] = value
            if "equity_price_usd" in record:
                value = None if record["equity_price_usd"] == "" else float(record["equity_price_usd"])
                if value is not None and (not finite(value) or value <= 0):
                    raise DataError("Equity price level must be positive and finite or missing")
                row["equity_price_usd"] = value
            if "unemployment_pct" in record:
                row["unemployment_pct"] = None if record["unemployment_pct"] == "" else float(record["unemployment_pct"])
                if row["unemployment_pct"] is not None and (not finite(row["unemployment_pct"]) or not 0 <= row["unemployment_pct"] <= 100):
                    raise DataError("Unemployment level is outside its domain")
            rows.append(row)
        if not rows or len(rows) != manifest["calendar_quarters"]:
            raise DataError("Prepared calendar count mismatch")
        return rows, manifest
    except (OSError, ValueError, TypeError, KeyError, AttributeError, csv.Error):
        raise DataError("Cannot read valid prepared proxy inputs") from None


def regress(x: list[list[float]], y: list[float], tolerance: float) -> dict:
    """Fail clearly when a finite input exceeds floating-point calculation range."""
    if not x or not y or not x[0]:
        raise DataError("Regression requires nonempty predictors and observations")
    try:
        result = _regress(x, y, tolerance)
    except (OverflowError, ZeroDivisionError):
        raise DataError("Regression exceeds its numerical domain") from None
    if any(not finite(v) for key in ("coefficients", "means", "scales", "vif") for v in result[key]):
        raise DataError("Regression produced nonfinite parameters")
    return result


def _regress(x: list[list[float]], y: list[float], tolerance: float) -> dict:
    """OLS using centered, standardized columns and reorthogonalized QR.

    Reject rank deficiency explicitly. Coefficients are stored in standardized
    coordinates with training means/scales; projections reuse those transforms.
    """
    n, p = len(y), len(x[0])
    if n <= p + 1 or len(x) != n or any(len(row) != p for row in x):
        raise DataError("Insufficient regression degrees of freedom")
    if any(not finite(v) for row in x for v in row) or any(not finite(v) for v in y):
        raise DataError("Regression input is nonfinite")
    means = [math.fsum(row[j] for row in x) / n for j in range(p)]
    scales = [math.sqrt(math.fsum((row[j] - means[j]) ** 2 for row in x) / n) for j in range(p)]
    if any(s <= tolerance for s in scales):
        raise DataError("Regression has constant or nearly constant predictors")
    columns = [[1.0] * n] + [[(row[j] - means[j]) / scales[j] for row in x] for j in range(p)]
    q, r = [], [[0.0] * (p + 1) for _ in range(p + 1)]
    for j, column in enumerate(columns):
        work = column.copy()
        for _ in range(2):
            for i, prior in enumerate(q):
                dot = math.fsum(a * b for a, b in zip(prior, work))
                r[i][j] += dot
                work = [a - dot * b for a, b in zip(work, prior)]
        r[j][j] = math.sqrt(math.fsum(v * v for v in work))
        if r[j][j] <= tolerance * math.sqrt(n):
            raise DataError("Regression design is rank deficient")
        q.append([v / r[j][j] for v in work])
    rhs = [math.fsum(a * b for a, b in zip(column, y)) for column in q]
    beta = [0.0] * (p + 1)
    for i in range(p, -1, -1):
        beta[i] = (rhs[i] - math.fsum(r[i][j] * beta[j] for j in range(i + 1, p + 1))) / r[i][i]
    # VIF_j = n * diagonal((Z'Z)^-1), computed by triangular solves.
    inverse = [[0.0] * (p + 1) for _ in range(p + 1)]
    for j in range(p + 1):
        for i in range(p, -1, -1):
            inverse[i][j] = ((1.0 if i == j else 0.0) - math.fsum(r[i][k] * inverse[k][j] for k in range(i + 1, p + 1))) / r[i][i]
    vif = [n * math.fsum(v * v for v in inverse[j]) for j in range(1, p + 1)]
    return {"coefficients": beta, "means": means, "scales": scales, "vif": vif}


def estimate(model: dict, values: list[float]) -> float:
    return model["coefficients"][0] + math.fsum(b * (v - m) / s for b, v, m, s in
        zip(model["coefficients"][1:], values, model["means"], model["scales"]))


def diagnostics(errors: list[float], dates: list[date]) -> dict:
    pairs = [(errors[i - 1], errors[i]) for i in range(1, len(errors)) if dates[i] == shift_quarter(dates[i - 1], 1)]
    denominator = math.fsum(e * e for e in errors)
    # Gap-safe residual lag cosine; not a formal serial-correlation test.
    norm = math.sqrt(math.fsum(a * a for a, _ in pairs) * math.fsum(b * b for _, b in pairs))
    return {"rmse_training_units": math.sqrt(denominator / len(errors)),
            "adjacent_residual_pairs": len(pairs),
            "residual_lag1_cosine": math.fsum(a * b for a, b in pairs) / norm if norm else None,
            "adjacent_difference_ratio": math.fsum((b - a) ** 2 for a, b in pairs) / denominator if denominator else None}


def fit_models(rows: list[dict], config: ModelConfig, rules: EvaluationRules, train_end: str | None = None) -> dict:
    """Fit a fixed candidate set on a common sample, excluding validation/holdout.

    Sample minima count eligible observations, while reservations count calendar
    quarters. Excluded rows never become adjacent simply because data are missing.
    """
    rules.validate_counts()
    reserve = rules.minimum_validation_quarters + rules.final_holdout_quarters
    if len(rows) <= reserve:
        raise DataError("Insufficient calendar history for reserved partitions")
    latest = rows[-reserve - 1]["date"]
    end = parse_quarter(train_end) if train_end else latest
    if end > latest or end not in {row["date"] for row in rows}:
        raise DataError("Training cutoff overlaps reserved validation/holdout or is unavailable")
    result = fit_at_origin(rows, config, rules, end)
    result.update(validation_start=label(rows[-reserve]["date"]),
                  final_holdout_start=label(rows[-rules.final_holdout_quarters]["date"]))
    return result


def fit_at_origin(rows: list[dict], config: ModelConfig, rules: EvaluationRules,
                  end: date, training_calendar_quarters: int | None = None) -> dict:
    """Fit only observations through an origin for chronological evaluation.

    This low-level interface does not select partitions. The evaluation driver
    owns those boundaries, freezes selection before holdout, and never passes
    later target values into estimation. A rolling window includes a warmup row.
    """
    rules.validate_counts()
    if training_calendar_quarters is not None and (type(training_calendar_quarters) is not int or training_calendar_quarters < rules.minimum_training_quarters + 1):
        raise DataError("Rolling calendar window needs the training minimum plus lag warmup")
    if not rows or any(rows[i]["date"] != shift_quarter(rows[i-1]["date"], 1) for i in range(1, len(rows))):
        raise DataError("Model input calendar must be consecutive and ordered")
    history = {row["date"]: row for row in rows if row["date"] <= end}
    if end not in history:
        raise DataError("Training origin is unavailable")
    if training_calendar_quarters is not None:
        first = shift_quarter(end, 1 - training_calendar_quarters)
        history = {d: row for d, row in history.items() if d >= first}
    origin = history[end]
    needed = set(config.credit_predictors + config.equity_predictors + config.yield_predictors)
    common = []
    for d, row in history.items():
        prior = history.get(shift_quarter(d, -1))
        if prior and all(finite(row[k]) for k in (*TARGETS, *needed)) and all(finite(prior[k]) for k in TARGETS):
            common.append((row, prior))
    if len(common) < rules.minimum_training_quarters or any(not finite(origin[k]) for k in TARGETS):
        raise DataError("Insufficient eligible training observations or incomplete origin")
    fitted = []
    for target in TARGETS:
        predictors = (config.equity_predictors if target == TARGETS[0] else
                      config.credit_predictors if target == TARGETS[3] else config.yield_predictors)
        methods = (*METHODS, "dynamic") if target == TARGETS[3] else METHODS
        for method in methods:
            features = (["lag_target"] if method in ("ar1", "dynamic") else []) + (predictors if method in ("static", "dynamic") else [])
            x = [[prior[target] if key == "lag_target" else row[key] for key in features] for row, prior in common]
            y = [row[target] for row, _ in common]
            model = {"id": f"{DATASET}:{target}:{method}:v1", "target": target, "method": method,
                     "features": features, "training_observations": len(y), "status": "fitted"}
            if method in ("historical_mean", "no_change"):
                model.update(coefficients=[math.fsum(y) / len(y) if method == "historical_mean" else 0.0], means=[], scales=[], vif=[])
            else:
                try:
                    model.update(regress(x, y, config.rank_tolerance))
                except DataError as exc:
                    model.update(status="unavailable", reason=str(exc))
                    fitted.append(model)
                    continue
            predicted = [(0.0 if target == TARGETS[0] else prior[target]) if method == "no_change" else estimate(model, values)
                         for values, (_, prior) in zip(x, common)]
            model["diagnostics"] = diagnostics([actual - pred for actual, pred in zip(y, predicted)], [row["date"] for row, _ in common])
            model["feature_ranges"] = [[min(col), max(col)] for col in zip(*x)] if features else []
            model["warnings"] = ["Training residuals are not out-of-sample accuracy"]
            if any(v > 10 for v in model["vif"]):
                model["warnings"].append("VIF exceeds 10; correlated predictors may destabilize coefficients")
            if method in ("ar1", "dynamic") and abs(model["coefficients"][1] / model["scales"][0]) >= 1:
                model["warnings"].append("AR coefficient magnitude is at least one; recursive instability is possible")
            # Same specification on disjoint early/late windows and recent minimum window.
            windows = {"early_half": (0, len(y) // 2), "late_half": (len(y) // 2, len(y)),
                       "recent_minimum": (len(y) - rules.minimum_training_quarters, len(y))}
            sensitivity = {}
            if features:
                for name, (start, stop) in windows.items():
                    if stop - start < rules.minimum_training_quarters:
                        sensitivity[name] = {"status": "insufficient_sample"}
                        continue
                    try:
                        alternative = regress(x[start:stop], y[start:stop], config.rank_tolerance)
                        slopes = [b / s for b, s in zip(alternative["coefficients"][1:], alternative["scales"])]
                        sensitivity[name] = {"status": "fitted", "observations": stop - start,
                            "slopes_original_units": slopes,
                            "intercept_original_units": alternative["coefficients"][0] - math.fsum(b * m for b, m in zip(slopes, alternative["means"]))}
                    except DataError as exc:
                        sensitivity[name] = {"status": "unavailable", "reason": str(exc)}
            model["window_sensitivity"] = sensitivity
            fitted.append(model)
    return {"schema_version": 1, "dataset_id": DATASET, "use_scope": "local_internal_only",
            "temporal_use": "retrospective_proxy_conditioning", "config": asdict(config), "evaluation_rules": asdict(rules),
            "training_end": label(end), "training_start": label(common[0][0]["date"]),
            "eligible_training_observations": len(common), "excluded_training_quarters": len(history) - len(common),
            "origin_targets": {k: origin[k] for k in TARGETS}, "models": fitted,
            "warnings": ["SPY quarterly-average price changes; not realized quarter-end returns",
                         "GZ nonfinancial corporate spread; not investment-grade-only OAS",
                         "Revised data; supplied future macros are conditions, not historical forecasts",
                         "This fit contains candidates, not a validation-selected production model"]}


def project(bundle: dict, path: dict) -> dict:
    """Project every available candidate recursively from its observed origin.

    No realized future target is an accepted input. Invalid economic outputs are
    flagged and preserved, never silently clipped; they are not pricing-ready.
    """
    if not isinstance(path, dict) or set(path) != {"schema_version", "dataset_id", "origin", "quarters"}:
        raise DataError("Macro path schema mismatch")
    if type(path["schema_version"]) is not int or path["schema_version"] != 1 or path["dataset_id"] != DATASET or path["origin"] != bundle["training_end"]:
        raise DataError("Macro path dataset/version/origin mismatch")
    quarters = path["quarters"]
    if not isinstance(quarters, list) or not 1 <= len(quarters) <= 8:
        raise DataError("Macro path requires 1 to 8 consecutive quarters")
    origin = parse_quarter(path["origin"])
    for i, row in enumerate(quarters, 1):
        if not isinstance(row, dict) or set(row) != {"quarter", *MACROS}:
            raise DataError("Macro path requires exactly the documented macro fields")
        if parse_quarter(row["quarter"]) != shift_quarter(origin, i) or any(not finite(row[k]) for k in MACROS):
            raise DataError("Macro path must be consecutive with finite numeric conditions")
        if row["gdp_growth_annualized_pct"] <= -100 or row["inflation_yoy_pct"] <= -100 or abs(row["unemployment_change_pp"]) > 100:
            raise DataError("Macro path exceeds transformation domain")
    paths = []
    for model in bundle["models"]:
        if model["status"] != "fitted":
            continue
        target, method = model["target"], model["method"]
        previous, values = bundle["origin_targets"][target], []
        for row in quarters:
            x = [previous if name == "lag_target" else row[name] for name in model["features"]]
            value = (0.0 if target == TARGETS[0] else previous) if method == "no_change" else estimate(model, x)
            if not finite(value):
                raise DataError("Recursive projection became nonfinite")
            outside = [key for key, v, (low, high) in zip(model["features"], x, model["feature_ranges"]) if not low <= v <= high]
            flags = []
            if outside:
                flags.append("outside_training_range:" + "|".join(outside))
            if (target == TARGETS[0] and value <= -100) or (target == TARGETS[3] and value < 0) or (target in TARGETS[1:3] and value <= -200):
                flags.append("invalid_for_pricing:no_clipping_applied")
            values.append({"quarter": row["quarter"], "value": value, "warnings": flags})
            previous = value
        paths.append({"model_id": model["id"], "target": target, "units": "percent", "values": values, "warnings": model["warnings"]})
    return {"schema_version": 1, "dataset_id": DATASET, "use_scope": "local_internal_only", "origin": path["origin"],
            "horizon_quarters": len(quarters), "paths": paths, "warnings": bundle["warnings"],
            "convention": "incremental quarterly-average SPY price changes; yield/GZ spread levels; zero residual innovations"}


def save_fit(folder: Path, config: ModelConfig, rules: EvaluationRules, output: Path, train_end: str | None) -> dict:
    rows, manifest = load_prepared(folder)
    bundle = fit_models(rows, config, rules, train_end)
    bundle.update(prepared_csv_sha256=manifest["csv_sha256"],
                  code_sha256=digest(Path(__file__).read_bytes()), python_version=platform.python_version())
    body = canonical(bundle)
    output.mkdir(parents=True, exist_ok=False)
    (output / "models.json").write_bytes(body)
    (output / "manifest.json").write_bytes(canonical({"schema_version": 1, "models_sha256": digest(body)}))
    return {"status": "fitted", "output": str(output.resolve()), "models_sha256": digest(body),
            "training_end": bundle["training_end"], "eligible_training_observations": bundle["eligible_training_observations"],
            "fitted_candidates": sum(m["status"] == "fitted" for m in bundle["models"]),
            "unavailable_candidates": sum(m["status"] != "fitted" for m in bundle["models"])}


def _unique_macro_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise DataError("Macro path JSON contains duplicate object keys")
        result[key] = value
    return result


def save_projection(folder: Path, macro_path: Path, output: Path) -> dict:
    try:
        body = (folder / "models.json").read_bytes()
        manifest = json.loads((folder / "manifest.json").read_bytes())
        bundle = json.loads(body)
        if manifest != {"schema_version": 1, "models_sha256": digest(body)} or bundle["dataset_id"] != DATASET or bundle["use_scope"] != "local_internal_only":
            raise DataError("Fitted model integrity mismatch")
        result = project(bundle, json.loads(macro_path.read_bytes(), object_pairs_hook=_unique_macro_object))
        result["models_sha256"] = digest(body)
        result["macro_path_sha256"] = digest(macro_path.read_bytes())
        content = canonical(result)
    except DataError:
        raise
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
        raise DataError("Cannot read valid models and macro path or project within numerical domain") from None
    output.mkdir(parents=True, exist_ok=False)
    (output / "projections.json").write_bytes(content)
    return {"status": "projected", "output": str(output.resolve()), "projection_sha256": digest(content),
            "horizon_quarters": result["horizon_quarters"], "paths": len(result["paths"]),
            "warning_rows": sum(bool(row["warnings"]) for p in result["paths"] for row in p["values"])}
