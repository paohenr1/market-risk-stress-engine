# Monitoring specification

This is a future review specification, not an active monitoring service. No alerts or scheduled jobs are deployed. The research owner should assign review responsibility before recurring use.

| When | Check | Trigger | Required response / retained record |
|---|---|---|---|
| Before each acquisition | Source definition, entitlement and redistribution terms | Provider field, methodology, access or rights change | Stop affected acquisition; record metadata and review the mapping before reuse |
| At preparation | Hashes, schema, units, duplicates, dates, common-day quality and required months | Integrity failure, unexpected unit, failed coverage rule | Reject or preserve explicit missing state; never fill or compress a calendar gap |
| Before fitting | Training/validation/holdout calendar and eligible counts | Fewer usable observations than required | Mark unavailable; report exclusions without relaxing sample minima |
| At each refit | Rank, VIF, residual dependence, AR stability and coefficient/window diagnostics | Rank failure; VIF above 10; absolute AR coefficient at least one; changed diagnostics | Mark rank failure unavailable; otherwise preserve warnings and request research review before adopting changes |
| At evaluation refresh | Fixed-specification MAE/RMSE/bias, error quantiles and counts by horizon | Material deterioration or changed comparable origins | Retain old/new results and investigate data/window effects; do not retune on the final holdout |
| At projection | Predictor ranges, recursion and market/macro domains | Extrapolation, nonfinite result, invalid price/spread/unemployment | Flag extrapolation; reject invalid states without clipping or fallback |
| At pricing | Zero shock, sizing, position/asset/factor/reverse/state-to-state sums | Failure beyond configured USD/relative tolerance | Fail the result; retain inputs and investigate before report generation |
| At reproduction/reporting | Inventory, hashes, source/runtime identities, exhibit/run IDs and layout | Altered artifact, mismatched source/runtime, failed bounds or page count | Reject reproduction/rendering; create a new identified run/report after correction |
| Before adopting a model/scenario change | Purpose, parameter/window/horizon sensitivity and independent challenge | Any proposed definition or selection-rule change | Declare a new experiment, update change log, rerun relevant checks and retain prior evidence |
| Before external sharing | Exact files/history/metadata, credentials, rights and claims | Any unresolved secret, disclosure, rights or evidence concern | Resolve disclosure and redistribution concerns before distribution |

Performance-deterioration thresholds are intentionally not calibrated here. Set them prospectively for a defined use, sample size and review budget; the existing descriptive diagnostics alone do not justify an automated acceptance threshold. Curve overlays remain design sensitivities, not pass/fail financial validity tests.
