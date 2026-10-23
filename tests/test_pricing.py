"""Independent analytical and behavioral checks on fictional bond contracts."""

from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal, localcontext
import json
from pathlib import Path
import tempfile
import unittest

from market_risk.data import DataError
from market_risk.pricing import (Bond, MarketState, Position, add_months, bond_price,
                                load_portfolio, sensitivity, zero_rate)

ROOT = Path(__file__).resolve().parents[1]


class PricingTests(unittest.TestCase):
    def setUp(self):
        self.date = date(2026, 6, 30)
        self.dates = tuple(add_months(self.date, i * 6) for i in range(1, 5))
        self.state = MarketState(100, .04, .04, .01)

    def test_coupon_bond_matches_independent_decimal_discount_formula(self):
        bond = Bond(4, self.date, self.dates)
        with localcontext() as context:
            context.prec = 50
            expected = Decimal(0)
            for i, payment in enumerate(self.dates):
                years = Decimal((payment - self.date).days) / Decimal(365)
                amount = Decimal(102 if i == 3 else 2)
                expected += amount / (Decimal('1.02') ** (2 * years))
        self.assertAlmostEqual(bond_price(bond, self.state, self.date)["dirty"], float(expected), places=11)
        zero = Bond(0, self.date, self.dates)
        time = (self.dates[-1] - self.date).days / 365
        self.assertAlmostEqual(bond_price(zero, self.state, self.date)["dirty"], 100 / 1.02 ** (2 * time), places=11)

    def test_clean_accrued_and_coupon_date_treatment(self):
        valuation = date(2026, 9, 30)
        bond = Bond(4, self.date, self.dates)
        result = bond_price(bond, self.state, valuation)
        expected_accrued = 2 * (valuation - self.date).days / (self.dates[0] - self.date).days
        self.assertAlmostEqual(result["accrued"], expected_accrued)
        self.assertAlmostEqual(result["dirty"], result["clean"] + result["accrued"])
        self.assertEqual(bond_price(bond, self.state, self.date)["accrued"], 0)
        self.assertEqual(len(result["cashflows"]), 4)
        with self.assertRaises(DataError):
            bond_price(bond, self.state, self.dates[0])  # stale previous-coupon schedule

    def test_curve_compounding_interpolation_and_extrapolation(self):
        import math
        state = replace(self.state, yield_10y=.06)
        self.assertAlmostEqual(zero_rate(state, 2), 2 * math.log(1.02))
        self.assertAlmostEqual(zero_rate(state, 10), 2 * math.log(1.03))
        self.assertAlmostEqual(zero_rate(state, 6), math.log(1.02) + math.log(1.03))
        self.assertEqual(zero_rate(state, .5), zero_rate(state, 2))
        self.assertEqual(zero_rate(state, 15), zero_rate(state, 10))
        self.assertAlmostEqual(zero_rate(state, 6, .0025) - zero_rate(state, 6), .0025)

    def test_price_yield_spread_relationships_and_negative_yields(self):
        bond = Bond(4, self.date, self.dates)
        prices = [bond_price(bond, replace(self.state, yield_2y=y, yield_10y=y), self.date)["dirty"] for y in (-.01, 0, .02, .04, .1)]
        self.assertEqual(prices, sorted(prices, reverse=True))
        self.assertAlmostEqual(prices[1], 108)
        corporate = replace(bond, corporate=True)
        low = bond_price(corporate, self.state, self.date)["dirty"]
        high = bond_price(corporate, replace(self.state, credit_spread=.03), self.date)["dirty"]
        self.assertGreater(low, high)
        self.assertEqual(bond_price(bond, self.state, self.date)["dirty"], bond_price(bond, replace(self.state, credit_spread=.03), self.date)["dirty"])

    def test_finite_difference_duration_convexity_against_zero_coupon_algebra(self):
        zero = Bond(0, self.date, self.dates)
        p = Position("zero", "government_bond", 1, zero)
        risk = sensitivity(p, self.state, self.date, 1)
        t = (self.dates[-1] - self.date).days / 365
        self.assertAlmostEqual(risk["parallel_effective_duration"], t / 1.02, delta=1e-6)
        self.assertAlmostEqual(risk["parallel_effective_convexity"], (t * t + t / 2) / 1.02 ** 2, delta=1e-4)
        self.assertAlmostEqual(risk["spread_dv01_usd"], 0, delta=1e-9)
        corporate = Position("zero_corp", "corporate_bond", 1, replace(zero, corporate=True))
        price = bond_price(corporate.bond, self.state, self.date)["dirty"]
        self.assertAlmostEqual(sensitivity(corporate, self.state, self.date, 1)["spread_dv01_usd"], price * t / 10000, delta=1e-8)
        edge = sensitivity(corporate, replace(self.state, credit_spread=0), self.date, 1)
        self.assertEqual(edge["spread_difference_method"], "forward_at_zero_boundary")
        self.assertGreater(edge["spread_dv01_usd"], 0)

    def test_nonfinite_invalid_compounding_and_bad_schedules_fail(self):
        bond = Bond(4, self.date, self.dates)
        for state in (replace(self.state, equity_price=0), replace(self.state, yield_2y=-2),
                      replace(self.state, credit_spread=-.001), replace(self.state, yield_10y=float("nan"))):
            with self.assertRaises(DataError):
                bond_price(bond, state, self.date)
        with self.assertRaises(DataError):
            bond_price(replace(bond, coupon_dates=(date(2027, 1, 1),)), self.state, self.date)
        with self.assertRaises(DataError):
            long_zero = Bond(0, self.date, tuple(add_months(self.date, 6 * i) for i in range(1, 201)))
            bond_price(long_zero, replace(self.state, yield_2y=-1.9999999999999998, yield_10y=-1.9999999999999998), self.date)

    def test_portfolio_schema_contract(self):
        original = json.loads((ROOT / "configs/portfolio.json").read_bytes())
        for edit in (lambda r: r.update(currency="CAD"), lambda r: r.update(fictional=False),
                     lambda r: r.update(extra=True), lambda r: r["positions"][0].update(weight=.5),
                     lambda r: r["positions"][1].update(coupon_pct=True)):
            raw = deepcopy(original)
            edit(raw)
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "portfolio.json"
                path.write_text(json.dumps(raw))
                with self.assertRaises(DataError):
                    load_portfolio(path)
