"""Part 1 feasibility probe of official, key-free FRED CSV exports.

This is research tooling, not the production data pipeline. It measures coverage
without transforming values or claiming point-in-time availability. Only series
whose FRED pages identify them as public domain are saved here. No API key is
read, printed, or embedded in a URL; the production API adapter comes in Part 2.
"""

import csv
from datetime import date, datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SERIES = ("GDPC1", "UNRATE", "CPIAUCSL", "FEDFUNDS", "DGS2", "DGS10")


def main() -> int:
    """Save official exports plus coverage evidence; return nonzero on any failure."""
    destination = ROOT / "data" / "raw" / "feasibility"
    destination.mkdir(parents=True, exist_ok=True)
    records = []
    failed = False
    for series in SERIES:
        # Use the published CSV export surface, not HTML extraction or a paid crawler.
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
        record = {"series_id": series, "source_url": url}
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "market-risk-feasibility/0.1"})
            with urllib.request.urlopen(request, timeout=25) as response:
                payload = response.read()
            text = payload.decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(text))
            if reader.fieldnames not in (["observation_date", series], ["DATE", series]):
                raise ValueError("Unexpected official CSV schema")
            date_field = reader.fieldnames[0]
            rows = list(reader)
            dates = [date.fromisoformat(row[date_field]) for row in rows]
            valid = [date.fromisoformat(row[date_field]) for row in rows if row[series] not in ("", ".")]
            if not valid or dates != sorted(set(dates)):
                raise ValueError("Empty values, duplicate dates, or unordered observations")
            for row in rows:
                if row[series] not in ("", "."):
                    if not math.isfinite(float(row[series])):
                        raise ValueError("Nonfinite observation in official CSV")
            path = destination / f"{series}.csv"
            path.write_bytes(payload)
            record.update({
                "status": "retrieved", "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "saved_path": path.relative_to(ROOT).as_posix(),
                "first_valid_date": valid[0].isoformat(), "last_valid_date": valid[-1].isoformat(),
                "rows": len(rows), "missing_values": len(rows) - len(valid),
                "pre_event_quarters_with_any_observation": {
                    start: len({(d.year, (d.month - 1) // 3 + 1) for d in valid if d < date.fromisoformat(start)})
                    for start in ("2008-01-01", "2020-01-01", "2022-01-01")
                },
                "vintage": "latest_revised_export_not_point_in_time",
            })
        except (OSError, ValueError, urllib.error.URLError) as exc:
            failed = True
            record.update({"status": "blocked", "error": str(exc)})
        records.append(record)
        print(json.dumps(record))
    output = ROOT / "docs" / "evidence" / "fred-feasibility.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "schema_version": 1,
        "purpose": "coverage_probe_not_production_pipeline",
        "warning": "Counts are marginal raw coverage, not complete aligned training samples or release-time availability.",
        "series": records,
    }, indent=2) + "\n", encoding="utf-8")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
