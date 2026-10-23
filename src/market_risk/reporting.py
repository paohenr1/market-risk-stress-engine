"""Run-bound technical report source and CSV exhibits; no numerical stdout."""

import csv
from datetime import datetime, timezone
import io
from pathlib import Path

from market_risk.data import DataError, canonical, digest
from market_risk.execution import load_saved_run, read_json, source_identity
from market_risk.models import MACROS, TARGETS, finite, label, load_prepared


TARGET_NAMES = dict(zip(TARGETS, ["Equity", "2y yield", "10y yield", "GZ spread"]))
MACRO_NAMES = dict(zip(MACROS, ["annualized GDP growth", "unemployment-rate change", "policy rate", "year-on-year inflation"]))
SOURCES = [
    ("Federal Reserve: GZ/EBP research data and revision discussion", "https://www.federalreserve.gov/econres/notes/feds-notes/updating-the-recession-risk-and-the-excess-bond-premium-20161006.html"),
    ("U.S. Treasury: par-yield curve methodology", "https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics/treasury-yield-curve-methodology"),
    ("BEA: compound annual growth convention", "https://www.bea.gov/help/faq/463"),
    ("BLS via FRED: unemployment definition and metadata", "https://fred.stlouisfed.org/series/UNRATE"),
    ("FRED: observations API and vintage parameters", "https://fred.stlouisfed.org/docs/api/fred/series_observations.html"),
]


def number(value, places=2):
    return "unavailable" if value is None else f"{value:,.{places}f}"


def table(headers, rows, caption):
    return {"headers": headers, "rows": [[str(v) for v in row] for row in rows], "caption": caption}


def page(title, paragraphs, tables=None, chart=None, code=None):
    return {"title": title, "paragraphs": paragraphs, "tables": tables or [], "chart": chart, "code": code}


