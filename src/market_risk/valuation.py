"""Fixed-holding replay/direct valuation, reconciled attribution and sensitivities.

The standalone command applies observed changes and direct shocks. Integrated
execution supplies a conditional adapter to the same pricing kernel. Both use
one quarter-average baseline with fixed contractual time and cashflows.
"""

from dataclasses import asdict, replace
import math
from pathlib import Path
import platform

from market_risk.config import FoundationConfig
from market_risk.data import DataError, canonical, digest
from market_risk.models import TARGETS, finite, label, load_prepared, parse_quarter
from market_risk.preparation import shift_quarter
from market_risk.pricing import (FACTOR_ORDER, RATE_FIELDS, MarketState, PortfolioSpec, Position,
                                bond_price, build_holdings, mark_portfolio, position_value, sensitivity, shocked)
from market_risk.scenarios import factor_changes, load_scenarios, validate_scenario


def close(actual: float, expected: float, foundation: FoundationConfig) -> bool:
    return abs(actual - expected) <= max(foundation.absolute_usd, foundation.relative * max(abs(actual), abs(expected)))


def observed_baseline(rows: list[dict], spec: PortfolioSpec) -> tuple[MarketState, dict]:
    row = next((r for r in rows if label(r["date"]) == spec.baseline_quarter), None)
    if row is None or not finite(row.get("equity_price_usd")) or row["equity_price_usd"] <= 0 or any(not finite(row[k]) for k in TARGETS[1:]):
        raise DataError("Baseline lacks a common valid observed proxy market state")
    state = MarketState(spec.normalized_equity_price, row[TARGETS[1]] / 100, row[TARGETS[2]] / 100, row[TARGETS[3]] / 100)
    state.validate()
    return state, row


def attribution(positions: tuple[Position, ...], baseline: MarketState, stressed: MarketState,
                spec: PortfolioSpec, order: tuple[str, ...]) -> dict:
    if len(order) != 4 or set(order) != set(FACTOR_ORDER):
        raise DataError("Attribution order must contain each factor once")
    state = baseline
    previous = mark_portfolio(positions, state, spec.valuation_date)["total"]
    result = {}
    for factor in order:
        state = replace(state, **{factor: getattr(stressed, factor)})
        current = mark_portfolio(positions, state, spec.valuation_date)["total"]
        result[factor] = current - previous
        previous = current
    return result


def value_state(positions: tuple[Position, ...], baseline: MarketState, stressed: MarketState,
                spec: PortfolioSpec, foundation: FoundationConfig, risks: dict) -> dict:
    base = mark_portfolio(positions, baseline, spec.valuation_date)
    marked = mark_portfolio(positions, stressed, spec.valuation_date)
    pnl = marked["total"] - base["total"]
    position_pnl = {p.identifier: marked["positions"][p.identifier] - base["positions"][p.identifier] for p in positions}
    asset_pnl = {kind: math.fsum(position_pnl[p.identifier] for p in positions if p.kind == kind) for kind in sorted({p.kind for p in positions})}
    factors = attribution(positions, baseline, stressed, spec, FACTOR_ORDER)
    reverse = attribution(positions, baseline, stressed, spec, ("equity_price", "credit_spread", "yield_10y", "yield_2y"))
    checks = {"position_reconciliation": close(math.fsum(position_pnl.values()), pnl, foundation),
              "asset_reconciliation": close(math.fsum(asset_pnl.values()), pnl, foundation),
              "factor_reconciliation": close(math.fsum(factors.values()), pnl, foundation),
              "reverse_factor_reconciliation": close(math.fsum(reverse.values()), pnl, foundation)}
    if not all(checks.values()):
        raise DataError("Valuation attribution failed reconciliation")
    changes = [getattr(stressed, k) - getattr(baseline, k) for k in RATE_FIELDS]
    first, second = 0.0, 0.0
    for p in positions:
        risk = risks[p.identifier]
        first += math.fsum(a * b for a, b in zip(risk["gradient_usd_per_decimal_rate"], changes))
        second += .5 * math.fsum(risk["hessian_usd_per_decimal_rate_squared"][i][j] * changes[i] * changes[j] for i in range(3) for j in range(3))
    equity_pnl = math.fsum(position_pnl[p.identifier] for p in positions if p.kind == "equity")
    approximation_first, approximation_second = equity_pnl + first, equity_pnl + first + second
    isolated = {}
    for factor in FACTOR_ORDER:
        one = replace(baseline, **{factor: getattr(stressed, factor)})
        isolated[factor] = mark_portfolio(positions, one, spec.valuation_date)["total"] - base["total"]
    return {"market_state": asdict(stressed), "baseline_value_usd": base["total"], "stressed_value_usd": marked["total"],
            "pnl_usd": pnl, "pnl_percent_baseline": 100 * pnl / base["total"],
            "baseline_positions_usd": base["positions"], "stressed_positions_usd": marked["positions"],
            "position_pnl_usd": position_pnl, "asset_class_pnl_usd": asset_pnl,
            "factor_order": list(FACTOR_ORDER), "factor_pnl_usd": factors,
            "reverse_factor_pnl_usd": reverse, "isolated_factor_pnl_usd": isolated,
            "isolated_interaction_residual_usd": pnl - math.fsum(isolated.values()),
            "largest_loss_contributors": sorted((k for k, v in position_pnl.items() if v < 0), key=position_pnl.get)[:3],
            "first_order_pnl_usd": approximation_first, "second_order_pnl_usd": approximation_second,
            "first_order_error_usd": approximation_first - pnl, "second_order_error_usd": approximation_second - pnl,
            "second_order_error_percent_baseline": 100 * (approximation_second - pnl) / base["total"],
            "error_relative_to_full_pnl": (approximation_second - pnl) / abs(pnl) if abs(pnl) > max(foundation.absolute_usd, 1e-6 * base["total"]) else None,
            "checks": checks}


