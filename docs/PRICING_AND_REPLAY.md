# Fictional portfolio pricing and historical replay

The pricing command turns observed historical factor changes and direct shocks into fixed-time portfolio diagnostics. It is for researchers inspecting understandable pricing, sensitivities and P&L attribution. It does not reproduce a traded portfolio, calibrate an issuer curve, predict defaults or measure realized investment returns. For conditional pricing and saved-run reproduction, see [integrated execution](INTEGRATED_EXECUTION.md).

## Run offline

Use the prepared proxy inputs from [PROXY_DATA.md](PROXY_DATA.md):

```powershell
.\.venv\Scripts\python.exe -m market_risk price-scenarios --portfolio configs/portfolio.json --foundation configs/foundation.toml --data outputs/proxy/checked --scenarios configs/scenarios.json --output outputs/pricing/example
```

Choose a new output directory. Read `valuation.md` locally for P&L and approximation tables. `valuation.json` contains contracts, fixed quantities, baseline and stressed position values, cumulative and state-to-state P&L, attribution in two orders, sensitivity matrices, proxy overlays, warnings and checks. The manifest binds JSON and Markdown bytes by SHA-256. Input snapshots/vintage, prepared-data/config/scenario hashes, code fingerprint, conventions, runtime and tolerances accompany results. A batch records unavailable cases explicitly rather than fabricating values; conditional scenarios are explicitly deferred. CLI output is restricted to counts, flags, hashes and paths. All numeric reports remain ignored local internal-use artifacts containing subscribed-data derivatives.

## Baseline and fictional holdings

`configs/portfolio.json` declares a USD 1 million portfolio at the fixed valuation date June 30, 2026, associated with the 2026Q2 quarterly-average proxy state. That state is an illustrative average of monthly states, not simultaneous quotes observed on June 30. Its macro state, source-data hash and vintage are retained; revised data must not be represented as available on that date. A valid observed SPY level is required to establish the baseline, but equity is normalized to a fictional price of 100. The normalization does not fabricate a historical price observation.

| Position | Baseline market-value weight | Contract |
|---|---|---|
| Broad equity proxy | 40% | Fractional shares at normalized price100; price changes only |
| Short government bond | 20% | Synthetic 4% fixed coupon; maturity2028-06-30 |
| Long government bond | 20% | Synthetic 4.5% fixed coupon; maturity2036-06-30 |
| Corporate spread proxy bond | 15% | Synthetic 5% fixed coupon; maturity2031-06-30 |
| Cash | 5% | Fixed nominal USD balance |

All bonds are noncallable synthetic instruments. Their explicit semiannual coupon dates are anchored to June 30/December 30 and are unadjusted for holidays. The previous coupon date is June 30, 2026; on that date the coupon is treated as already paid and excluded from remaining cashflows. These contracts do not purport to follow all real Treasury settlement/calendar rules. Portfolio weights, coupons, schedules and sensitivity grids are editable under the strict schema. The only supported conventions are those explicitly declared in the file.

One bond unit is face100. The semiannual coupon per unit is `100*(coupon_pct/100)/2`; the final cashflow adds principal100. For each position, target market value divided by its baseline dirty unit value determines quantity. Quantities remain fractional and unrounded: equity quantity is shares, bond quantity is face100 units, and cash quantity is USD. Sizing difference from the target portfolio value is reported and checked against configured tolerance. No claim of executable trading lots is made. Holdings never change after sizing.

## Discounting, accrual and curve approximation

Prepared rates in percent are divided by100 once to obtain decimal rates. Basis-point shocks are divided by10000 once. A 100bp shock is a 0.01 decimal-rate change. A 2% equity price shock is a multiplicative factor1.02.

The two observed Treasury par-yield proxies `y2,y10` become continuous zero-rate proxies `z=2*log(1+y/2)`. Interpolate `z` linearly between2 and10 years; hold the endpoint rate flat outside that interval. Discount each future cashflow at `exp(-z(t)*t)`, where `t` is actual calendar days from the fixed valuation date divided by365. Negative yields are allowed when `1+y/2>0`. Nonfinite input, nonpositive equity price, negative credit spread, invalid compounding bases and nonfinite/underflowed nonpositive bond values fail clearly. Finite poor outcomes are never clipped into plausible ones.

Treasury describes its official curve as a [par-yield curve](https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics/treasury-yield-curve-methodology). Converting compounding conventions does **not** bootstrap par yields into true zero rates. This implementation deliberately uses the previously documented two-node par-as-zero approximation. Cashflows below2 or beyond10 years generate flat-extrapolation warnings; even a nominal10-year maturity can extend slightly beyond10 ACT/365 years because of leap days.

For the fictional corporate bond, add the decimal GZ spread as a constant continuously compounded discount margin at every cashflow maturity. GZ is a nonfinancial corporate spread, not IG-only OAS or an issuer curve. The mapping is an illustrative proxy to this single fictional bond; spread widening is not a default-loss model.

