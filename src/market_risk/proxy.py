"""Approved SPY/GZ proxy inputs, kept distinct from index/IG-OAS history."""

import csv
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import io
import json
import math
from pathlib import Path
import platform
import tomllib
import urllib.error
import urllib.request

from market_risk.data import DataConfig, DataError, canonical, digest, load_snapshot
from market_risk.intrinio import request_intrinio
from market_risk.preparation import growth, monthly_mean, prepare_rows, quarter_start, shift_quarter

GZ_URL = "https://www.federalreserve.gov/econresdata/notes/feds-notes/2016/files/ebp_csv.csv"


@dataclass(frozen=True)
class ProxyConfig:
    schema_version: int
    dataset_id: str
    observation_start: date
    observation_end: date
    minimum_common_days_per_month: int
    maximum_excluded_union_day_fraction: float
    equity_definition: str
    credit_definition: str
    market_alignment: str
    use_scope: str


def proxy_record(config: ProxyConfig) -> dict:
    record = asdict(config)
    for field in ("observation_start", "observation_end"):
        record[field] = record[field].isoformat()
    return record


def load_proxy_config(path: Path) -> ProxyConfig:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        if set(raw) != set(ProxyConfig.__dataclass_fields__):
            raise DataError("Proxy configuration has missing or unknown fields")
        expected = {"schema_version": 1, "dataset_id": "spy_gz_monthly_average_v1",
                    "equity_definition": "SPY_split_only_price_proxy",
                    "credit_definition": "GZ_nonfinancial_corporate_spread_proxy",
                    "market_alignment": "mean_of_three_monthly_means", "use_scope": "local_internal_only"}
        if type(raw["schema_version"]) is not int or any(raw[k] != v for k, v in expected.items()):
            raise DataError("Proxy identity, definitions, alignment or use scope is invalid")
        start, end = raw["observation_start"], raw["observation_end"]
        if type(start) is not date or type(end) is not date or not date(1993, 1, 1) <= start <= end < datetime.now(timezone.utc).date():
            raise DataError("Proxy dates must be completed historical dates on/after 1993-01-01")
        count, fraction = raw["minimum_common_days_per_month"], raw["maximum_excluded_union_day_fraction"]
        if type(count) is not int or not 10 <= count <= 23:
            raise DataError("Minimum common daily observations must be an integer from 10 to 23")
        if type(fraction) not in (int, float) or not math.isfinite(fraction) or not 0 <= fraction <= 0.10:
            raise DataError("Maximum excluded union-day fraction must be finite and between 0 and 0.10")
        return ProxyConfig(**raw)
    except (OSError, tomllib.TOMLDecodeError, TypeError):
        raise DataError("Cannot read a valid proxy configuration") from None


def intrinio_pages(endpoint: str, params: dict, field: str) -> list[dict]:
    """Follow bounded, nonrepeating cursor pagination, preserving response pages."""
    pages, seen = [], set()
    for _ in range(200):
        page = request_intrinio(endpoint, params)
        if not isinstance(page.get(field), list):
            raise DataError("Unexpected Intrinio collection schema")
        pages.append(page)
        token = page.get("next_page")
        if token is None:
            return pages
        if not isinstance(token, str) or not token or token in seen or not page[field]:
            raise DataError("Invalid or repeated Intrinio pagination cursor")
        seen.add(token)
        params = {**params, "next_page": token}
    raise DataError("Intrinio pagination exceeded the safety limit")