def scenario_changes(record: dict, rows: list[dict], baseline: MarketState, spec: PortfolioSpec) -> list[dict]:
    """Return cumulative baseline-referenced changes; never reapply them incrementally."""
    if record["mode"] == "direct_shock":
        if record["origin"] != spec.baseline_quarter:
            raise DataError("Direct shock origin must match the common baseline quarter")
        return [{"quarter": label(shift_quarter(parse_quarter(record["origin"]), record["horizon_quarters"])), "changes": record["shocks"]}]
    if record["mode"] != "historical_replay":
        raise DataError("Conditional pricing adapter is not part of this increment")
    by_date = {r["date"]: r for r in rows}
    origin_date = parse_quarter(record["origin"])
    origin = by_date.get(origin_date)
    if origin is None or not finite(origin.get("equity_price_usd")) or origin["equity_price_usd"] <= 0 or any(not finite(origin.get(k)) for k in TARGETS[1:]):
        raise DataError("Historical origin lacks observed market levels")
    MarketState(origin["equity_price_usd"], origin[TARGETS[1]] / 100, origin[TARGETS[2]] / 100, origin[TARGETS[3]] / 100).validate()
    dates = [shift_quarter(origin_date, i) for i in range(1, record["horizon_quarters"] + 1)]
    if any(d not in by_date for d in dates):
        raise DataError("Historical replay calendar unavailable")
    return factor_changes(origin, [by_date[d] for d in dates])


def diagnostics(positions: tuple[Position, ...], baseline: MarketState, spec: PortfolioSpec, risks: dict) -> list[dict]:
    """Small/large parallel and nonparallel full-repricing comparisons per position."""
    results = []
    grid = [(v, v, "parallel") for v in spec.record["sensitivity"]["parallel_shifts_bp"]]
    grid += [(a, b, "nonparallel") for a, b in spec.record["sensitivity"]["nonparallel_shifts_bp"]]
    for a, b, kind in grid:
        stressed = replace(baseline, yield_2y=baseline.yield_2y + a / 10000, yield_10y=baseline.yield_10y + b / 10000)
        stressed.validate()
        for p in positions:
            if p.bond is None:
                continue
            base = position_value(p, baseline, spec.valuation_date)
            pnl = position_value(p, stressed, spec.valuation_date) - base
            risk = risks[p.identifier]
            change = [a / 10000, b / 10000, 0]
            first = math.fsum(risk["gradient_usd_per_decimal_rate"][i] * change[i] for i in range(3))
            second = first + .5 * math.fsum(risk["hessian_usd_per_decimal_rate_squared"][i][j] * change[i] * change[j] for i in range(3) for j in range(3))
            results.append({"position": p.identifier, "shift_kind": kind, "short_bp": a, "long_bp": b,
                            "full_pnl_usd": pnl, "duration_or_keyrate_pnl_usd": first, "quadratic_pnl_usd": second,
                            "first_order_error_usd": first - pnl, "second_order_error_usd": second - pnl,
                            "second_order_error_percent_baseline": 100 * (second - pnl) / base,
                            "relative_error_to_pnl": (second - pnl) / abs(pnl) if abs(pnl) > max(.01, 1e-6 * base) else None})
    return results


