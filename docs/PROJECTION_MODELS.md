# Conditional projection models

This module fits research candidates for the SPY/GZ quarterly-average dataset. It is for readers studying a small, auditable conditional modeling pipeline. The fit command does not select a model. The separate [evaluation](SCENARIOS_AND_EVALUATION.md) supplies chronological validation selection and conditional holdout comparisons. Historical replay and direct-shock portfolio pricing are documented in [pricing and replay](PRICING_AND_REPLAY.md); conditional pricing is documented in [integrated execution](INTEGRATED_EXECUTION.md).

## Run offline

Prepare the prepared dataset using [PROXY_DATA.md](PROXY_DATA.md), then run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m market_risk fit-models --config configs/models.toml --foundation configs/foundation.toml --data outputs/proxy/checked --output outputs/models/example
.\.venv\Scripts\python.exe -m market_risk project-models --models outputs/models/example --macro-path examples/macro_path.json --output outputs/projections/example
```

Use new output directories on each invocation; existing directories are never overwritten. CLI output contains counts, dates and hashes, not coefficients or market observations. Models and projections contain subscribed-data derivatives: keep them under ignored `outputs/` for local internal use. Do not upload these artifacts without establishing redistribution rights and completing disclosure review.

The example macro path is invented solely to exercise the API. Its dates do not make it historical evidence or a calibrated recession scenario. The supplied origin must match the fitted training cutoff. `--train-end YYYYQn` can move that cutoff earlier, never into reserved validation or holdout data. Future macro rows must have consecutive calendar quarters and exactly the four fields in the example. Inputs use percent for annualized quarter-over-quarter real GDP growth, year-over-year quarterly-average CPI growth and the policy rate; unemployment change uses percentage points from the preceding quarter. The API conditions on changes and does not reconstruct unemployment levels; level consistency is a scenario-construction responsibility.

## Targets and candidate equations

Write `y_t` for a target, `x_t` for supplied contemporaneous macro conditions, and `e_t` for a residual. Target names retain their definitions:

| Target | Definition | Static predictors |
|---|---|---|
| `equity_price_return_pct` | Percent SPY split-only price change between quarterly-average states; excludes dividends | GDP growth |
| `yield_2y_pct` | Quarterly average of monthly common-date Treasury 2-year means, percent | Policy rate, inflation |
| `yield_10y_pct` | Corresponding 10-year yield level, percent | Policy rate, inflation |
| `credit_spread_proxy_pct` | Quarterly mean GZ nonfinancial corporate spread, percent; not IG-only OAS or EBP | GDP growth, unemployment change |

Each target has four benchmarks:

- No-change: zero SPY price change; previous level for yields and spread.
- Historical mean: training mean of that target, held constant over the path.
- AR(1): `y_t = a + phi*y_(t-1) + e_t`.
- Static: `y_t = a + b'x_t + e_t`.

The GZ challenger adds the same two macro predictors to its lagged level: `s_t = a + phi*s_(t-1) + b1*gdp_t + b2*unemployment_change_t + e_t`. This creates 17 candidates. Weak growth and rising unemployment motivate credit conditioning; GDP provides a simple equity association; policy and inflation describe yield conditions. These are associations, not causal estimates or complete asset-pricing models. Predictor lists are editable within the supported four-macro registry in `configs/models.toml`; specification changes must be settled before final evaluation.

## Estimation and partitions

OLS uses training-only means and population standard deviations, followed by reorthogonalized QR. It avoids squaring the condition number through normal equations. The implementation uses the standard library to keep this small candidate set dependency-free. Behavioral checks recover known coefficients and exercise singular designs; these do not substitute for independent financial validation. Constant or rank-deficient designs are explicitly unavailable; no implicit ridge penalty or fallback regression is applied.

All candidates share one eligible sample: current targets and configured macros must exist, and all targets must exist in the immediately preceding calendar quarter. No imputation or gap compression occurs. This intentionally sacrifices observations for comparability. The configured training minimum counts eligible rows; holdout and preceding validation reservations count calendar quarters, including incomplete rows. Counts follow the [foundation evaluation rules](FOUNDATION.md#evaluation-rules-declared-before-modeling). Earlier explicit cutoffs leave an additional unused interval. With the current prepared data and default configuration, training ends 2018Q2, validation starts 2018Q3, holdout starts 2021Q3, and 96 training rows are eligible. Missing validation/holdout rows reduce evaluation feasibility; see the [evaluation protocol](SCENARIOS_AND_EVALUATION.md#evaluation-protocol-declared-before-results) for common-date exclusions and unavailable exercises.

CSV contents are bound to the preparation manifest by SHA-256. This checks integrity, not external authenticity: reproduce preparation from verified source snapshots when auditing provenance. Saved models bind prepared data, model configuration, evaluation rules, code hash and Python version. A separate hash protects fitted artifact bytes at projection load time. These hashes are reproducibility checks, not signatures against deliberate tampering.

## Recursive projection and diagnostics

Forward macro paths must contain 1 through 8 consecutive quarters, including every intermediate quarter needed for recursion. Empty paths and paths of 9 or more quarters are rejected before output creation. This bounds the forward path length, not historical coverage: earlier fitted origins remain supported subject to training availability, sample minima and reserved-partition rules.

Macro-path JSON rejects duplicate object keys at every nesting level, even when their values are identical. Rejection occurs before output creation and does not echo the supplied keys or values.

The first projection starts after the last observed training quarter. Every AR/dynamic step uses its own previous prediction, never a realized future market target. Residual innovations are zero; no scenario probability or formal interval is supplied. Static and mean models can jump after the observed origin; the saved origin remains the observed state and no fitted origin is substituted. The [integrated pricing contract](INTEGRATED_EXECUTION.md#projection-to-pricing-contract) preserves that reference. Equity outputs are incremental average-state price changes; yield and spread outputs are levels. No equity price path or portfolio valuation is produced here.

Each model records in-sample residual RMSE in target units, adjacent residual-pair counts, a lag-one residual cosine, and an adjacent squared-difference ratio. The last two use only truly adjacent calendar pairs; they are descriptive diagnostics, not formal tests with p-values. One percentage unit of yield/spread error is 100 basis points. Training residuals are not uncertainty estimates for future paths.

VIFs flag values above 10. AR coefficient magnitude at least one warns of potential recursive instability. Regressions are also refitted on disjoint early/late training halves when each meets the configured training minimum, plus the most recent eligible rows equal in count to that minimum; original-unit coefficients allow stability/window comparisons. Windows count eligible rows and can span calendar gaps. These diagnostics expose concerns without selecting a winner or silently changing a model.

Projected predictors outside their training min/max range receive warnings. Finite but invalid pricing outputs (SPY changes at or below -100%, negative GZ spread, yield at or below -200% under the semiannual conversion described in [pricing and replay](PRICING_AND_REPLAY.md)) are retained and flagged, never clipped. These checks are limited domain guards; an unflagged path is not evidence of plausibility. Nonfinite recursive output fails. The evaluation command implements the separate conditional reconstruction comparisons, multi-horizon errors, stress failures and historical-error uncertainty summaries.