def parse_equity(pages: list[dict], split_pages: list[dict], config: ProxyConfig) -> dict[date, float | None]:
    """Use raw close and split ratios only; never use dividend-adjusted close."""
    values, security_id = {}, None
    if not isinstance(pages, list) or not isinstance(split_pages, list):
        raise DataError("Invalid SPY response pages")
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get("stock_prices"), list) or not isinstance(page.get("security"), dict):
            raise DataError("Invalid SPY response page")
        security = page.get("security", {})
        if security.get("ticker") != "SPY" or security.get("currency") != "USD" or not security.get("id"):
            raise DataError("SPY security identity or currency mismatch")
        if security_id is not None and security_id != security["id"]:
            raise DataError("SPY security identity changed during pagination")
        security_id = security["id"]
        for record in page["stock_prices"]:
            try:
                observed = date.fromisoformat(record["date"])
                value = record["close"]
                if record["frequency"] != "daily" or record["intraperiod"] is not False:
                    raise DataError("SPY requires completed daily closes")
                if observed in values or not config.observation_start <= observed <= config.observation_end or observed.weekday() >= 5:
                    raise DataError("SPY duplicate, weekend or out-of-range observation")
                if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value <= 0):
                    raise DataError("SPY close must be positive and finite or null")
                values[observed] = value
            except (KeyError, TypeError, ValueError) as exc:
                if isinstance(exc, DataError):
                    raise
                raise DataError("Invalid SPY observation schema") from None
    adjustments = {}
    for page in split_pages:
        if not isinstance(page, dict) or not isinstance(page.get("stock_price_adjustments"), list):
            raise DataError("Invalid split response page")
        for record in page["stock_price_adjustments"]:
            try:
                observed = date.fromisoformat(record["date"])
                ratio = record["split_ratio"]
                if observed in adjustments or not config.observation_start <= observed <= config.observation_end:
                    raise DataError("Duplicate or out-of-range split adjustment")
                if type(ratio) not in (int, float) or not math.isfinite(ratio) or ratio <= 0:
                    raise DataError("Invalid split ratio")
                if record.get("security", {}).get("id") != security_id:
                    raise DataError("Split security identity mismatch")
                adjustments[observed] = ratio
            except (KeyError, TypeError, ValueError) as exc:
                if isinstance(exc, DataError):
                    raise
                raise DataError("Invalid split adjustment schema") from None
    if not values:
        raise DataError("SPY history is empty")
    for observed, value in values.items():
        if value is not None:
            # Intrinio's 4-for-1 split ratio is 0.25: multiply earlier prices.
            factor = math.prod(ratio for effective, ratio in adjustments.items() if observed < effective)
            adjusted = value * factor
            if not math.isfinite(adjusted) or adjusted <= 0:
                raise DataError("Split-adjusted SPY close is invalid")
            values[observed] = adjusted
    return dict(sorted(values.items()))


def parse_gz(content: bytes) -> dict[date, float | None]:
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
        if reader.fieldnames != ["date", "gz_spread", "ebp", "est_prob"]:
            raise DataError("Federal Reserve GZ CSV schema changed")
        values, previous = {}, None
        for record in reader:
            if set(record) != set(reader.fieldnames) or any(v is None for v in record.values()):
                raise DataError("Malformed Federal Reserve GZ CSV row")
            observed = date.fromisoformat(record["date"])
            raw = record["gz_spread"].strip()
            value = None if raw in ("", "NA", "NaN", ".") else float(raw)
            if observed.day != 1 or (previous is not None and observed <= previous):
                raise DataError("GZ monthly labels are invalid, duplicate or unsorted")
            if value is not None and (not math.isfinite(value) or value < 0):
                raise DataError("GZ spread must be finite and nonnegative")
            values[observed], previous = value, observed
        if not values:
            raise DataError("GZ CSV is empty")
        return values
    except (UnicodeError, ValueError, TypeError, csv.Error) as exc:
        if isinstance(exc, DataError):
            raise
        raise DataError("Invalid Federal Reserve GZ CSV") from None


