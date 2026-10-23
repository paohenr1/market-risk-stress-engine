# Validation issue register

See [validation status](VALIDATION_STATUS.md) for verification evidence. This register separates resolved implementation issues from remaining research limitations.

| ID | Issue | Status | Evidence or disposition |
|---|---|---|---|
| V01 | Exact index/IG-OAS inputs unavailable | Open source limitation | Labelled SPY/GZ proxy track implemented; original dataset readiness remains false |
| V02 | Minimum sample thresholds could be weakened | Resolved implementation issue | Exact integer minima of 40 training /12 validation /20 holdout enforced and tested |
| V03 | Duplicate macro JSON keys could be ambiguous | Resolved implementation issue | Duplicate keys rejected at every nesting level before projection |
| V04 | Forward paths could exceed the declared horizon | Resolved implementation issue | Consecutive 1-8-quarter paths enforced |
| V05 | Evaluation origins omitted the available predecessor observation | Resolved implementation issue | Pre-partition origin supported while all forward labels remain inside the partition |
| V06 | Historical starting state unnecessarily depended on incoming equity return | Resolved implementation issue | Positive price and valid observed yield/spread levels suffice at origin; forward gaps remain unavailable |
| V07 | Conditional integration and saved-run reproduction absent | Resolved implementation issue | Saved-run execution checks with frozen selections and offline reruns |
| V08 | Invented macro paths coincide under selected models | Disclosed empirical finding | Selected AR/no-change specifications omit macro predictors; no fallback manufactured |
| V10 | Dedicated static lint and remote CI not configured/executed | Open verification limitation | Git whitespace is the configured lint step; remote push/PR/CI remain skipped |
| V11 | Financial validity and omitted risks unresolved | Open research limitation | Limitations register and independent challenge required before any operational use |
| V12 | Report generation, CSV binding and PDF layout | Resolved implementation issue | Report checks passed; full local suite, independent CLI/value-binding/geometry checks, redacted actual layouts and synthetic chart review recorded |
| V13 | Report wording/chart labels assumed default configuration | Resolved implementation issue | Captured assumptions/IDs, government-only selection, fractional coordinates and one USD-thousands scale verified by report tests and review |
| V14 | Reproduction paragraph and macro exports could misdescribe edited or individual runs | Resolved implementation issue | Captured relative tolerance and run-scoped definitions/exports, including selected unavailable cases, covered by regression tests |
| V15 | One-point chart series had no visible mark | Resolved implementation issue | Singleton filled-circle SVG/PDF markers preserve coordinates and multi-point behavior; regression tests and review passed |

Reopen a resolved issue if data, configuration, source or runtime changes invalidate its evidence. A new numerical result is not grounds for silently changing selection rules or weakening tests.
