"""Check SPX event-window coverage without saving or publishing index prices."""

from datetime import date, datetime, timezone
import json
import math
from pathlib import Path

from market_risk.data import DataError
from market_risk.intrinio import request_intrinio


WINDOWS = {
    "pre_2008_training_probe": ("2000-01-01", "2000-03-31"),
    "financial_crisis_probe": ("2008-07-01", "2008-09-30"),
    "covid_probe": ("2020-01-01", "2020-03-31"),
    "rates_2022_probe": ("2022-01-01", "2022-03-31"),
    "baseline_probe": ("2026-04-01", "2026-06-30"),
}


def main() -> int:
    results = []
    for label, (start, end) in WINDOWS.items():
        try:
            page = request_intrinio("indices/stock_market/$SPX/historical_data/level", {
                "start_date": start, "end_date": end, "page_size": 100, "sort_order": "asc"})
            prices = page["historical_data"]
            index = page["index"]
            if not isinstance(prices, list) or index["symbol"] != "$SPX":
                raise DataError("Unexpected index response")
            dates = []
            missing_count = 0
            for record in prices:
                observed = date.fromisoformat(record["date"])
                value = record["value"]
                if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value <= 0):
                    raise DataError("Invalid index close")
                if not date.fromisoformat(start) <= observed <= date.fromisoformat(end):
                    raise DataError("Index observation outside requested interval")
                if value is None:
                    missing_count += 1
                else:
                    dates.append(observed)
            if len(set(dates)) != len(dates):
                raise DataError("Duplicate index dates")
            results.append({"window": label, "requested_start": start, "requested_end": end,
                            "status": "observed" if dates else "empty", "count": len(dates),
                            "source_missing_count": missing_count,
                            "first_date": min(dates).isoformat() if dates else None,
                            "last_date": max(dates).isoformat() if dates else None,
                            "index_symbol": index["symbol"], "index_name": index["name"],
                            "metadata_observation_start": index.get("observation_start"),
                            "metadata_observation_end": index.get("observation_end"),
                            "additional_page": bool(page.get("next_page"))})
        except DataError as exc:
            results.append({"window": label, "status": "unavailable", "reason": str(exc)})
        except (ValueError, KeyError, TypeError):
            results.append({"window": label, "status": "unavailable", "reason": "Unexpected index response schema"})
    report = {"schema_version": 1, "checked_at_utc": datetime.now(timezone.utc).isoformat(),
              "endpoint": "https://api-v2.intrinio.com/indices/stock_market/$SPX/historical_data/level", "windows": results,
              "price_values_saved": False, "full_continuous_history_verified": False,
              "rights": "Authenticated access verified; snapshot/redistribution terms still require assessment",
              "credit_spread_coverage_verified": False}
    destination = Path(__file__).resolve().parents[1] / "docs" / "evidence" / "intrinio-coverage.json"
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if all(row["status"] == "observed" for row in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