def fetch_proxy(config: ProxyConfig, root: Path) -> Path:
    params = {"start_date": config.observation_start.isoformat(), "end_date": config.observation_end.isoformat(), "page_size": 1000}
    prices = intrinio_pages("securities/SPY/prices", {**params, "frequency": "daily"}, "stock_prices")
    splits = intrinio_pages("securities/SPY/prices/adjustments/splits", params, "stock_price_adjustments")
    parse_equity(prices, splits, config)
    try:
        request = urllib.request.Request(GZ_URL, headers={"User-Agent": "market-risk-data/0.1", "Accept": "*/*"})
        with urllib.request.urlopen(request, timeout=30) as response:
            gz_content = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        raise DataError(f"Federal Reserve CSV rejected (HTTP {status})") from None
    except OSError:
        raise DataError("Federal Reserve CSV network failure") from None
    parse_gz(gz_content)
    saved = {"SPY.json": canonical({"prices": prices, "splits": splits}), "GZ.csv": gz_content}
    manifest = {"schema_version": 1, "config": proxy_record(config),
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                "temporal_use": "latest_revised_retrospective_proxy",
                "sources": {"SPY": "https://api-v2.intrinio.com/securities/SPY/prices", "GZ": GZ_URL},
                "units": {"SPY": "USD split-only price", "GZ": "percent spread"},
                "rights": "SPY subscription data: local internal use only; no redistribution. GZ: cite Federal Reserve research note.",
                "files": {name: digest(content) for name, content in saved.items()}}
    content = canonical(manifest)
    snapshot = root / digest(content)
    snapshot.mkdir(parents=True, exist_ok=False)
    for name, body in saved.items():
        (snapshot / name).write_bytes(body)
    (snapshot / "manifest.json").write_bytes(content)
    return snapshot