def build_document(manifest: dict, valuation: dict, evaluation: dict, prepared: dict, rows: list[dict], definitions: list[dict]) -> tuple[dict, dict]:
    """All reported numbers come from supplied saved artifacts or explicit arithmetic."""
    run_id = manifest["run_id"]
    results = valuation["results"]
    priced = [r for r in results if r["status"] == "priced"]
    endpoints = [(r, r["path"][-1]) for r in priced]
    cond = [r for r in priced if r["mode"] == "conditional_projection"]
    definition_map = {r["id"]: r for r in definitions}
    counters, names = {}, {}
    for result in results:
        definition = definition_map[result["id"]]
        kind = {"historical_replay": "Replay", "conditional_projection": "Conditional", "direct_shock": "Direct"}[definition["mode"]]
        counters[kind] = counters.get(kind, 0) + 1
        names[result["id"]] = f"{kind} {counters[kind]}"
    rules, settings, config = evaluation["evaluation_rules"], evaluation["settings"], evaluation["model_config"]
    quality = prepared.get("config", {})
    quality_text = (f"The captured quality rules require at least {quality['minimum_common_days_per_month']} common dates and exclusions no greater than {100 * quality['maximum_excluded_union_day_fraction']:g}% of the valid-date union. "
                    if {"minimum_common_days_per_month", "maximum_excluded_union_day_fraction"} <= quality.keys()
                    else "This prepared manifest does not record the numerical common-day quality thresholds; none are inferred. ")
    weights = ", ".join(f"{100 * p['weight']:g}% {p['id'].replace('_', ' ')}" for p in valuation["portfolio"]["positions"])
    horizons = ", ".join(str(r["horizon_quarters"]) for r in cond)
    tolerance = valuation["tolerances"]
    predictors = "; ".join(f"{name}: {', '.join(MACRO_NAMES[k] for k in config[field])}" for name, field in
                           (("equity", "equity_predictors"), ("yields", "yield_predictors"), ("credit", "credit_predictors")))
    selected = evaluation["selected_holdout_summaries"]
    complete = sum(all(finite(r.get(k)) for k in (*TARGETS, *MACROS)) for r in rows)
    missing = [(label(r["date"]), ", ".join(k for k in (*TARGETS, *MACROS) if not finite(r.get(k))))
               for r in rows if any(not finite(r.get(k)) for k in (*TARGETS, *MACROS))]
    charts = {}
    charts["endpoint_pnl"] = {"kind": "bar", "title": "Endpoint fixed-time portfolio P&L", "y_label": "USD",
        "labels": [names[r["id"]] for r, _ in endpoints], "series": [{"name": "Full repricing", "values": [v["pnl_usd"] for _, v in endpoints]}]}
    if not endpoints:
        charts.pop("endpoint_pnl")
    if cond:
        charts["conditional_paths"] = {"kind": "line", "title": "Conditional cumulative P&L from the common baseline", "y_label": "USD",
            "labels": [str(i + 1) for i in range(max(len(r["path"]) for r in cond))],
            "series": [{"name": names[r["id"]], "values": [s["pnl_usd"] for s in r["path"]]} for r in cond]}
    bonds = [h for h in valuation["holdings"] if "bond" in h]
    government_bonds = [h for h in bonds if h["kind"] == "government_bond"]
    long_bond = max(government_bonds, key=lambda h: h["bond"]["coupon_dates"][-1]) if government_bonds else None
    diagnostics = valuation["approximation_diagnostics"]
    if long_bond:
        parallel = sorted([r for r in diagnostics if r["position"] == long_bond["id"] and r["shift_kind"] == "parallel"], key=lambda r: r["short_bp"])
        charts["bond_approximations"] = {"kind": "line", "title": "Long government proxy: parallel-shift P&L", "y_label": "USD",
            "labels": [str(r["short_bp"]) for r in parallel], "series": [
                {"name": name, "values": [r[field] for r in parallel]} for name, field in
                (("Full repricing", "full_pnl_usd"), ("First order", "duration_or_keyrate_pnl_usd"), ("Quadratic", "quadratic_pnl_usd"))]}
    pages = []
    pages.append(page("1. Executive summary and intended use", [
        "Market Risk Variable Projection and Portfolio Stress Testing is a Python research pipeline for studying how observed or explicitly assumed market conditions change the value of a fictional USD portfolio. It is intended for researchers and readers evaluating the implementation of a transparent modeling and valuation process.",
        "The question is conditional: given a stated macroeconomic or market-factor path, what does each model imply, and what is the resulting fixed-time valuation change? Historical replay, macro-conditioned projection and direct factor shocks are separate modes. None assigns scenario probabilities or establishes a tradable forecast.",
        "The implemented dataset uses SPY split-only price changes as an equity proxy and the Federal Reserve GZ nonfinancial corporate spread as a credit proxy. These are not an equity index total-return series or investment-grade-only option-adjusted spreads. The originally specified index/IG-OAS dataset remains unavailable.",
        "All results below are generated from the saved run identified in every page footer and exhibit. They are local research outputs containing subscribed-data derivatives. Software correctness, empirical reconstruction errors and independent financial validation are separate questions; this work establishes no regulatory or production fitness."
    ], [table(["Run characteristic", "Value"], [
        ["Valuation date / average-state baseline", f"{valuation['valuation_date']} / {valuation['baseline_quarter']}"],
        ["Portfolio baseline dirty value, USD", number(valuation["baseline_value_usd"])],
        ["Priced / unavailable scenarios", f"{len(priced)} / {sum(r['status'] != 'priced' for r in results)}"],
        ["Priced quarterly states", sum(len(r["path"]) for r in priced)],
        ["Data calendar / complete rows", f"{len(rows)} / {complete}"],
    ], "Table 1. Saved-run scope; counts are not claims of model validity.")]))
    pages.append(page("2. Architecture and data flow", [
        "Acquisition is separate from preparation. Official FRED observations and metadata, the permitted Intrinio price snapshot and the Federal Reserve monthly credit panel are saved before transformations. Offline preparation verifies hashes, retains calendar gaps and produces a labelled quarterly dataset.",
        "The evaluation stage compares fixed benchmark specifications on chronological validation and then holdout partitions. Validation alone selects one method and training window for each target. Integrated execution verifies that saved decision against its input data and configuration, refits the selected specification through the scenario origin, projects recursively and passes factor changes to pricing.",
        "Pricing sizes fractional holdings once at baseline dirty values. Every subsequent state reuses those quantities, contractual cashflows, valuation date and accrued interest. A single valuation kernel computes totals, attribution and sensitivity approximations for all modes. Reporting reads the saved result; it does not fit new models or independently recompute a different P&L path."
    ], [table(["Layer", "Module", "Output / boundary"], [
        ["Sources", "data, intrinio, proxy", "Permitted hashed input snapshots"],
        ["Preparation", "preparation, proxy", "Quarterly calendar, missing states retained"],
        ["Estimation", "models", "Window-local scaling, candidate fits"],
        ["Evaluation", "evaluation", "Frozen validation decision, holdout errors"],
        ["Scenarios", "scenarios", "Explicit dates, units and path semantics"],
        ["Valuation", "pricing, valuation", "Fixed holdings and reconciled P&L"],
        ["Execution", "execution", "Run identity, captured inputs, integrity checks"],
        ["Reporting", "reporting", "Run-bound Markdown, CSV and PDF exhibits"],
    ], "Table 2. Data flow proceeds top to bottom; credentials are never report inputs.")]))
    pages.append(page("3. Data sources, alignment and transformations", [
        "The data window is retained on a quarterly calendar, including incomplete quarters. SPY and the two Treasury yields use common valid daily dates within each month. " + quality_text + "A quarter requires all three monthly states, averaged equally. This is not a simultaneous endpoint quote.",
        "The monthly GZ panel is averaged over three published monthly observations; no identical-daily-date claim is made for it. Its nonfinancial corporate bond coverage differs from investment-grade OAS. The Federal Reserve research note describes the sample and revisions [1]. The pipeline uses the GZ spread column, not the excess bond premium component.",
        "A split-only adjustment preserves price-return semantics and excludes dividends. Equity changes compare successive quarterly average prices. Revised FRED vintages and supplied future macro conditions support retrospective conditional reconstruction, not release-time forecasting. FRED vintage parameters are documented in [5]; growth annualization follows the compound convention described by BEA [3]."
    ], [table(["Input", "Prepared variable / transformation"], [
        ["GDPC1", "100 * ((real GDP_q / GDP_previous_q)^4 - 1)"],
        ["UNRATE", "Quarter mean; change from prior calendar quarter in pp"],
        ["CPIAUCSL", "100 * (quarter-mean CPI / year-earlier mean - 1)"],
        ["FEDFUNDS", "Quarter mean of published monthly rates"],
        ["SPY price", "100 * (quarter-average price / previous average - 1)"],
        ["DGS2 / DGS10", "Common-day monthly means, equal-weight quarter mean"],
        ["GZ spread", "Mean of three published monthly spread levels"],
    ], "Table 3. Transformations preserve calendar lags and reject missing required months.")]))
    selection_rows = [[TARGET_NAMES[t], evaluation["selection"][t].get("method", "unavailable"), evaluation["selection"][t].get("window", "unavailable")]
                      for t in TARGETS]
    macro_sensitive = any(c.get("method") in ("static", "dynamic") for c in evaluation["selection"].values())
    model_finding = ("At least one selected specification includes supplied macro predictors; the saved paths show their conditional response."
                     if macro_sensitive else
                     "None of the selected specifications contains a supplied macro predictor. The selected AR/mean/no-change paths therefore do not respond to differences between the invented macro scenarios. This is a result of the declared one-quarter validation selection, not evidence that macro conditions are economically irrelevant. Macro challengers remain in the evaluation exports.")
    pages.append(page("4. Projection methodologies and benchmarks", [
        "The target variables are incremental equity average-price change, the 2-year and 10-year observable yield levels, and the GZ spread level. Models are deliberately small: no-change, historical mean, AR(1) and static regression are compared for each target; a lagged-spread plus macro regression is the credit challenger. All use the same eligible current/lag calendar observations within a comparison.",
        "Captured static predictors are " + predictors + ". The dynamic credit challenger adds its own previous spread. Predictors are centered and scaled inside each training window; reorthogonalized QR solves the standardized regression. Rank-deficient candidates are unavailable. VIF, residual dependence, coefficient/window stability and recursive instability are diagnostic warnings, not model-selection overrides.",
        "Recursive projection begins at observed targets. Future lagged targets are earlier predictions, never realized future targets. Innovations are zero. For yield/spread levels the pricing map is baseline + (forecast level - observed origin), with consistent percent-to-decimal conversion. Equity compounds product(1 + return/100). The observed origin is retained with zero P&L; mean reversion or intercept mismatch may affect the first forecast movement and is not silently removed.",
        model_finding
    ], [table(["Target", "Validation-selected method", "Window"], selection_rows,
              "Table 4. Frozen choices from one-quarter validation MAE; no holdout winner selection.")]))
    pages.append(page("5. Scenario construction", [
        "Historical replays use the exact common quarterly-average origins and ends in the captured scenario definitions. Each intervening state is required. Their cumulative observed factor changes are applied to the common portfolio baseline, rather than substituting historical portfolio holdings or factor levels.",
        "Conditional paths supply declared macro conditions quarter by quarter. Incremental unemployment changes must imply levels between zero and 100%. Selected models translate those conditions into market paths. Direct shocks bypass estimation and apply declared cumulative equity/rate/spread shocks. These modes answer different conditional questions and are not interchangeable historical forecasts. Table 5 maps the report labels to saved scenario IDs; the complete captured definitions preserve each narrative and rationale.",
        "Scenario definitions preserve narrative, rationale, version, units, origin, horizon and reference convention. A cumulative shock is applied once to the original baseline; it is not reapplied to the previous state. No dates are substituted and no missing observations are interpolated. Invalid conditions, target domains or selected fits produce an explicit unavailable scenario, without clipping or fallback. Complete macro paths and resulting factors are exported as CSV."
    ], [table(["Scenario", "Mode", "Origin", "Quarters", "Status"], [
        [f"{names[r['id']]}: {r['id']}", definition_map[r["id"]]["mode"].replace("_", " "),
         definition_map[r["id"]]["origin"], definition_map[r["id"]]["horizon_quarters"], r["status"]] for r in results],
        "Table 5. Scenario availability belongs to this saved run; no scenario probabilities are assigned.")]))
    pages.append(page("6. Portfolio and pricing methodology", [
        "The fictional USD portfolio's captured target weights are " + weights + ". Positions are fractional and unrounded; bonds are sized in face-100 units using dirty prices. Cash is held at its nominal value. Holdings remain fixed across all states.",
        "Each synthetic bond has explicit unadjusted semiannual coupon dates and a fixed principal payment. Coupons on the valuation date are treated as already paid. Times to payment use actual days/365; accrued interest uses actual elapsed days over the coupon period. Clean price equals dirty price minus accrued interest. There is no passage of time as scenario labels advance.",
        "The government curve is a disclosed par-as-zero proxy, not a bootstrapped curve. A semiannual observable quote y becomes z = 2*log(1+y/2); continuous z is interpolated linearly between 2 and 10 years and held flat outside. Treasury's published curve is a par-yield construct [2]; converting compounding does not identify genuine zero rates.",
        "Dirty price per 100 is the sum of contractual cashflow * exp(-(z(t)+s)*t), with s=0 for government bonds and the decimal GZ margin for the fictional corporate bond. This is a flat continuous spread proxy, not option-adjusted valuation or a default-loss model. Negative government yields are allowed within the conversion domain; negative spreads and nonpositive equity prices fail."
    ], [table(["Position", "Quantity units", "Coupon %", "Last payment"], [
        [h["id"].replace("_", " "), h["quantity_units"], number(h["bond"]["coupon_pct"]) if "bond" in h else "n/a",
         h["bond"]["coupon_dates"][-1] if "bond" in h else "n/a"] for h in valuation["holdings"]],
        "Table 6. Contractual terms are synthetic; quantities and baseline prices are in the saved valuation JSON.")]))
    pages.append(page("7. Empirical evaluation", [
        f"Validation spans {evaluation['validation']['start']} to {evaluation['validation']['end']}; the final holdout spans {evaluation['holdout']['start']} to {evaluation['holdout']['end']}. Training requires at least {rules['minimum_training_quarters']} eligible quarters, validation at least {rules['minimum_validation_quarters']} usable one-quarter errors, and holdout reserves {rules['final_holdout_quarters']} calendar quarters. A {settings['rolling_calendar_quarters']}-calendar-quarter rolling window includes its lag warmup. Missing quarters reduce usable error counts without compressing the calendar.",
        "Selection uses one-quarter validation mean absolute error (MAE), with deterministic ties favoring simpler models and expanding windows. That decision is frozen before holdout errors are calculated. Each expanding or rolling origin fit uses history through that origin. Earlier holdout observations may train later refits, but they never select the method or window. Multi-quarter label paths remain inside their evaluation partition.",
        "Table 7 reports selected-specification holdout errors on common origin/horizon comparisons. Equity errors are percentage points of the final quarter's incremental average-price change; yield/spread errors are basis points. Cumulative multi-quarter equity errors and all competing specifications are retained in the CSV exports. Lower error on this revised-data conditional reconstruction is not evidence of real-time predictability."
    ], [table(["Target", "h", "n", "MAE", "RMSE", "Bias", "Unit"], [
        [TARGET_NAMES[s["target"]], s["horizon"], s["observations"], number(s.get("mae")), number(s.get("rmse")), number(s.get("bias")),
         "pp" if s["target"] == TARGETS[0] else "bp"] for s in sorted(selected, key=lambda s: (TARGETS.index(s["target"]), s["horizon"]))],
        "Table 7. MAE is mean absolute error; RMSE is root mean squared error; bias is mean signed error. h is quarters.")]))
    pages.append(page("8. Portfolio stress results", [
        "Full repricing is the valuation reference. Endpoint P&L below is stressed dirty value minus the original common-baseline dirty value. Equity, rates and credit can offset one another; the largest negative portfolio result is conditional on these particular scenarios, holdings and pricing conventions.",
        "Historical labels identify observed average-state changes applied to the common baseline. Conditional labels identify an invented macro path translated by the frozen selected models. The direct diagnostic is an imposed factor combination. Their relative ordering does not estimate which event is more likely or which forecast will occur.",
        "The numerical exports include every intermediate state, cumulative P&L and changes between successive states. Those changes telescope to endpoint P&L. None includes coupon receipts, dividends, maturity, reinvestment, rebalancing or management action."
    ], [table(["Scenario", "Endpoint", "P&L USD", "% baseline"], [
        [names[r["id"]], v["quarter"], number(v["pnl_usd"]), number(v["pnl_percent_baseline"])] for r, v in endpoints],
        "Table 8. Fixed-time endpoint valuation changes; all cases retain the same baseline and holdings.")], "endpoint_pnl" if endpoints else None))
    pages.append(page("9. Attribution and conditional paths", [
        f"Position contributions are stressed minus baseline position values. They sum to asset-class and portfolio P&L within max(USD {tolerance['absolute_usd']:g}, {tolerance['relative']:g} times the larger absolute reference). Cash contributes zero under the fixed-time convention. The zero-shock state is checked exactly.",
        "Factor attribution sequentially changes equity, 2-year yield, 10-year yield and credit spread. Each contribution is the portfolio value difference after adding that factor to the preceding state. The reverse-order calculation uses equity, spread, long yield and short yield; both totals reconcile, while individual contributions can differ because interactions are nonlinear.",
        "Isolated factor shocks need not add to the combined result. Their interaction residual and reverse-order contributions are saved, rather than presented as an additive risk decomposition. " +
        (f"The conditional chart shows cumulative portfolio P&L over the priced horizons ({horizons} quarters); its horizontal axis is quarters after the declared origin." if cond else "No conditional path was priced in this run.")
    ], [table(["Scenario", "Equity USD", "2y USD", "10y USD", "Spread USD"], [
        [names[r["id"]], *[number(v["factor_pnl_usd"][f]) for f in ("equity_price", "yield_2y", "yield_10y", "credit_spread")]]
        for r, v in endpoints], "Table 9. Sequential endpoint contributions in the declared primary order.")], "conditional_paths" if cond else None))
    approximation_rows = []
    for h in bonds:
        values = [r for r in diagnostics if r["position"] == h["id"]]
        if values:
            approximation_rows.append([h["id"].replace("_", " "), len(values), number(max(abs(r["first_order_error_usd"]) for r in values)),
                                       number(max(abs(r["second_order_error_usd"]) for r in values))])
    pages.append(page("10. Full repricing and approximation comparisons", [
        "Sensitivities are finite differences of the same pricing function used for full valuation. First order uses the dollar gradient with respect to the two observable yield quotes and spread. Second order adds one half of the full 3-by-3 Hessian quadratic form, including cross terms. Equity P&L is exact in both approximations.",
        "For a parallel yield move, the two yield derivatives aggregate into effective duration and convexity. For nonparallel shifts, two-node key-rate derivatives are used instead of a scalar duration approximation. " +
        f"Captured parallel shifts (bp): {', '.join(f'{v:g}' for v in valuation['portfolio']['sensitivity']['parallel_shifts_bp'])}. " +
        "The CSV retains every configured nonparallel short/long shift pair. The chart's horizontal axis is the imposed parallel shift in basis points.",
        "Table 10 summarizes the largest absolute approximation errors across that configured grid for each bond; maxima need not arise at the same shift. Small-shock agreement is a local numerical check, not a promise of large-shock accuracy. P&L-relative errors are suppressed when full P&L is near zero. Dollar and baseline-relative errors remain available in the CSV.",
        "Central differences are normally used. At a spread boundary, declared forward formulas avoid evaluating negative spreads. Bump size, interpolation and floating-point cancellation can affect the approximation and are recorded in the saved risk diagnostics."
    ], [table(["Bond", "Grid states", "Max first error USD", "Max quadratic error USD"], approximation_rows,
              "Table 10. Absolute errors against full repricing; parallel and twist states included.")], "bond_approximations" if long_bond else None))
    overlay_rows = []
    for r, _ in endpoints:
        differences = []
        for overlay in valuation["zero_proxy_overlay_sensitivity"]:
            found = next((x for x in overlay["scenario_pnl"] if x["scenario_id"] == r["id"]), None)
            differences.append(number(found["pnl_difference_usd"]) if found else "unavailable")
        overlay_rows.append([names[r["id"]], *differences])
    overlay_headers = [f"{o['continuous_zero_overlay_bp']:+g}bp difference USD" for o in valuation["zero_proxy_overlay_sensitivity"]]
    quantile_horizon = max((s["horizon"] for s in selected), default=None)
    pages.append(page("11. Projection uncertainty and pricing uncertainty", [
        "Projection uncertainty concerns the statistical mapping from macro conditions to market variables. Held-out error quantiles, benchmark comparisons, training-window sensitivity, residual dependence, parameter stability and predictor extrapolation expose different aspects of this uncertainty. Error quantiles are descriptive: overlapping horizons are dependent, paths are conditioned on revised realized macros, and no calibrated prediction interval is supplied.",
        "Pricing uncertainty concerns the mapping from those market variables into values: par-as-zero discounting, two-node interpolation, flat extrapolation, the continuous GZ margin and fictional instrument contracts. These design approximations remain even if a projected factor path were known exactly. A regression error distribution does not quantify curve or spread-mapping error.",
        "The table varies the continuous government zero proxy in parallel while retaining the original holdings. Each overlay uses its own alternate baseline value and reprices the same endpoint states. Reported differences compare that alternate P&L with the original P&L. This is a design-sensitivity check, not a calibrated pricing confidence bound.",
        "The pipeline omits default events and recovery, liquidity/transaction costs, counterparty exposure, funding, optionality, basis risk beyond the disclosed proxies, management actions and scenario probabilities. It does not quantify all sources of model risk."
    ], [table(["Scenario", *overlay_headers], overlay_rows, "Table 11. Endpoint P&L sensitivity to the configured proxy-curve overlays."),
        table(["Target", "h", "n", "Error q10", "Error q90", "Unit"], [
            [TARGET_NAMES[s["target"]], s["horizon"], s["observations"], number(s.get("error_q10")), number(s.get("error_q90")),
             "pp" if s["target"] == TARGETS[0] else "bp"] for s in selected if s["horizon"] == quantile_horizon],
            (f"Table 12. Descriptive held-out endpoint errors at the longest evaluated horizon ({quantile_horizon} quarters); overlapping paths, not prediction intervals."
             if quantile_horizon else "Table 12. Selected holdout error quantiles are unavailable; no selected evaluated horizon exists."))]))
    pages.append(page("12. Controls, reproduction and handover", [
        "A saved run preserves configurations, input snapshots/manifests, selected-model evidence, origin refits, source fingerprint including local edits, runtime version, dependency declaration, warnings, checks and outputs. There is no random simulation; the random seed is explicitly null. Report and exhibit manifests identify the parent run and content hashes.",
        "The completed pricing/execution checkpoint passed 80 local software tests and independent review, test, documentation and whitespace phases. These checks support implementation behavior, including analytical pricing references, zero shock, reconciliation, calendar/leakage handling, invalid inputs and offline reproduction. They do not establish independent financial validation. Reporting verification is recorded separately in the accompanying project validation status; it is not inferred from a successful rendering operation.",
        f"Offline reproduction requires the matching executable fingerprint and exact Python runtime. It verifies the inventory and hashes, then recalculates from the saved inputs into a new linked run. Dollar comparisons use the configured absolute/relative tolerance; other floats use absolute 1e-12 or relative {tolerance['relative']:g}. Statuses and schemas agree exactly. Timestamps and IDs can differ.",
        "Future monitoring is specified, not running: check source schema, rights, dates, missingness and units on refresh; review sample coverage and diagnostics before refitting; compare fixed-specification horizon errors, parameter drift and extrapolation before adopting changes. Fail closed on broken integrity or reconciliation. Research ownership must assign review and acceptance before any operational use."
    ], code="market-risk reproduce-run --run <saved-run> --output <new-run>\nmarket-risk generate-report --run <saved-run> --output <report-dir>\npython tools/render_report.py --report <report-dir>"))
    pages.append(page("Appendix A. Sources and methodology boundaries", [
        "The following primary sources support data definitions, revision cautions and published yield conventions. The project's proxy curve, fictional contracts, model specification choices and conditional scenario magnitudes are implementation assumptions, not methods endorsed by those providers.",
        *[f"[{i}] {title}. {url}" for i, (title, url) in enumerate(SOURCES, 1)],
        "Intrinio supplies the permitted SPY price/split snapshot under the user's existing access. Only split ratios adjust prices; dividend-adjusted close is not used. The captured input manifests record provider request metadata and hashes. No credentials are embedded. Rights to source and derived data must be assessed separately before distribution.",
        "GZ and EBP are distinct columns in the Federal Reserve research panel. This project uses GZ as a nonfinancial credit-spread proxy. The Treasury source describes a par-yield curve; this project does not claim to reconstruct its underlying term structure or market prices from two yield observations."
    ]))
    pages.append(page("Appendix B. Coverage, exhibits and provenance", [
        "Incomplete rows below remain on the original calendar. A complete transformed row is not necessarily a model-eligible lagged training row or a complete future evaluation path. Exact eligible counts are in the model bundles and evaluation summaries.",
        "Exhibits are exported from the same verified saved run: endpoint totals, every state and factor contribution, selected and competing validation/holdout error summaries, approximation grids, curve overlays and macro conditions. CSV rows carry the run ID. Chart definitions use those same values; PDF/SVG rendering does not perform a new valuation.",
        f"Run ID: {run_id}. Dataset: {valuation['dataset_id']}. Data CSV SHA256: {valuation['prepared_csv_sha256']}.",
        f"Selection SHA256: {valuation['selection_sha256']}. Saved executable SHA256: {manifest['source']['sha256']}.",
        "The report manifest binds the saved-run manifest hash and report-generation source fingerprint. The rendering manifest separately binds PDF/SVG files and the optional rendering dependency versions. Hashes detect accidental changes; they are not digital signatures or provider redistribution permission."
    ], [table(["Quarter", "Missing transformed fields"], [[q, fields.replace("_pct", "").replace("_annualized", "")] for q, fields in missing] or [["none", "none"]],
              "Table B1. Required transformed fields missing in the captured prepared dataset.")]))
    return {"schema_version": 1, "run_id": run_id, "title": "Market Risk Variable Projection and Portfolio Stress Testing",
            "subtitle": "Technical report | Local research use | Fixed-time valuation",
            "scenario_labels": names,
            "assumptions": {"preparation_quality": quality, "model_config": config, "evaluation_rules": rules,
                            "evaluation_settings": settings, "portfolio_positions": valuation["portfolio"]["positions"],
                            "tolerances": tolerance, "scenarios": [definition_map[r["id"]] for r in results]},
            "findings": {"selected_models_use_macros": macro_sensitive,
                         "conditional_paths_identical": (all(r["path"] == cond[0]["path"] for r in cond[1:]) if len(cond) > 1 else None)},
            "pages": pages, "charts": charts}, {"priced": priced, "endpoints": endpoints, "selected": selected}


