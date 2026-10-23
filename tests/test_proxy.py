"""Synthetic behavioral fixtures for the approved proxy acquisition/preparation."""

from dataclasses import replace
from datetime import date, timedelta
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from market_risk.data import DataError, load_data_config
from market_risk.proxy import (align_months, fetch_proxy, intrinio_pages, load_proxy,
                               load_proxy_config, parse_equity, parse_gz, prepare_proxy)

ROOT = Path(__file__).resolve().parents[1]
SECURITY = {"id": "synthetic-spy", "ticker": "SPY", "currency": "USD"}


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.config = replace(load_proxy_config(ROOT / "configs/proxy.toml"),
                              observation_start=date(2020, 1, 1), observation_end=date(2020, 6, 30))

    def price_record(self, observed, close):
        return {"date": observed.isoformat(), "close": close, "adj_close": 999,
                "frequency": "daily", "intraperiod": False}

    def test_price_adjustment_uses_only_split_and_never_adj_close(self):
        pages = [{"security": SECURITY, "stock_prices": [self.price_record(date(2020, 1, 2), 100), self.price_record(date(2020, 4, 1), 50)]}]
        splits = [{"stock_price_adjustments": [{"date": "2020-04-01", "split_ratio": .5, "factor": .49,
                                                "dividend": 1, "security": SECURITY}]}]
        values = parse_equity(pages, splits, self.config)
        self.assertEqual(values[date(2020, 1, 2)], 50)
        self.assertEqual(values[date(2020, 4, 1)], 50)

    def test_equity_rejects_duplicate_partial_and_wrong_security(self):
        record = self.price_record(date(2020, 1, 2), 100)
        bad_pages = [({**SECURITY, "currency": "CAD"}, [record]),
                     (SECURITY, [record, record]),
                     (SECURITY, [{**record, "intraperiod": True}]),
                     (SECURITY, [{**record, "close": float("nan")}]),
                     (SECURITY, [{**record, "frequency": "monthly"}])]
        for security, rows in bad_pages:
            with self.subTest(rows=rows), self.assertRaises(DataError):
                parse_equity([{"security": security, "stock_prices": rows}], [], self.config)

    def test_pagination_follows_cursor_and_rejects_cycles(self):
        pages = [{"stock_prices": [1], "next_page": "next"}, {"stock_prices": [2], "next_page": None}]
        with patch("market_risk.proxy.request_intrinio", side_effect=pages) as call:
            self.assertEqual(len(intrinio_pages("securities/SPY/prices", {}, "stock_prices")), 2)
            self.assertEqual(call.call_args.args[1]["next_page"], "next")
        with patch("market_risk.proxy.request_intrinio", return_value={"stock_prices": [1], "next_page": "same"}):
            with self.assertRaisesRegex(DataError, "cursor"):
                intrinio_pages("securities/SPY/prices", {}, "stock_prices")

    def test_gz_uses_spread_not_excess_bond_premium_and_preserves_missing(self):
        content = b"date,gz_spread,ebp,est_prob\n2020-01-01,2,-1,0.2\n2020-02-01,NA,5,0.3\n"
        values = parse_gz(content)
        self.assertEqual(values[date(2020, 1, 1)], 2)
        self.assertIsNone(values[date(2020, 2, 1)])
        for content in (b"date,ebp\n2020-01-01,2\n", b"date,gz_spread,ebp,est_prob\n2020-01-02,2,0,0\n", b"date,gz_spread,ebp,est_prob\n2020-01-01,nan,0,0\n"):
            with self.assertRaises(DataError):
                parse_gz(content)

    def daily_values(self):
        equity = {}
        observed = self.config.observation_start
        while observed <= self.config.observation_end:
            if observed.weekday() < 5:
                equity[observed] = 100 if observed.month <= 3 else 110
            observed += timedelta(days=1)
        fred = {"DGS2": {d: 2 for d in equity}, "DGS10": {d: 3 for d in equity},
                "GDPC1": {date(2020, 1, 1): 100, date(2020, 4, 1): 102},
                "UNRATE": {date(2020, m, 1): 4 for m in range(1, 7)},
                "CPIAUCSL": {date(2020, m, 1): 100 for m in range(1, 7)},
                "FEDFUNDS": {date(2020, m, 1): 1 for m in range(1, 7)}}
        return equity, fred

    def test_common_dates_and_coverage_limits(self):
        equity, fred = self.daily_values()
        fred["DGS2"][date(2020, 1, 2)] = None
        # This excluded extreme equity price must not enter the mean.
        equity[date(2020, 1, 2)] = 10000
        monthly, diagnostics = align_months(equity, fred, self.config)
        self.assertEqual(monthly["equity_price_usd"][date(2020, 1, 1)], 100)
        self.assertEqual(diagnostics[0]["common_days"], diagnostics[0]["equity_valid_days"] - 1)
        strict = replace(self.config, maximum_excluded_union_day_fraction=0)
        monthly, _ = align_months(equity, fred, strict)
        self.assertIsNone(monthly["equity_price_usd"][date(2020, 1, 1)])

    def test_missing_equity_dates_cannot_hide_in_coverage_denominator(self):
        equity, fred = self.daily_values()
        for observed in list(equity)[:5]:
            del equity[observed]
        monthly, diagnostics = align_months(equity, fred, self.config)
        self.assertGreater(diagnostics[0]["observed_union_days"], diagnostics[0]["equity_valid_days"])
        self.assertIsNone(monthly["equity_price_usd"][date(2020, 1, 1)])

    def test_nonfinite_aggregate_fails_before_export(self):
        equity, fred = self.daily_values()
        equity = {d: 1e308 for d in equity}
        with self.assertRaisesRegex(DataError, "nonfinite"):
            align_months(equity, fred, self.config)

    def test_snapshot_reproduces_offline_and_rejects_tampering(self):
        equity, fred = self.daily_values()
        prices = [{"security": SECURITY, "stock_prices": [self.price_record(d, v) for d, v in equity.items()], "next_page": None}]
        splits = [{"stock_price_adjustments": [], "next_page": None}]
        gz = b"date,gz_spread,ebp,est_prob\n" + b"".join(f"2020-{m:02d}-01,2,0,0\n".encode() for m in range(1, 7))
        fred_config = replace(load_data_config(ROOT / "configs/data.toml"), observation_start=date(2020, 1, 1), observation_end=date(2020, 6, 30))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with patch("market_risk.proxy.intrinio_pages", side_effect=[prices, splits]), patch("urllib.request.urlopen", return_value=io.BytesIO(gz)):
                snapshot = fetch_proxy(self.config, root / "raw")
            with patch("market_risk.proxy.load_snapshot", return_value=({}, fred)), patch("urllib.request.urlopen", side_effect=AssertionError("Network in offline preparation")):
                a = prepare_proxy(snapshot, root / "fred", self.config, fred_config, root / "a")
                b = prepare_proxy(snapshot, root / "fred", self.config, fred_config, root / "b")
            self.assertEqual(a, b)
            self.assertEqual((root / "a/quarterly.csv").read_bytes(), (root / "b/quarterly.csv").read_bytes())
            import csv
            rows = list(csv.DictReader(io.StringIO((root / "a/quarterly.csv").read_text())))
            self.assertAlmostEqual(float(rows[1]["equity_price_return_pct"]), 10)
            self.assertEqual(rows[1]["credit_spread_proxy_pct"], "2.0")
            self.assertEqual(rows[1]["original_index_ig_oas_ready"], "False")
            (snapshot / "GZ.csv").write_bytes(b"changed")
            with self.assertRaisesRegex(DataError, "integrity"):
                load_proxy(snapshot, self.config)

    def test_config_rejects_unknown_fields_and_dividend_return_definition(self):
        original = (ROOT / "configs/proxy.toml").read_text()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "proxy.toml"
            for text in (original + "unknown = 1\n", original.replace("SPY_split_only_price_proxy", "SPY_total_return"), original.replace("0.10", "nan")):
                path.write_text(text)
                with self.assertRaises(DataError):
                    load_proxy_config(path)


if __name__ == "__main__":
    unittest.main()
