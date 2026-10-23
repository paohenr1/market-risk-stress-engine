"""Transparent fictional cash-flow pricing under a two-node par-as-zero proxy.

Rates inside this module are decimal annual rates. Boundaries convert prepared
percent quotes or user basis-point shocks exactly once. Valuation time never
moves when scenario labels advance. These are dirty-value diagnostics, not
realized horizon returns, default losses or a calibrated issuer curve.
"""

import calendar
from dataclasses import dataclass, replace
from datetime import date
import json
import math
from pathlib import Path

from market_risk.data import DataError
from market_risk.models import DATASET, _unique_macro_object, finite, parse_quarter

RATE_FIELDS = ("yield_2y", "yield_10y", "credit_spread")
FACTOR_ORDER = ("equity_price", *RATE_FIELDS)
CONVENTIONS = {"valuation": "fixed_time", "discount_curve": "two_node_par_as_zero_proxy",
               "day_count": "actual_365_fixed", "coupon_frequency": 2,
               "coupon_dates": "unadjusted", "accrual": "actual_elapsed_over_coupon_period",
               "spread": "gz_percent_as_continuous_discount_margin_proxy",
               "holdings": "fractional_unrounded_fixed", "value": "dirty",
               "coupon_on_valuation_date": "already_paid_excluded"}


def require_keys(record: dict, fields: set[str], context: str) -> None:
    if not isinstance(record, dict) or set(record) != fields:
        raise DataError(f"{context} schema mismatch")


def number(value: object, context: str, minimum: float | None = None) -> float:
    if not finite(value) or (minimum is not None and value < minimum):
        raise DataError(f"{context} is outside its finite numeric domain")
    return float(value)


def add_months(anchor: date, months: int) -> date:
    """Shift from a fixed anchor day, clipping only in the destination month."""
    year, month = divmod(anchor.year * 12 + anchor.month - 1 + months, 12)
    return date(year, month + 1, min(anchor.day, calendar.monthrange(year, month + 1)[1]))


@dataclass(frozen=True)
class MarketState:
    equity_price: float
    yield_2y: float
    yield_10y: float
    credit_spread: float

    def validate(self) -> None:
        for name in FACTOR_ORDER:
            number(getattr(self, name), "Market state")
        if self.equity_price <= 0 or self.yield_2y <= -2 or self.yield_10y <= -2 or self.credit_spread < 0:
            raise DataError("Market state has invalid price, compounding base or spread")


@dataclass(frozen=True)
class Bond:
    """Coupon dates are explicit and regular from previous_coupon; face per unit100."""

    coupon_pct: float
    previous_coupon: date
    coupon_dates: tuple[date, ...]
    corporate: bool = False

    def validate(self, valuation_date: date) -> None:
        number(self.coupon_pct, "Coupon", 0)
        if type(self.corporate) is not bool or not self.coupon_dates or len(self.coupon_dates) > 200:
            raise DataError("Bond requires a finite regular coupon schedule")
        if not self.previous_coupon <= valuation_date < self.coupon_dates[0]:
            raise DataError("Bond schedule must bracket the fixed valuation date")
        for i, payment in enumerate(self.coupon_dates, 1):
            if payment != add_months(self.previous_coupon, 6 * i):
                raise DataError("Bond coupon schedule must be anchored regular semiannual dates")

    def cashflows(self, valuation_date: date) -> list[tuple[date, float, float]]:
        self.validate(valuation_date)
        coupon = self.coupon_pct / 2  # face100 * annual percentage /100 /2
        return [(payment, (payment - valuation_date).days / 365,
                 coupon + (100 if i == len(self.coupon_dates) - 1 else 0))
                for i, payment in enumerate(self.coupon_dates)]

    def accrued(self, valuation_date: date) -> float:
        self.validate(valuation_date)
        return self.coupon_pct / 2 * (valuation_date - self.previous_coupon).days / (self.coupon_dates[0] - self.previous_coupon).days