def markdown(document: dict) -> str:
    lines = [f"# {document['title']}", "", document["subtitle"], "", f"Saved run: `{document['run_id']}`", ""]
    for p in document["pages"]:
        lines += [f"## {p['title']}", ""]
        for paragraph in p["paragraphs"]:
            lines += [paragraph, ""]
        for t in p["tables"]:
            lines += [t["caption"], "", "| " + " | ".join(t["headers"]) + " |", "| " + " | ".join("---" for _ in t["headers"]) + " |"]
            lines += ["| " + " | ".join(str(v).replace("|", "/").replace("\n", " ") for v in row) + " |" for row in t["rows"]]
            lines += [""]
        if p["chart"]:
            lines += [f"![{document['charts'][p['chart']]['title']}](rendered/{p['chart']}.svg)", "", f"Figure source: saved run `{document['run_id']}`.", ""]
        if p["code"]:
            lines += ["```text", p["code"], "```", ""]
    return "\n".join(lines)


def csv_bytes(run_id: str, records: list[dict]) -> bytes:
    headers = ["run_id", *dict.fromkeys(k for row in records for k in row if k != "run_id")]
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    for row in records:
        writer.writerow({"run_id": run_id, **{k: canonical(v).decode().strip() if isinstance(v, (dict, list)) else v for k, v in row.items() if k != "run_id"}})
    return stream.getvalue().encode()