Dirty price is the discounted remaining cashflow sum. Accrued interest is the coupon amount multiplied by actual days elapsed since the previous coupon divided by actual days in the coupon period. Clean price is dirty minus accrued. Accrued interest stays fixed throughout stress repricing because valuation time stays fixed. The explicit synthetic ACT/365 discounting/calendar convention differs from full production Treasury pricing, so a coupon equal to a flat quoted yield need not give exactly par under irregular actual calendar lengths. Treasury confirms [semiannual coupon payments](https://www.treasurydirect.gov/marketable-securities/understanding-pricing/); it does not endorse this simplified curve or contract mapping.

To quantify design sensitivity without claiming a calibrated curve-error bound, the default report adds ±25bp to both continuous zero proxies, leaving the original sized holdings fixed. It shows alternative baseline values and alternative final scenario P&L, each relative to its own overlay baseline, together with differences from the main curve result. These invented overlays expose sensitivity; they do not measure the actual par-versus-zero error or supply uncertainty probabilities.

## Replay and shock semantics

Historical presets retain their exact common quarterly-average origin and end labels from [SCENARIOS_AND_EVALUATION.md](SCENARIOS_AND_EVALUATION.md). The historical origin must have valid observed equity price, yield and spread levels; its incoming return is not required. Every subsequent quarter must provide the consecutive equity change and observed market levels needed to calculate the path; gaps are never bridged. Quarterly-average equity changes compound as `product(1+r/100)-1`. Yield and GZ cumulative changes are differences from the historical origin, expressed in basis points.

Apply those cumulative changes independently to the **common2026Q2 baseline**, rather than replacing it with historical levels. A cumulative -20% then -12% means baseline equity100 becomes80 then88; it does not become70.4 by applying -12% to80. State-to-state P&L is the difference between successive cumulative P&Ls. Their sum must reconcile to final cumulative P&L. Requested/effective historical labels remain identical; quarter labels identify average observation periods, not executable daily endpoints.

Direct shocks are explicitly cumulative changes from the same common baseline and must declare that baseline quarter. They bypass estimation. The default direct scenario is an invented diagnostic, not an historical event or macro projection. All states are checked again at the pricing boundary, including rate/spread validity after historical changes are applied to today's illustrative baseline.

Valuation date, remaining cashflows, accrued interest and holdings remain fixed. No coupons/dividends are received, no instruments mature and no cash is reinvested as scenario labels advance. Cash remains its starting nominal amount. The resulting P&L is a fixed-time diagnostic, not a realized horizon total return. The default `price-scenarios` batch prices three historical replays and one direct shock, totaling13 states. That command marks the two macro scenarios deferred; use [integrated execution](INTEGRATED_EXECUTION.md) to price them with frozen validation selections.

## Attribution and approximation checks

Position P&L is stressed dirty value minus baseline dirty value. Positions aggregate into equity/government/corporate/cash classes and must sum to portfolio P&L. Factor attribution sequentially applies equity, short yield, long yield and spread. Each factor receives the value difference after adding its change to those already applied. The report repeats attribution with equity/spread/long/short order, explicitly exposing order dependence. Both sums must reconcile. Isolated single-factor P&Ls and their interaction residual are shown separately; they are not falsely asserted to add to total P&L. Largest negative position contributions are identified.

Sensitivities use finite differences of exactly the same fixed-holding pricing function. At the default1bp bump, the rate gradient is in USD per decimal quote-rate unit and the Hessian in USD per squared decimal-rate unit. Parallel duration is `-(dV/dy2+dV/dy10)/V`; parallel convexity is the sum of the four2x2 rate-Hessian entries divided byV. DV01 reports the positive loss magnitude associated with an upward1bp shift for conventional long bonds. Spread DV01 is reported separately.

Central differences normally apply. When the spread is smaller than the bump, finite differences use declared forward formulas to avoid evaluating negative spreads: a second-order forward first derivative and a forward second derivative, with corresponding mixed differences. No hidden spread clipping occurs. Rate bumps that violate the compounding domain fail explicitly. The report records the bump and difference method; finite-difference accuracy is subject to bump/cancellation effects.

Default diagnostic shifts are ±1/25/200bp and zero in parallel, plus +100/-100bp and -100/+100bp twists. Duration-only versus duration-plus-convexity comparisons are meaningful for parallel shifts. Nonparallel comparisons use two-node key-rate derivatives, including cross terms in the quadratic approximation. Combined replay/shock approximations include the rate/spread3x3 Hessian and exact equity price P&L. Full repricing is the reference; approximations may deteriorate badly for large shifts. Approximation errors are reported in USD and relative to baseline value. A P&L-relative ratio is omitted when absolute P&L is below the larger of one cent and one millionth of baseline value.

By default, reconciliation uses the foundation's `max(0.01USD,1e-10*max(abs(actual),abs(expected)))` tolerance. Zero-shock identity is also checked exactly. Software tests compare prices with independent Decimal discount formulas and zero-coupon algebra, check coupon/accrual semantics, monotonic yield/spread behavior, finite-domain rejection, sensitivities against analytical zero-coupon derivatives, small-shock approximation accuracy, nonparallel behavior and nonlinear attribution. These checks do not establish operational valuation accuracy, calibrated credit risk, independent financial validation or historical release-time forecasting accuracy.