def price_scenarios(rows: list[dict], spec: PortfolioSpec, definitions: list[dict], foundation: FoundationConfig,
                    conditional_adapter=None) -> dict:
    baseline, origin = observed_baseline(rows, spec)
    positions = build_holdings(spec, baseline)
    base = mark_portfolio(positions, baseline, spec.valuation_date)
    if not close(base["total"], spec.total_usd, foundation):
        raise DataError("Initial position sizing failed target-value reconciliation")
    risks = {p.identifier: sensitivity(p, baseline, spec.valuation_date, spec.record["sensitivity"]["bump_bp"]) for p in positions}
    contracts = []
    for p in positions:
        contract = {"id": p.identifier, "kind": p.kind, "quantity": p.quantity,
                    "quantity_units": "face100_units" if p.bond else "equity_shares" if p.kind == "equity" else "USD_cash"}
        if p.bond:
            contract["bond"] = {"coupon_pct": p.bond.coupon_pct, "previous_coupon": p.bond.previous_coupon.isoformat(),
                                "coupon_dates": [d.isoformat() for d in p.bond.coupon_dates],
                                "baseline_price_per100": bond_price(p.bond, baseline, spec.valuation_date)}
        contracts.append(contract)
    results = []
    for record in definitions:
        validate_scenario(record)
        if record["mode"] == "conditional_projection" and conditional_adapter is None:
            results.append({"id": record["id"], "status": "deferred", "reason": "conditional_projection_pricing_integration_is_part6"})
            continue
        path = []
        try:
            extra = {}
            if record["mode"] == "conditional_projection":
                changes, extra = conditional_adapter(record, baseline, positions, risks)
            else:
                changes = scenario_changes(record, rows, baseline, spec)
            previous_pnl = 0.0
            for entry in changes:
                stressed = shocked(baseline, entry["changes"])
                value = value_state(positions, baseline, stressed, spec, foundation, risks)
                value.update(quarter=entry["quarter"], factor_changes=entry["changes"],
                             state_to_state_pnl_usd=value["pnl_usd"] - previous_pnl)
                previous_pnl = value["pnl_usd"]
                path.append(value)
            if not close(math.fsum(r["state_to_state_pnl_usd"] for r in path), path[-1]["pnl_usd"], foundation):
                raise DataError("State-to-state P&L failed reconciliation")
            results.append({"id": record["id"], "status": "priced", "mode": record["mode"], "version": record["version"],
                            "requested_origin": record["origin"], "effective_origin": record["origin"],
                            "requested_end": record.get("end", path[-1]["quarter"]), "effective_end": path[-1]["quarter"],
                            "horizon_quarters": record["horizon_quarters"], "path": path, **extra})
        except DataError as exc:
            results.append({"id": record["id"], "status": "unavailable", "reason": str(exc)})
    overlays = []
    for bump in spec.record["sensitivity"]["zero_proxy_overlays_bp"]:
        overlay = bump / 10000
        alternate_base = mark_portfolio(positions, baseline, spec.valuation_date, overlay)["total"]
        scenario_pnl = []
        for result in results:
            if result["status"] != "priced":
                continue
            end = result["path"][-1]
            state = MarketState(**end["market_state"])
            alternate_pnl = mark_portfolio(positions, state, spec.valuation_date, overlay)["total"] - alternate_base
            scenario_pnl.append({"scenario_id": result["id"], "alternate_pnl_usd": alternate_pnl,
                                 "pnl_difference_usd": alternate_pnl - end["pnl_usd"]})
        overlays.append({"continuous_zero_overlay_bp": bump, "baseline_value_usd": alternate_base,
                         "baseline_difference_usd": alternate_base - base["total"], "scenario_pnl": scenario_pnl})
    zero_check = value_state(positions, baseline, baseline, spec, foundation, risks)
    return {"schema_version": 1, "dataset_id": spec.record["dataset_id"], "use_scope": "local_internal_only",
            "portfolio": spec.record, "valuation_date": spec.valuation_date.isoformat(), "baseline_quarter": spec.baseline_quarter,
            "baseline_market": asdict(baseline), "baseline_macro": {k: v for k, v in origin.items() if k in ("gdp_growth_annualized_pct", "unemployment_pct", "unemployment_change_pp", "inflation_yoy_pct", "policy_rate_pct")},
            "baseline_value_usd": base["total"], "sizing_difference_usd": base["total"] - spec.total_usd,
            "holdings": contracts, "sensitivities": risks, "results": results,
            "approximation_diagnostics": diagnostics(positions, baseline, spec, risks),
            "zero_proxy_overlay_sensitivity": overlays,
            "zero_shock_identity_passed": zero_check["pnl_usd"] == 0 and all(zero_check["checks"].values()),
            "warnings": ["Fictional holdings, fractional unrounded quantities; fixed valuation time, cashflows and accrued interest",
                         "Quarterly-average baseline is not a simultaneous quote on the valuation date",
                         "Par yields are zero-rate proxies; compounding conversion is not curve bootstrapping",
                         "Flat extrapolation below2years and beyond10years affects unsupported maturity regions",
                         "GZ is a nonfinancial spread proxy applied to fictional corporate discounting, not IG-only OAS or default loss",
                         "Proxy-curve overlays quantify design sensitivity, not a calibrated error bound",
                         "Attribution allocates nonlinear interactions by order; isolated factor P&Ls need not add",
                         "No time passage, coupon receipts, dividends, maturity, reinvestment or rebalancing; P&L is a fixed-time diagnostic",
                         ("Conditional projections use frozen validation selections; no scenario probabilities or operational validation"
                          if conditional_adapter else "Conditional projection-to-pricing mapping remains Part6; no scenario probabilities or operational validation")]}


