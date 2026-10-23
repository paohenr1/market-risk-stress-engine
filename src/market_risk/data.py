"""Official FRED snapshots with explicit native units and reproducible inputs.

Snapshot manifests contain no credentials. Raw JSON is retained locally and
hashed. An as-of vintage is a single historical database view, not a sequence
of information sets available at each historical forecast origin.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import tomllib
import urllib.error
import urllib.parse
import urllib.request

from market_risk.credentials import read_key


class DataError(ValueError):
    """A safe, credential-free data diagnostic."""


# Keep the initial download allowlist to the public government series reviewed
# in DATA_FEASIBILITY.md. Restricted equity and ICE series need separate review.
SERIES = {
    "GDPC1": ("Quarterly", "Billions of Chained 2017 Dollars", "SAAR"),
    "UNRATE": ("Monthly", "Percent", "SA"),
    "CPIAUCSL": ("Monthly", "Index 1982-1984=100", "SA"),
    "FEDFUNDS": ("Monthly", "Percent", "NSA"),
    "DGS2": ("Daily", "Percent", "NSA"),
    "DGS10": ("Daily", "Percent", "NSA"),
}


@dataclass(frozen=True)
class DataConfig:
    schema_version: int
    observation_start: date
    observation_end: date
    vintage_date: date
    endpoint_max_age_days: int
    series: tuple[str, ...]


def load_data_config(path: Path) -> DataConfig:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
        if set(raw) != set(DataConfig.__dataclass_fields__):
            raise DataError("Data configuration has missing or unknown fields")
        if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
            raise DataError("Unsupported data configuration schema")
        for name in ("observation_start", "observation_end", "vintage_date"):
            if type(raw[name]) is not date:
                raise DataError("Data dates must be TOML dates")
        if not raw["observation_start"] <= raw["observation_end"] <= raw["vintage_date"] <= datetime.now(timezone.utc).date():
            raise DataError("Require observation_start <= observation_end <= vintage_date <= today")
        age = raw["endpoint_max_age_days"]
        if type(age) is not int or not 0 <= age <= 5:
            raise DataError("Endpoint maximum age must be an integer between 0 and 5")
        ids = raw["series"]
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids) or set(ids) != set(SERIES) or len(ids) != len(SERIES):
            raise DataError("The initial pipeline requires exactly the six reviewed FRED series")
        return DataConfig(**{**raw, "series": tuple(ids)})
    except (OSError, tomllib.TOMLDecodeError, TypeError):
        raise DataError("Cannot read a valid data configuration") from None


def canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def config_record(config: DataConfig) -> dict:
    return {"schema_version": config.schema_version,
            "observation_start": config.observation_start.isoformat(),
            "observation_end": config.observation_end.isoformat(),
            "vintage_date": config.vintage_date.isoformat(),
            "endpoint_max_age_days": config.endpoint_max_age_days,
            "series": list(config.series)}


def request_json(endpoint: str, params: dict, key: str) -> dict:
    query = urllib.parse.urlencode({**params, "api_key": key, "file_type": "json"})
    request = urllib.request.Request("https://api.stlouisfed.org/fred/" + endpoint + "?" + query,
                                     headers={"User-Agent": "market-risk-data/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            content = response.read()
        # Prevent accidental persistence of a credential reflected in a response.
        if key.encode() in content:
            raise DataError("Response unexpectedly contains authentication material")
        payload = json.loads(content)
        if not isinstance(payload, dict):
            raise DataError("FRED response must be an object")
        return payload
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        raise DataError(f"FRED request rejected (HTTP {status})") from None
    except (OSError, ValueError) as exc:
        if isinstance(exc, DataError):
            raise
        raise DataError("FRED network or JSON response failure") from None


def parse_series(series_id: str, metadata: dict, observations: list[dict], config: DataConfig) -> dict[date, float | None]:
    frequency, units, adjustment = SERIES[series_id]
    if not isinstance(metadata, dict) or not isinstance(observations, list):
        raise DataError(f"{series_id}: invalid source schema")
    if metadata.get("id") != series_id or metadata.get("frequency") != frequency or metadata.get("units") != units or metadata.get("seasonal_adjustment_short") != adjustment:
        raise DataError(f"{series_id}: source frequency, units or adjustment differs from the declared contract")
    values: dict[date, float | None] = {}
    previous = None
    for record in observations:
        if not isinstance(record, dict):
            raise DataError(f"{series_id}: invalid observation schema")
        try:
            observed = date.fromisoformat(record["date"])
            start = date.fromisoformat(record["realtime_start"])
            end = date.fromisoformat(record["realtime_end"])
            raw = record["value"]
            if not isinstance(raw, str):
                raise ValueError
            value = None if raw == "." else float(raw)
        except (KeyError, TypeError, ValueError):
            raise DataError(f"{series_id}: invalid observation schema") from None
        if not config.observation_start <= observed <= config.observation_end or not start <= config.vintage_date <= end:
            raise DataError(f"{series_id}: observation outside requested period or vintage")
        if previous is not None and observed <= previous:
            raise DataError(f"{series_id}: duplicate or unsorted observation dates")
        if value is not None and (not math.isfinite(value) or (series_id in ("GDPC1", "CPIAUCSL") and value <= 0)):
            raise DataError(f"{series_id}: nonfinite or invalid level")
        if frequency == "Monthly" and observed.day != 1:
            raise DataError(f"{series_id}: monthly observation is not a period-start date")
        if frequency == "Quarterly" and (observed.day != 1 or observed.month not in (1, 4, 7, 10)):
            raise DataError(f"{series_id}: quarterly observation is not a period-start date")
        values[observed] = value
        previous = observed
    if not values:
        raise DataError(f"{series_id}: empty observations")
    return values


def fetch_snapshot(config: DataConfig, root: Path) -> Path:
    """Fetch metadata and paginated observations, then write a new local snapshot."""
    key = read_key("FRED_API_KEY")
    if not key:
        raise DataError("FRED_API_KEY is not configured locally")
    saved: dict[str, bytes] = {}
    params = {"realtime_start": config.vintage_date.isoformat(), "realtime_end": config.vintage_date.isoformat()}
    for series_id in config.series:
        try:
            metadata_response = request_json("series", {**params, "series_id": series_id}, key)
            metadata = metadata_response["seriess"][0]
            offset = 0
            observations = []
            pages = []
            expected_count = None
            while True:
                page = request_json("series/observations", {
                    **params, "series_id": series_id, "observation_start": config.observation_start.isoformat(),
                    "observation_end": config.observation_end.isoformat(), "units": "lin",
                    "sort_order": "asc", "limit": 10000, "offset": offset,
                }, key)
                count = page["count"]
                batch = page["observations"]
                if type(count) is not int or count < 1 or not isinstance(batch, list) or page["offset"] != offset or page["units"] != "lin":
                    raise DataError(f"{series_id}: inconsistent pagination or transformations")
                if expected_count is not None and count != expected_count:
                    raise DataError(f"{series_id}: source count changed during retrieval")
                expected_count = count
                if not batch or offset + len(batch) > count:
                    raise DataError(f"{series_id}: incomplete or excessive page")
                observations.extend(batch)
                pages.append(page)
                offset += len(batch)
                if offset == count:
                    break
            parse_series(series_id, metadata, observations, config)
            saved[f"{series_id}.json"] = canonical({"metadata_response": metadata_response, "pages": pages})
        except (KeyError, IndexError, TypeError):
            raise DataError(f"{series_id}: unexpected FRED response schema") from None
    manifest = {"schema_version": 1, "provider": "FRED", "config": config_record(config),
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                "temporal_use": "single_vintage_retrospective_conditioning",
                "source": "https://api.stlouisfed.org/fred/series/observations",
                "rights": "Reviewed government series; see docs/DATA_FEASIBILITY.md",
                "files": {name: digest(content) for name, content in sorted(saved.items())}}
    manifest_content = canonical(manifest)
    snapshot = root / digest(manifest_content)
    snapshot.mkdir(parents=True, exist_ok=False)
    for name, content in saved.items():
        (snapshot / name).write_bytes(content)
    # Manifest last: interrupted downloads never appear complete.
    (snapshot / "manifest.json").write_bytes(manifest_content)
    return snapshot


def load_snapshot(snapshot: Path, config: DataConfig) -> tuple[dict, dict[str, dict[date, float | None]]]:
    """Verify hashes and source semantics before offline preparation."""
    try:
        manifest_bytes = (snapshot / "manifest.json").read_bytes()
        manifest = json.loads(manifest_bytes)
        if snapshot.name != digest(manifest_bytes):
            raise DataError("Snapshot manifest hash does not match its directory")
        required = {"schema_version", "provider", "config", "retrieved_at_utc", "temporal_use", "source", "rights", "files"}
        if set(manifest) != required or manifest["schema_version"] != 1 or manifest["provider"] != "FRED" or manifest["config"] != config_record(config):
            raise DataError("Snapshot schema, provider or configuration mismatch")
        if set(manifest["files"]) != {f"{i}.json" for i in config.series}:
            raise DataError("Snapshot file inventory mismatch")
        values = {}
        for series_id in config.series:
            filename = f"{series_id}.json"
            content = (snapshot / filename).read_bytes()
            if digest(content) != manifest["files"][filename]:
                raise DataError(f"{series_id}: snapshot integrity check failed")
            payload = json.loads(content)
            observations = [row for page in payload["pages"] for row in page["observations"]]
            values[series_id] = parse_series(series_id, payload["metadata_response"]["seriess"][0], observations, config)
        return manifest, values
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        if isinstance(exc, DataError):
            raise
        raise DataError("Cannot read a valid complete snapshot") from None