@dataclass(frozen=True)
class Position:
    identifier: str
    kind: str
    quantity: float  # equity shares, face100 bond units, or cashUSD
    bond: Bond | None = None


@dataclass(frozen=True)
class PortfolioSpec:
    record: dict
    valuation_date: date
    baseline_quarter: str
    total_usd: float
    normalized_equity_price: float


def load_portfolio(path: Path) -> PortfolioSpec:
    try:
        record = json.loads(path.read_bytes(), object_pairs_hook=_unique_macro_object)
        require_keys(record, {"schema_version", "dataset_id", "portfolio_id", "fictional", "currency", "valuation_date",
                              "baseline_quarter", "target_value_usd", "normalized_equity_price", "conventions", "positions", "sensitivity"}, "Portfolio")
        if (type(record["schema_version"]) is not int or record["schema_version"] != 1 or record["dataset_id"] != DATASET
                or record["fictional"] is not True or record["currency"] != "USD" or record["conventions"] != CONVENTIONS
                or not isinstance(record["portfolio_id"], str) or not record["portfolio_id"].strip()):
            raise DataError("Unsupported portfolio identity, currency or conventions")
        valuation_date = date.fromisoformat(record["valuation_date"])
        quarter = parse_quarter(record["baseline_quarter"])
        if not quarter <= valuation_date < add_months(quarter, 3):
            raise DataError("Baseline quarter must contain the valuation date")
        total = number(record["target_value_usd"], "Portfolio value")
        equity = number(record["normalized_equity_price"], "Normalized equity price")
        if total <= 0 or equity <= 0:
            raise DataError("Portfolio value and equity normalization must be positive")
        positions = record["positions"]
        if not isinstance(positions, list) or not positions:
            raise DataError("Portfolio positions must be nonempty")
        identifiers, weights = set(), []
        for p in positions:
            kind = p.get("kind") if isinstance(p, dict) else None
            if kind not in ("equity", "government_bond", "corporate_bond", "cash"):
                raise DataError("Unknown position kind")
            fields = {"id", "kind", "weight"} | ({"coupon_pct", "previous_coupon", "coupon_dates"} if kind.endswith("bond") else set())
            require_keys(p, fields, "Position")
            if not isinstance(p["id"], str) or not p["id"].strip() or p["id"] in identifiers:
                raise DataError("Position identifiers must be nonempty and unique")
            identifiers.add(p["id"])
            weight = number(p["weight"], "Position weight")
            if not 0 < weight <= 1:
                raise DataError("Position weight must be in (0,1]")
            weights.append(weight)
            if kind.endswith("bond"):
                configured_bond(p).validate(valuation_date)
        if not math.isclose(math.fsum(weights), 1, rel_tol=0, abs_tol=1e-12):
            raise DataError("Position weights must sum to one")
        sensitivity = record["sensitivity"]
        require_keys(sensitivity, {"bump_bp", "parallel_shifts_bp", "nonparallel_shifts_bp", "zero_proxy_overlays_bp"}, "Sensitivity")
        if number(sensitivity["bump_bp"], "Sensitivity bump") <= 0:
            raise DataError("Sensitivity bump must be positive")
        for key in ("parallel_shifts_bp", "zero_proxy_overlays_bp"):
            if not isinstance(sensitivity[key], list) or not sensitivity[key] or any(not finite(v) for v in sensitivity[key]):
                raise DataError("Sensitivity grids must contain finite numbers")
        pairs = sensitivity["nonparallel_shifts_bp"]
        if not isinstance(pairs, list) or not pairs or any(not isinstance(p, list) or len(p) != 2 or any(not finite(v) for v in p) for p in pairs):
            raise DataError("Nonparallel shifts require two finite basis-point values")
        return PortfolioSpec(record, valuation_date, record["baseline_quarter"], total, equity)
    except DataError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise DataError("Cannot read a valid portfolio configuration") from None


