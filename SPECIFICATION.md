# Research scope and acceptance criteria

This project studies a reproducible market-risk research workflow: data preparation, market-variable projection, chronological comparison, scenario construction, fictional portfolio valuation and reporting. It is educational software for researchers inspecting assumptions and implementation. It does not reproduce a bank's proprietary methodology or establish regulatory compliance.

## Data and timing

The executable dataset combines FRED macroeconomic and Treasury observations, Intrinio SPY split-only prices and the Federal Reserve's GZ nonfinancial corporate spread. The original broad equity-index and investment-grade OAS targets remain unavailable. Neither proxy is relabelled as the original target. See [source definitions and restrictions](docs/PROXY_DATA.md) and [source feasibility](docs/DATA_FEASIBILITY.md).

Daily SPY and Treasury observations share a common-date panel within each month. Quarterly market states are equal-weight averages of three monthly states; GZ uses its separate monthly bond panel. All required months must be present. Missing quarters are retained and calendar lags never bridge gaps. Equity changes describe average price states, exclude dividends and are not realized quarter-end returns.

Snapshots record retrieval time, vintage where available, source metadata and hashes. Revised macroeconomic data support retrospective conditioning, not historical information-set claims. Raw observations and numerical derivatives remain local, subject to provider rights. Prefer official structured sources and never put credentials in source, logs or configuration files.

## Projection and evaluation

Four targets cover equity price changes, 2-year and 10-year Treasury yields, and the credit spread proxy. Benchmarks include no-change, historical mean, AR(1) and static macro regression; credit also has a dynamic regression challenger. Estimation and diagnostics are described in [projection models](docs/PROJECTION_MODELS.md).

Training requires at least 40 eligible quarters. Chronological validation and final holdout reserve at least 12 and 20 calendar quarters respectively, with at least 12 usable one-quarter validation errors required for selection. Higher configured counts are allowed. Expanding and rolling fits compare common dates; preprocessing uses training data only. Validation MAE selects specifications/windows before holdout results are computed. Holdout diagnostics cannot retune the selection. Missing inputs or insufficient samples yield explicit unavailable results.

Recursive paths span 1–8 consecutive quarters, condition on supplied macros and use predicted market lags after the origin. Zero residual innovations do not imply certainty. Report diagnostics, extrapolation warnings, window/horizon sensitivity, actual eligible counts and descriptive historical errors. Do not assign scenario probabilities or call historical error quantiles prediction intervals. See [evaluation rules](docs/SCENARIOS_AND_EVALUATION.md).

## Scenarios and pricing

Separate historical factor replay, macro-conditioned projection and direct shocks. Configurable historical examples cover the financial crisis, pandemic and monetary tightening; hypothetical recession/stagflation paths and direct shocks are illustrative inputs. Preserve origins, horizons, units and change references. Reject invalid economic states without clipping or silently substituting models.

The fictional USD 1 million portfolio has baseline weights of 40% equity, 20% short government bond, 20% long government bond, 15% corporate spread proxy bond and 5% cash. The fixed valuation date is June 30, 2026, associated with an illustrative 2026Q2 average market state. Equity is normalized to 100; holdings are fractional and fixed. Synthetic bond schedules, accrual and clean/dirty values are explicit. Valuation time and remaining cashflows do not advance through scenario labels.

Map the two Treasury quotes into the disclosed continuous par-as-zero proxy, interpolate between nodes and extrapolate flat outside them. The corporate bond adds a flat continuous GZ margin; this is not issuer calibration or investment-grade OAS pricing. Full repricing is the reference. Reconcile positions, asset classes, factor attribution in two orders, interaction residuals and state-to-state changes. Report first/second-order approximation errors and curve overlays. See [pricing conventions](docs/PRICING_AND_REPLAY.md).

## Reproducibility and reports

Financial calculations remain separate from acquisition and presentation. Configuration records declare schemas, units, conventions and editable assumptions. CLI commands return safe status summaries and reject ambiguous or invalid inputs. Saved runs capture exact input files, configuration, model decisions, source/runtime identity and output hashes. Existing output directories are not overwritten.

Offline reproduction requires matching source/runtime and compares calculations within declared tolerances. Default dollar agreement is `abs(a-b) <= max(0.01, 1e-10 * max(abs(a), abs(b)))`; this is a numerical criterion, not a validity threshold. Reporting verifies saved artifacts without refitting or silently revaluing them. Markdown, CSV and optional PDF/SVG exhibits identify their parent run and disclose captured assumptions. Rendering success and visual review are separate checks.

## Acceptance and unresolved limits

Verification covers input rejection, missing-data/calendar rules, chronological isolation, analytical pricing references, sensitivity behavior, reconciliation, offline reproduction, artifact integrity and report binding. Preserve actual failures and distinguish skipped checks from passes. Methods, limitations, resolved issues and prospective monitoring are documented separately:

- [Verification results](docs/VALIDATION_STATUS.md)
- [Limitations](docs/LIMITATIONS.md)
- [Validation issues](docs/VALIDATION_ISSUES.md)
- [Monitoring specification](docs/MONITORING.md)

Implemented software does not establish forecasting improvement, independent financial validation, production readiness or coverage of omitted risks. Numerical data and reports are not included in the source package. Project-owned source code and original documentation are MIT licensed. Third-party data and dependencies remain subject to their respective terms; data redistribution rights must be established before distributing numerical artifacts.