def save_valuation(data: Path, spec: PortfolioSpec, scenario_path: Path, foundation: FoundationConfig, output: Path) -> dict:
    rows, manifest = load_prepared(data)
    definitions = load_scenarios(scenario_path)
    try:
        result = price_scenarios(rows, spec, definitions, foundation)
        result.update(prepared_csv_sha256=manifest["csv_sha256"],
                      proxy_snapshot_id=manifest.get("proxy_snapshot_id"), fred_snapshot_id=manifest.get("fred_snapshot_id"),
                      fred_vintage_date=manifest.get("fred_vintage_date"), scenario_config_sha256=digest(scenario_path.read_bytes()),
                      portfolio_config_sha256=digest(canonical(spec.record)), python_version=platform.python_version(),
                      tolerances={"absolute_usd": foundation.absolute_usd, "relative": foundation.relative},
                      code_sha256=digest(canonical({name: digest(Path(__file__).with_name(name).read_bytes()) for name in ("pricing.py", "valuation.py", "scenarios.py", "models.py")})))
        content = canonical(result)
    except (OverflowError, ZeroDivisionError, ValueError) as exc:
        if isinstance(exc, DataError):
            raise
        raise DataError("Valuation exceeds its numerical domain") from None
    markdown = valuation_markdown(result).encode()
    output.mkdir(parents=True, exist_ok=False)
    (output / "valuation.json").write_bytes(content)
    (output / "valuation.md").write_bytes(markdown)
    (output / "manifest.json").write_bytes(canonical({"schema_version": 1, "valuation_sha256": digest(content), "markdown_sha256": digest(markdown)}))
    return {"status": "priced", "output": str(output.resolve()), "valuation_sha256": digest(content),
            "priced_scenarios": sum(r["status"] == "priced" for r in result["results"]),
            "deferred_scenarios": sum(r["status"] == "deferred" for r in result["results"]),
            "unavailable_scenarios": sum(r["status"] == "unavailable" for r in result["results"]),
            "priced_states": sum(len(r.get("path", [])) for r in result["results"]),
            "zero_shock_identity_passed": result["zero_shock_identity_passed"]}


def valuation_markdown(result: dict) -> str:
    lines = ["# Fictional portfolio fixed-time valuation", "", "Local internal use only; numeric outputs contain subscribed-data derivatives.", "",
             f"Valuation date {result['valuation_date']}; quarterly-average baseline {result['baseline_quarter']}. All holdings remain fixed.", "",
             f"Baseline dirty value USD {result['baseline_value_usd']:.2f}.", "",
             "| Scenario | Status | State | Cumulative P&L USD | State-to-state P&L USD | P&L % baseline |", "|---|---|---|---|---|---|"]
    for r in result["results"]:
        if r["status"] != "priced":
            lines.append(f"| {r['id']} | {r['status']} | unavailable | unavailable | unavailable | unavailable |")
        else:
            for state in r["path"]:
                lines.append(f"| {r['id']} | priced | {state['quarter']} | {state['pnl_usd']:.2f} | {state['state_to_state_pnl_usd']:.2f} | {state['pnl_percent_baseline']:.4f} |")
    lines += ["", "Position/asset/factor contributions, reversed-order attribution, isolated interaction residuals, sensitivity comparisons, contract cashflows and proxy-curve overlays are in valuation.json.", "",
              "## Bond approximation comparisons", "", "Parallel first-order results are duration-only; nonparallel results use key-rate derivatives. Quadratic results include rate cross terms.", "",
              "| Position | Kind | Short bp | Long bp | Full P&L USD | First order USD | Quadratic USD | Quadratic error USD |", "|---|---|---|---|---|---|---|---|"]
    for row in result["approximation_diagnostics"]:
        lines.append(f"| {row['position']} | {row['shift_kind']} | {row['short_bp']} | {row['long_bp']} | {row['full_pnl_usd']:.2f} | {row['duration_or_keyrate_pnl_usd']:.2f} | {row['quadratic_pnl_usd']:.2f} | {row['second_order_error_usd']:.2f} |")
    lines += ["", "## Limitations", ""] + [f"- {w}" for w in result["warnings"]]
    return "\n".join(lines) + "\n"
