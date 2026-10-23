"""Quarterly transformations that preserve calendar gaps and common endpoints."""

import calendar
import csv
from datetime import date, timedelta
import io
import math
from pathlib import Path
import platform

from market_risk import __version__
from market_risk.data import DataConfig, DataError, canonical, config_record, digest, load_snapshot


def quarter_start(value: date) -> date:
    return date(value.year, 1 + 3 * ((value.month - 1) // 3), 1)


def shift_quarter(value: date, count: int) -> date:
    total = value.year * 12 + value.month - 1 + count * 3
    year, month = divmod(total, 12)
    return date(year, month + 1, 1)


def quarter_end(value: date) -> date:
    month = value.month + 2
    return date(value.year, month, calendar.monthrange(value.year, month)[1])


def monthly_mean(values: dict[date, float | None], quarter: date) -> float | None:
    months = [values.get(date(quarter.year, quarter.month + i, 1)) for i in range(3)]
    if any(value is None for value in months):
        return None
    return sum(months) / 3


def growth(current: float | None, prior: float | None, exponent: int = 1) -> float | None:
    if current is None or prior is None:
        return None
    try:
        result = 100 * ((current / prior) ** exponent - 1)
    except (ZeroDivisionError, OverflowError):
        raise DataError("Growth transformation exceeds its numerical domain") from None
    if not math.isfinite(result):
        raise DataError("Growth transformation produced a nonfinite result")
    return result


def prepare_rows(values: dict[str, dict[date, float | None]], config: DataConfig) -> list[dict]:
    """Retain every complete calendar quarter, even if some factors are missing."""
    rows = []
    quarter = quarter_start(config.observation_start)
    if quarter < config.observation_start:
        quarter = shift_quarter(quarter, 1)
    while quarter_end(quarter) <= config.observation_end:
        end = quarter_end(quarter)
        prior = shift_quarter(quarter, -1)
        year_prior = shift_quarter(quarter, -4)
        unemployment = monthly_mean(values["UNRATE"], quarter)
        previous_unemployment = monthly_mean(values["UNRATE"], prior)
        row = {"quarter": f"{quarter.year}Q{1 + (quarter.month - 1) // 3}",
               "quarter_end": end.isoformat(),
               "gdp_growth_annualized_pct": growth(values["GDPC1"].get(quarter), values["GDPC1"].get(prior), 4),
               "unemployment_pct": unemployment,
               "unemployment_change_pp": None if unemployment is None or previous_unemployment is None else unemployment - previous_unemployment,
               "inflation_yoy_pct": growth(monthly_mean(values["CPIAUCSL"], quarter), monthly_mean(values["CPIAUCSL"], year_prior)),
               "policy_rate_pct": monthly_mean(values["FEDFUNDS"], quarter),
               "effective_yield_date": None, "yield_2y_pct": None, "yield_10y_pct": None}
        # Both yield nodes use one observed date; no per-series endpoint filling.
        for age in range(config.endpoint_max_age_days + 1):
            candidate = end - timedelta(days=age)
            short = values["DGS2"].get(candidate)
            long = values["DGS10"].get(candidate)
            if short is not None and long is not None:
                row.update(effective_yield_date=candidate.isoformat(), yield_2y_pct=short, yield_10y_pct=long)
                break
        missing = [name for name, value in row.items() if value is None]
        row["complete_available_factors"] = not missing
        row["missing_fields"] = "|".join(missing)
        # The full four-market-factor dataset remains unavailable until approved
        # equity and credit inputs are selected and aligned on common dates.
        row["full_market_dataset_ready"] = False
        rows.append(row)
        quarter = shift_quarter(quarter, 1)
    return rows


def prepare_snapshot(snapshot: Path, config: DataConfig, output: Path) -> dict:
    """Write deterministic CSV and provenance; no network access is required."""
    manifest, values = load_snapshot(snapshot, config)
    rows = prepare_rows(values, config)
    buffer = io.StringIO(newline="")
    fields = list(rows[0]) if rows else ["quarter", "quarter_end"]
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    content = buffer.getvalue().encode("utf-8")
    code_files = [Path(__file__), Path(__file__).with_name("data.py"), Path(__file__).with_name("credentials.py")]
    code_hash = digest(canonical({path.name: digest(path.read_bytes()) for path in code_files}))
    report = {"schema_version": 1, "snapshot_id": snapshot.name,
              "config": config_record(config), "config_sha256": digest(canonical(config_record(config))),
              "csv_sha256": digest(content), "preparation_code_sha256": code_hash,
              "package_version": __version__, "python_version": platform.python_version(),
              "runtime_dependencies": "Python standard library only",
              "temporal_use": manifest["temporal_use"],
              "warnings": ["A single vintage does not establish availability at each historical origin",
                           "Equity price and IG spread inputs remain unconfigured",
                           "Common yield dates cover only DGS2/DGS10; full replay alignment is pending"],
              "calendar_quarters": len(rows),
              "complete_available_factor_quarters": sum(row["complete_available_factors"] for row in rows),
              "incomplete_quarters": [{"quarter": row["quarter"], "missing_fields": row["missing_fields"]} for row in rows if not row["complete_available_factors"]],
              "source_missing_counts": {name: sum(v is None for v in series.values()) for name, series in values.items()},
              "full_market_dataset_ready": False,
              "output_units": {"gdp_growth_annualized_pct": "percent annualized QoQ",
                               "unemployment_pct": "percent", "unemployment_change_pp": "percentage points",
                               "inflation_yoy_pct": "percent YoY", "policy_rate_pct": "percent",
                               "yield_2y_pct": "percent", "yield_10y_pct": "percent"}}
    # Never overwrite a previous run's artifacts.
    output.mkdir(parents=True, exist_ok=False)
    (output / "quarterly.csv").write_bytes(content)
    (output / "manifest.json").write_bytes(canonical(report))
    return report