def configured_bond(record: dict) -> Bond:
    if not isinstance(record["coupon_dates"], list):
        raise DataError("Coupon dates must be an array")
    return Bond(record["coupon_pct"], date.fromisoformat(record["previous_coupon"]),
                tuple(date.fromisoformat(d) for d in record["coupon_dates"]), record["kind"] == "corporate_bond")


def zero_rate(state: MarketState, years: float, overlay: float = 0) -> float:
    """Semiannual quotes to continuous proxy, linear2-10y and flat outside."""
    state.validate()
    number(years, "Cash-flow time", 0)
    number(overlay, "Zero proxy overlay")
    short, long = 2 * math.log1p(state.yield_2y / 2), 2 * math.log1p(state.yield_10y / 2)
    weight = min(1, max(0, (years - 2) / 8))
    return short * (1 - weight) + long * weight + overlay


def bond_price(bond: Bond, state: MarketState, valuation_date: date, overlay: float = 0) -> dict:
    """Dirty/clean/accrued USD per face100; cashflows on valuation date excluded."""
    flows = bond.cashflows(valuation_date)
    try:
        dirty = math.fsum(amount * math.exp(-(zero_rate(state, t, overlay) + (state.credit_spread if bond.corporate else 0)) * t)
                          for _, t, amount in flows)
    except (OverflowError, ValueError):
        raise DataError("Bond discounting exceeds numerical domain") from None
    if not finite(dirty) or dirty <= 0:
        raise DataError("Bond price must be positive and finite")
    accrued = bond.accrued(valuation_date)
    warnings = []
    if any(t < 2 for _, t, _ in flows):
        warnings.append("Cashflows below2years use flat short-node extrapolation")
    if any(t > 10 for _, t, _ in flows):
        warnings.append("Cashflows beyond10years use flat long-node extrapolation")
    if bond.corporate:
        warnings.append("GZ nonfinancial spread is a continuous margin proxy, not issuer OAS or default loss")
    return {"dirty": dirty, "clean": dirty - accrued, "accrued": accrued, "warnings": warnings,
            "cashflows": [{"date": d.isoformat(), "years_act365": t, "amount_per100": a} for d, t, a in flows]}


def position_value(position: Position, state: MarketState, valuation_date: date, overlay: float = 0) -> float:
    state.validate()
    if not finite(position.quantity) or position.quantity < 0:
        raise DataError("Position quantity must be nonnegative and finite")
    if position.kind == "equity":
        value = position.quantity * state.equity_price
    elif position.kind == "cash":
        value = position.quantity
    elif position.kind in ("government_bond", "corporate_bond") and position.bond is not None:
        if position.bond.corporate != (position.kind == "corporate_bond"):
            raise DataError("Bond type and spread mapping disagree")
        value = position.quantity * bond_price(position.bond, state, valuation_date, overlay)["dirty"]
    else:
        raise DataError("Unknown or incomplete position")
    if not finite(value):
        raise DataError("Position value is nonfinite")
    return value


def build_holdings(spec: PortfolioSpec, baseline: MarketState) -> tuple[Position, ...]:
    """Size once from dirty market values; fractional quantities stay unrounded."""
    baseline.validate()
    positions = []
    for record in spec.record["positions"]:
        target, kind = spec.total_usd * record["weight"], record["kind"]
        bond = configured_bond(record) if kind.endswith("bond") else None
        unit_value = (baseline.equity_price if kind == "equity" else 1 if kind == "cash"
                      else bond_price(bond, baseline, spec.valuation_date)["dirty"])
        positions.append(Position(record["id"], kind, target / unit_value, bond))
    return tuple(positions)


def mark_portfolio(positions: tuple[Position, ...], state: MarketState, valuation_date: date, overlay: float = 0) -> dict:
    values = {p.identifier: position_value(p, state, valuation_date, overlay) for p in positions}
    if len(values) != len(positions):
        raise DataError("Duplicate position identifiers")
    total = math.fsum(values.values())
    if not finite(total) or total <= 0:
        raise DataError("Portfolio market value must be positive and finite")
    return {"positions": values, "total": total}