def load_proxy(snapshot: Path, config: ProxyConfig) -> tuple[dict, dict, dict]:
    try:
        content = (snapshot / "manifest.json").read_bytes()
        manifest = json.loads(content)
        required = {"schema_version", "config", "retrieved_at_utc", "temporal_use", "sources", "units", "rights", "files"}
        if not isinstance(manifest, dict) or set(manifest) != required or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1 or manifest["config"] != proxy_record(config) or snapshot.name != digest(content):
            raise DataError("Proxy snapshot manifest/config mismatch")
        if manifest["temporal_use"] != "latest_revised_retrospective_proxy" or manifest["units"] != {"SPY": "USD split-only price", "GZ": "percent spread"} or manifest["sources"] != {"SPY": "https://api-v2.intrinio.com/securities/SPY/prices", "GZ": GZ_URL}:
            raise DataError("Proxy snapshot source, units or temporal contract mismatch")
        if set(manifest["files"]) != {"SPY.json", "GZ.csv"}:
            raise DataError("Proxy snapshot inventory mismatch")
        files = {}
        for name, expected_hash in manifest["files"].items():
            body = (snapshot / name).read_bytes()
            if digest(body) != expected_hash:
                raise DataError("Proxy snapshot integrity failure")
            files[name] = body
        payload = json.loads(files["SPY.json"])
        return manifest, parse_equity(payload["prices"], payload["splits"], config), parse_gz(files["GZ.csv"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        if isinstance(exc, DataError):
            raise
        raise DataError("Cannot read a valid proxy snapshot") from None


def align_months(equity: dict, fred: dict, config: ProxyConfig) -> tuple[dict, list[dict]]:
    monthly = {name: {} for name in ("equity_price_usd", "yield_2y_pct", "yield_10y_pct")}
    diagnostics = []
    month = date(config.observation_start.year, config.observation_start.month, 1)
    while month <= config.observation_end:
        equity_dates = [d for d, v in equity.items() if d.year == month.year and d.month == month.month and v is not None]
        union = {d for source in (equity, fred["DGS2"], fred["DGS10"]) for d, v in source.items()
                 if d.year == month.year and d.month == month.month and v is not None}
        common = [d for d in equity_dates if fred["DGS2"].get(d) is not None and fred["DGS10"].get(d) is not None]
        excluded = 1 - len(common) / len(union) if union else 1
        complete = len(common) >= config.minimum_common_days_per_month and excluded <= config.maximum_excluded_union_day_fraction
        for name, source in (("equity_price_usd", equity), ("yield_2y_pct", fred["DGS2"]), ("yield_10y_pct", fred["DGS10"])):
            monthly[name][month] = sum(source[d] for d in common) / len(common) if complete else None
            result = monthly[name][month]
            if result is not None and not math.isfinite(result):
                raise DataError("Monthly proxy mean produced a nonfinite value")
        diagnostics.append({"month": month.isoformat(), "equity_valid_days": len(equity_dates),
                            "observed_union_days": len(union), "common_days": len(common),
                            "excluded_fraction": excluded, "complete": complete,
                            "first_common_date": min(common).isoformat() if common else None,
                            "last_common_date": max(common).isoformat() if common else None})
        month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    return monthly, diagnostics


def prepare_proxy(snapshot: Path, fred_snapshot: Path, config: ProxyConfig, fred_config: DataConfig, output: Path) -> dict:
    manifest, equity, gz = load_proxy(snapshot, config)
    _, fred = load_snapshot(fred_snapshot, fred_config)
    if not fred_config.observation_start <= config.observation_start <= config.observation_end <= fred_config.observation_end:
        raise DataError("FRED inputs do not cover the configured proxy interval")
    monthly, diagnostics = align_months(equity, fred, config)
    rows = prepare_rows(fred, fred_config)
    prices = {}
    selected = []
    for row in rows:
        end = date.fromisoformat(row["quarter_end"])
        quarter = quarter_start(end)
        if quarter < config.observation_start or end > config.observation_end:
            continue
        price = monthly_mean(monthly["equity_price_usd"], quarter)
        prices[quarter] = price
        row.pop("effective_yield_date")
        row.update(dataset_id=config.dataset_id, market_alignment=config.market_alignment,
                   equity_price_usd=price, equity_price_return_pct=growth(price, prices.get(shift_quarter(quarter, -1))),
                   credit_spread_proxy_pct=monthly_mean(gz, quarter),
                   yield_2y_pct=monthly_mean(monthly["yield_2y_pct"], quarter),
                   yield_10y_pct=monthly_mean(monthly["yield_10y_pct"], quarter),
                   original_index_ig_oas_ready=False)
        missing = [name for name, value in row.items() if value is None]
        if any(type(value) is float and not math.isfinite(value) for value in row.values()):
            raise DataError("Quarterly proxy transformation produced a nonfinite value")
        row["complete_available_factors"] = not missing
        row["missing_fields"] = "|".join(missing)
        row["proxy_factors_complete"] = not missing
        selected.append(row)
    if not selected:
        raise DataError("Proxy interval has no complete calendar quarters")
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(selected[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(selected)
    csv_content = buffer.getvalue().encode()
    code_dir = Path(__file__).parent
    code_hash = digest(canonical({name: digest((code_dir / name).read_bytes()) for name in ("proxy.py", "preparation.py", "data.py", "intrinio.py", "credentials.py")}))
    report = {"schema_version": 1, "dataset_id": config.dataset_id, "config": proxy_record(config),
              "proxy_snapshot_id": snapshot.name, "fred_snapshot_id": fred_snapshot.name,
              "fred_vintage_date": fred_config.vintage_date.isoformat(), "proxy_retrieved_at_utc": manifest["retrieved_at_utc"],
              "temporal_use": "retrospective_proxy_conditioning", "use_scope": config.use_scope,
              "csv_sha256": digest(csv_content), "config_sha256": digest(canonical(proxy_record(config))),
              "preparation_code_sha256": code_hash, "python_version": platform.python_version(),
              "runtime_dependencies": "Python standard library only", "calendar_quarters": len(selected),
              "complete_proxy_quarters": sum(row["proxy_factors_complete"] for row in selected),
              "incomplete_quarters": [{"quarter": row["quarter"], "missing_fields": row["missing_fields"]} for row in selected if not row["proxy_factors_complete"]],
              "monthly_alignment": diagnostics, "original_index_ig_oas_ready": False,
              "units": {"equity_price_usd": "USD split-only price", "equity_price_return_pct": "percent price return between quarterly average states",
                        "credit_spread_proxy_pct": "percent GZ spread", "yield_2y_pct": "percent", "yield_10y_pct": "percent"},
              "warnings": ["SPY ETF price proxy includes fund expenses and tracking differences; excludes dividends",
                           "GZ covers nonfinancial corporate bonds, not an investment-grade-only OAS",
                           "Quarterly states average three monthly means; they are not quarter-end market quotes",
                           "GZ averages its own bond panel; daily date identity with SPY/Treasuries cannot be established",
                           "Latest revised inputs do not establish historical release-time availability",
                           "No model fitting or event replay has been validated; raw and derived price data remain local"]}
    output.mkdir(parents=True, exist_ok=False)
    (output / "quarterly.csv").write_bytes(csv_content)
    (output / "manifest.json").write_bytes(canonical(report))
    return report