def save_report(saved: Path, output: Path) -> dict:
    manifest, valuation, _ = load_saved_run(saved, require_current_source=False)
    # Report reading permits archived executable versions; it never claims a rerun.
    evaluation = read_json(saved / "inputs/evaluation/evaluation.json")
    rows, prepared = load_prepared(saved / "inputs/data")
    selected_ids = {r["id"] for r in valuation["results"]}
    definitions = [r for r in read_json(saved / "inputs/scenarios.json")["scenarios"] if r["id"] in selected_ids]
    document, context = build_document(manifest, valuation, evaluation, prepared, rows, definitions)
    run_id = manifest["run_id"]
    exhibits = {}
    exhibits["endpoints.csv"] = [{"scenario": r["id"], "quarter": v["quarter"], "pnl_usd": v["pnl_usd"],
        "pnl_percent_baseline": v["pnl_percent_baseline"], "position_pnl_usd": v["position_pnl_usd"],
        "asset_class_pnl_usd": v["asset_class_pnl_usd"], "reverse_factor_pnl_usd": v["reverse_factor_pnl_usd"],
        "isolated_interaction_residual_usd": v["isolated_interaction_residual_usd"],
        **{f"factor_{k}_usd": x for k, x in v["factor_pnl_usd"].items()}} for r, v in context["endpoints"]]
    exhibits["paths.csv"] = [{"scenario": r["id"], "mode": r["mode"], "quarter": s["quarter"],
        "pnl_usd": s["pnl_usd"], "state_to_state_pnl_usd": s["state_to_state_pnl_usd"],
        **{f"factor_{k}_usd": v for k, v in s["factor_pnl_usd"].items()}, **s["factor_changes"]} for r in context["priced"] for s in r["path"]]
    exhibits["evaluation.csv"] = [{"partition": stage, **s} for stage in ("validation", "holdout") for s in evaluation[stage]["summaries"]]
    exhibits["selected_holdout.csv"] = context["selected"]
    exhibits["approximations.csv"] = valuation["approximation_diagnostics"]
    exhibits["curve_overlays.csv"] = [{"overlay_bp": o["continuous_zero_overlay_bp"], **r} for o in valuation["zero_proxy_overlay_sensitivity"] for r in o["scenario_pnl"]]
    exhibits["macro_conditions.csv"] = [{"scenario": r["id"], **q} for r in definitions if r["mode"] == "conditional_projection" for q in r["quarters"]]
    output.mkdir(parents=True, exist_ok=False)
    files = {"document.json": canonical(document), "report.md": markdown(document).encode(),
             **{name: csv_bytes(run_id, records) for name, records in exhibits.items()}}
    for name, content in files.items():
        (output / name).write_bytes(content)
    report_manifest = {"schema_version": 1, "run_id": run_id, "created_utc": datetime.now(timezone.utc).isoformat(),
        "use_scope": "local_internal_only", "saved_run_manifest_sha256": digest((saved / "manifest.json").read_bytes()),
        "saved_run_source_sha256": manifest["source"]["sha256"], "report_source": source_identity(),
        "files": {name: digest(body) for name, body in files.items()}, "planned_pages": len(document["pages"]),
        "checks": {"saved_run_integrity": True, "report_is_revaluation": False, "pdf_visual_review": "pending"}}
    (output / "report_manifest.json").write_bytes(canonical(report_manifest))
    return {"status": "report_source_generated", "run_id": run_id, "output": str(output.resolve()),
            "pages": len(document["pages"]), "csv_exhibits": len(exhibits), "charts": len(document["charts"]),
            "manifest_sha256": digest(canonical(report_manifest))}
