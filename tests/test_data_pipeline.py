"""Behavioral checks using explicitly synthetic test observations, never models."""

from dataclasses import replace
from datetime import date
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

from market_risk.data import (DataError, SERIES, fetch_snapshot, load_data_config,
                              load_snapshot, parse_series, request_json)
from market_risk.preparation import prepare_rows, prepare_snapshot, shift_quarter

ROOT = Path(__file__).resolve().parents[1]


def test_payload(series_id, config):
    frequency, units, adjustment = SERIES[series_id]
    metadata = {"id": series_id, "frequency": frequency, "units": units,
                "seasonal_adjustment_short": adjustment}
    observations = []
    for year in (2020, 2021):
        for month in range(1, 13):
            if frequency == "Quarterly" and month not in (1, 4, 7, 10):
                continue
            observed = date(year, month, 1)
            if frequency == "Daily":
                # Just quarter-end test observations; no invented production data.
                if month not in (3, 6, 9, 12):
                    continue
                from market_risk.preparation import quarter_end, quarter_start
                observed = quarter_end(quarter_start(observed))
            value = 100 if series_id in ("GDPC1", "CPIAUCSL") else 4
            if series_id == "GDPC1" and observed == date(2021, 1, 1):
                value = 102
            if series_id == "CPIAUCSL" and year == 2021:
                value = 110
            observations.append({"date": observed.isoformat(), "value": str(value),
                                 "realtime_start": config.vintage_date.isoformat(),
                                 "realtime_end": config.vintage_date.isoformat()})
    return metadata, observations


class DataPipelineTests(unittest.TestCase):
    def setUp(self):
        original = load_data_config(ROOT / "configs" / "data.toml")
        self.config = replace(original, observation_start=date(2020, 1, 1), observation_end=date(2021, 12, 31))
        self.values = {sid: parse_series(sid, *test_payload(sid, self.config), self.config) for sid in SERIES}

    def test_hand_worked_transformations_and_calendar_gaps(self):
        rows = prepare_rows(self.values, self.config)
        self.assertEqual(len(rows), 8)
        self.assertIsNone(rows[0]["gdp_growth_annualized_pct"])
        self.assertAlmostEqual(rows[4]["gdp_growth_annualized_pct"], 100 * (1.02 ** 4 - 1))
        self.assertAlmostEqual(rows[4]["inflation_yoy_pct"], 10)
        self.assertEqual(rows[4]["unemployment_change_pp"], 0)
        self.values["UNRATE"][date(2020, 10, 1)] = None
        self.values["CPIAUCSL"][date(2020, 10, 1)] = None
        rows = prepare_rows(self.values, self.config)
        self.assertIsNone(rows[3]["unemployment_pct"])
        self.assertIsNone(rows[4]["unemployment_change_pp"])
        self.assertIsNone(rows[7]["inflation_yoy_pct"])
        self.assertEqual(rows[3]["quarter"], "2020Q4")
        self.assertFalse(rows[4]["full_market_dataset_ready"])

    def test_common_endpoint_never_uses_future_or_different_dates(self):
        end = date(2020, 3, 31)
        self.values["DGS2"][end] = None
        self.values["DGS2"][date(2020, 3, 30)] = 2
        self.values["DGS10"][date(2020, 3, 30)] = 3
        self.values["DGS2"][date(2020, 4, 1)] = 99
        row = prepare_rows(self.values, self.config)[0]
        self.assertEqual(row["effective_yield_date"], "2020-03-30")
        self.assertEqual((row["yield_2y_pct"], row["yield_10y_pct"]), (2, 3))
        row = prepare_rows(self.values, replace(self.config, endpoint_max_age_days=0))[0]
        self.assertIsNone(row["yield_2y_pct"])
        self.assertIsNone(row["yield_10y_pct"])

    def test_rejects_bad_units_dates_values_and_vintage(self):
        metadata, observations = test_payload("GDPC1", self.config)
        bad = [({**metadata, "units": "Percent"}, observations),
               (metadata, observations + observations[-1:]),
               (metadata, [{**observations[0], "value": "nan"}]),
               (metadata, [{**observations[0], "value": "0"}]),
               (metadata, [{**observations[0], "date": "2020-02-01"}]),
               (metadata, [{**observations[0], "realtime_start": "2099-01-01"}])]
        for meta, obs in bad:
            with self.subTest(meta=meta, obs=obs[:1]), self.assertRaises(DataError):
                parse_series("GDPC1", meta, obs, self.config)

    def test_paginated_snapshot_reproduces_offline_and_detects_tampering(self):
        calls = []
        def fake_request(endpoint, params, key):
            calls.append((endpoint, params))
            metadata, obs = test_payload(params["series_id"], self.config)
            if endpoint == "series":
                return {"seriess": [metadata]}
            offset = params["offset"]
            return {"count": len(obs), "offset": offset, "units": "lin", "observations": obs[offset:offset+3]}
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch("market_risk.data.read_key", return_value="private-test-key"), patch("market_risk.data.request_json", side_effect=fake_request):
                snapshot = fetch_snapshot(self.config, root / "raw")
            self.assertTrue(any(params.get("offset", 0) > 0 for _, params in calls))
            with patch("urllib.request.urlopen", side_effect=AssertionError("Offline mode contacted network")):
                first = prepare_snapshot(snapshot, self.config, root / "first")
                second = prepare_snapshot(snapshot, self.config, root / "second")
            self.assertEqual(first, second)
            self.assertEqual((root / "first" / "quarterly.csv").read_bytes(), (root / "second" / "quarterly.csv").read_bytes())
            with self.assertRaises(DataError):
                load_snapshot(snapshot, replace(self.config, endpoint_max_age_days=1))
            (snapshot / "GDPC1.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(DataError, "integrity"):
                load_snapshot(snapshot, self.config)

    def test_http_error_never_exposes_secret(self):
        secret = "private-test-key"
        error = urllib.error.HTTPError("https://example.com?api_key=" + secret, 401, secret, {}, None)
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(DataError) as raised:
                request_json("series", {"series_id": "GDPC1"}, secret)
        self.assertNotIn(secret, str(raised.exception))
        self.assertIn("401", str(raised.exception))

    def test_config_contract_rejects_unknown_and_boolean_policy(self):
        original = (ROOT / "configs" / "data.toml").read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.toml"
            for text in (original + "unknown = 1\n", original.replace("endpoint_max_age_days = 5", "endpoint_max_age_days = true"), original.replace("1976-01-01", "2099-01-01")):
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(DataError):
                    load_data_config(path)

    def test_year_boundary_quarter_shift(self):
        self.assertEqual(shift_quarter(date(2021, 1, 1), -1), date(2020, 10, 1))


if __name__ == "__main__":
    unittest.main()
