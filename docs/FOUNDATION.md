# Foundation decisions and contracts

These conventions define the implemented research pipeline. The linked method guides explain the calculations and their limitations.

## Runtime and organization

Use Python 3.11+ with a `src` package, TOML assumptions, dataclass records, an argparse CLI, and standard-library unittest. The verified local runtime is Python 3.14.8. The calculation engine uses no third-party runtime libraries. Keep data preparation, financial calculations, validation, and report export separate.

## Timing, baseline, and units

- Portfolio currency: USD; initial target market value: USD 1,000,000; weights as specified.
- Baseline valuation date: 2026-06-30, with the proxy baseline interpreted under the [average-state conventions](PROXY_DATA.md#alignment-and-missing-observations). [Pricing and replay](PRICING_AND_REPLAY.md) implement the fictional portfolio baseline and fixed-time diagnostics. The baseline macro state is identified by quarter and vintage; contemporaneous revised values must not be claimed available on the valuation date.
- Equity price: normalize the declared baseline equity state to a fictional unit price of USD 100 and size exposure from this value. Normalization is not a fabricated historical observation. Use simple price returns with no dividends.
- Fixed-time valuation: valuation date and remaining cash flows do not advance as scenario labels move through quarters. Cash stays at its baseline nominal value. Projected levels are scenario states, not dated realized total returns.
- User equity shocks: percentage values (e.g., -20 means -20%); yield/spread shocks: basis points (100 bp = 1 percentage point = 0.01 in decimal rates).
- Model rates: percentage units with diagnostics in basis points; convert exactly once at the pricing boundary. GDP and inflation use percentage growth units; unemployment changes use percentage points.
- Cumulative P&L: value at state q minus baseline value. State-to-state P&L: value at q minus value at q-1. Preserve both.

Quarterly equity price levels compound as `P_q = P_0 * product(1 + r_j/100)` for quarterly returns expressed in percent. An incremental -20% followed by +10% produces 88 from 100. A cumulative +10% second state instead produces 110. Inputs must explicitly distinguish those cases.

## Data definitions

The table below records the original index/IG-OAS target and six-series FRED conventions. The executable v1 proxy market definitions and alignment are owned by [PROXY_DATA.md](PROXY_DATA.md); they supersede the market-factor rows for that track.

| Factor | Proposed definition | Quarterly rule |
|---|---|---|
| Real GDP growth | `100 * ((GDPC1_q / GDPC1_(q-1))^4 - 1)` | Native quarterly real GDP level; annualized quarter-over-quarter growth; the level's SAAR scaling cancels in the ratio |
| Unemployment | UNRATE level in percent; changes in percentage points | Mean of all three monthly values; reject incomplete quarters |
| Inflation | `100 * (CPI_quarter / CPI_quarter_minus_4 - 1)` | CPI quarter = mean of three monthly CPIAUCSL levels; year-over-year growth |
| Policy rate | FEDFUNDS in percent | Arithmetic mean of three monthly values, described as mean of monthly daily averages |
| 2y/10y yields | DGS2/DGS10 levels in percent | Last common valid observed market date on or before quarter-end, within five calendar days |
| Credit spread | Intended aggregate investment-grade spread in percent | Same effective market date as other replay factors; unavailable in the original target dataset |
| Equity | Broad US price index; simple return in percent | Quarter-end common effective market observations where available; long-history inputs unavailable in the original target dataset |

Never forward-fill across quarters, backfill, infer a missing historical value, or fill an endpoint from a different date for each factor. Enforce complete monthly quarters. Preserve the quarterly calendar when excluding incomplete observations: lags and growth transformations must not jump across a missing quarter. Daily holiday gaps differ from unexplained gaps: preserve source missing markers and diagnose them before using values. For implemented preparation behavior when an endpoint is unavailable, see [preparation rules](DATA_PIPELINE.md#preparation-rules).

FRED quarterly and monthly dates label observation periods, not release dates. Latest revised exports support retrospective conditioning only. The implemented API vintage contract and its historical-information limitations are documented in [source and temporal contracts](DATA_PIPELINE.md#source-and-temporal-contracts).

## Analytical and schema contracts

Executable schemas require `schema_version`, reject unknown fields, and identify units. Foundation, data configuration and snapshot records have explicit schema contracts. Projection and scenario contracts are documented in [projection models](PROJECTION_MODELS.md) and [scenarios and evaluation](SCENARIOS_AND_EVALUATION.md). [Pricing and replay](PRICING_AND_REPLAY.md) implement baseline/portfolio/valuation records for historical and direct inputs; conditional pricing and saved-run orchestration are documented in [integrated execution](INTEGRATED_EXECUTION.md).

| Record | Required meaning |
|---|---|
| Data observation | Series ID, period/date, numeric value or missing marker, native frequency/units, release or vintage information when available, snapshot ID |
| Baseline state | Valuation date, requested/effective market dates, normalized equity price, observed 2y/10y yields, discount representation, selected credit spread, macro origin quarter/vintage, source snapshot IDs |
| Scenario | ID/version, analytical mode (`historical_replay` or `conditional_projection`) or `direct_shock` diagnostic designation, input type, narrative, requested dates, horizon, units, source/design rationale, explicit reference and change convention |
| Projected state | Scenario ID, quarter label, market levels, incremental and cumulative equity returns where appropriate, warning flags, model version and starting-state reference |
| Portfolio | ID/version, USD currency, fixed holdings; equity units and baseline price; bond face, coupon rate, schedule, maturity, day count, compounding and pricing convention; cash amount |
| Valuation result | Baseline/state values and cumulative/state-to-state P&L by position and asset class; reconciled factor contributions; warnings and checks |
| Run manifest | Schema version, run ID, UTC timestamp, source/code fingerprint, data/config/model hashes, versions, dependency/runtime lock, seeds, warnings, check status and artifact paths |

Historical replay applies observed changes to the common baseline. Conditional projection uses supplied macro paths and market lags; recursive quarters use predicted market lags. Direct shocks bypass model estimation. Historical reconstruction using realized macro paths is explicitly labeled retrospective.

Conditional level projections start at the declared baseline state; use the [proxy baseline interpretation](PROXY_DATA.md#alignment-and-missing-observations) for the proxy track. The implemented anchoring formula and unanchored comparison are owned by the [projection-to-pricing contract](INTEGRATED_EXECUTION.md#projection-to-pricing-contract). Equity price starts at the normalized baseline. The origin is not itself a stressed valuation. Reject or warn on economically invalid paths rather than silently clipping them.

## Concrete v1 pricing design

The implemented pricing contract is owned by [pricing and replay](PRICING_AND_REPLAY.md): synthetic schedules and accrual, two-node par-as-zero discounting, continuous GZ margin, fixed holdings and valuation time, domain checks, curve-overlay sensitivity, and reconciled order-dependent attribution. It preserves the foundation design constraints; neither the curve nor the spread mapping is a calibrated issuer valuation. A yield-difference fallback would change the mapping and must be labeled separately.

## Evaluation rules declared before modeling

Foundation configuration requires integer counts of at least 40 training quarters, 12 chronological validation quarters and 20 final holdout quarters. Larger integer counts are allowed; booleans, nonintegers and lower counts are rejected both when loading configuration and when calling `fit_models` directly. The defaults use these lower bounds and evaluation horizons 1, 4, 8 quarters where feasible. These are minimum sample rules, not guarantees of statistical adequacy. An evaluation origin may be the quarter immediately preceding its partition. Every future rolling-origin label, including intermediate quarters, must stay within that evaluation partition; do not allow multi-quarter labels to cross a selection/holdout boundary.

Evaluate contemporaneous macro-conditioned reconstruction separately from any lagged-information forecast. Fit transformations inside each training window. Compare identical origin/horizon sets. Report actual eligible observations after alignment, transformations and lags. Insufficient event history produces an unavailable result, not a relaxed threshold. Formal prediction intervals are optional; horizon error summaries and model/window sensitivities are required.

Default dollar reconciliation/reproduction tolerance: `abs(a-b) <= max(0.01 USD, 1e-10 * max(abs(a), abs(b)))`. This is a numerical agreement criterion, not a financial model validity threshold.