def shocked(baseline: MarketState, changes: dict) -> MarketState:
    require_keys(changes, {"equity_price_change", "yield_2y_change", "yield_10y_change", "credit_spread_proxy_change"}, "Factor changes")
    if any(not finite(v) for v in changes.values()):
        raise DataError("Factor changes must be finite")
    result = MarketState(baseline.equity_price * (1 + changes["equity_price_change"] / 100),
                         baseline.yield_2y + changes["yield_2y_change"] / 10000,
                         baseline.yield_10y + changes["yield_10y_change"] / 10000,
                         baseline.credit_spread + changes["credit_spread_proxy_change"] / 10000)
    result.validate()
    return result


def sensitivity(position: Position, state: MarketState, valuation_date: date, bump_bp: float) -> dict:
    """Dollar gradient and Hessian in decimal quote rates; finite-difference grid.

    Central differences normally apply. At the nonnegative spread boundary use
    explicit forward formulas; never evaluate a negative spread as a hidden fix.
    """
    h = number(bump_bp, "Sensitivity bump") / 10000
    if h <= 0:
        raise DataError("Sensitivity bump must be positive")
    value = position_value(position, state, valuation_date)
    one_sided = state.credit_spread < h
    cache = {}

    def evaluate(steps: tuple[int, int, int]) -> float:
        if steps not in cache:
            shifted = replace(state, **{name: getattr(state, name) + steps[i] * h for i, name in enumerate(RATE_FIELDS)})
            cache[steps] = position_value(position, shifted, valuation_date)
        return cache[steps]

    gradient, hessian = [], [[0.0] * 3 for _ in range(3)]
    for i in range(3):
        up = tuple(1 if k == i else 0 for k in range(3))
        down = tuple(-1 if k == i else 0 for k in range(3))
        if i == 2 and one_sided:
            twice = tuple(2 if k == i else 0 for k in range(3))
            gradient.append((-3 * value + 4 * evaluate(up) - evaluate(twice)) / (2 * h))
            hessian[i][i] = (evaluate(twice) - 2 * evaluate(up) + value) / h ** 2
        else:
            gradient.append((evaluate(up) - evaluate(down)) / (2 * h))
            hessian[i][i] = (evaluate(up) - 2 * value + evaluate(down)) / h ** 2
    for i in range(3):
        for j in range(i + 1, 3):
            def point(a: int, b: int) -> float:
                return evaluate(tuple(a if k == i else b if k == j else 0 for k in range(3)))
            mixed = ((point(1, 1) - point(-1, 1) - point(1, 0) + point(-1, 0)) / (2 * h ** 2)
                     if j == 2 and one_sided else
                     (point(1, 1) - point(1, -1) - point(-1, 1) + point(-1, -1)) / (4 * h ** 2))
            hessian[i][j] = hessian[j][i] = mixed
    parallel_first = gradient[0] + gradient[1]
    parallel_second = math.fsum(hessian[i][j] for i in (0, 1) for j in (0, 1))
    result = {"bump_bp": bump_bp, "rate_order": list(RATE_FIELDS), "gradient_usd_per_decimal_rate": gradient,
              "hessian_usd_per_decimal_rate_squared": hessian,
              "parallel_effective_duration": -parallel_first / value if value else None,
              "parallel_effective_convexity": parallel_second / value if value else None,
              "parallel_dv01_usd": -parallel_first / 10000, "spread_dv01_usd": -gradient[2] / 10000,
              "spread_difference_method": "forward_at_zero_boundary" if one_sided else "central"}
    if any(not finite(v) for v in gradient) or any(not finite(v) for row in hessian for v in row):
        raise DataError("Sensitivities exceed numerical domain")
    return result
