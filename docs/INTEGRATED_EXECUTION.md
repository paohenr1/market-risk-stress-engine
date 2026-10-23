# Integrated stress cycles and offline reproduction

The `run-stress` command connects validated model choices to fictional portfolio pricing. It runs historical replays, conditional macro scenarios and direct shocks individually or as a batch. This is a research pipeline for examining assumptions and fixed-time valuation changes. It is not an investment recommendation, operational risk system or release-time forecasting exercise.

## Run from saved inputs

Use an editable installation from this repository, Python 3.11 or newer, and the preparation/evaluation commands in [proxy data](PROXY_DATA.md) and [scenarios and evaluation](SCENARIOS_AND_EVALUATION.md). Runtime dependencies are the Python standard library; the build dependency is pinned in `pyproject.toml` and `requirements-bootstrap.txt`. Saved runs record the exact Python version and implementation. No network access or API key is needed for these commands.

From the repository root in PowerShell:

```powershell
.\.venv\Scripts\python.exe -m market_risk run-stress `
  --data outputs/proxy/checked `
  --evaluation outputs/evaluation/research `
  --config configs/models.toml `
  --foundation configs/foundation.toml `
  --portfolio configs/portfolio.json `
  --scenarios configs/scenarios.json `
  --output outputs/runs/stress-cycle

.\.venv\Scripts\python.exe -m market_risk reproduce-run `
  --run outputs/runs/stress-cycle `
  --output outputs/runs/stress-cycle-repeat
```

Replace the data and evaluation paths with the corresponding saved artifacts for a different dataset vintage. An evaluation must match the prepared CSV, model configuration and evaluation rules exactly. The command verifies both evaluation file hashes, recomputes the validation-only decision from its saved summaries and checks its selection hash and calendar. Holdout scores are not used for selection. Hashes detect accidental alteration; they do not authenticate the origin of a deliberately rewritten artifact.

Omit `--scenario` to run all definitions. Add `--scenario illustrative_recession` to run one, or repeat the option with distinct known IDs to run several. Results retain configuration order. Unknown, duplicate or empty selections fail. Scenario definitions remain editable through the documented schema; use a fresh output directory for each execution. Existing output directories are never overwritten. A failed calculation may leave an incomplete directory; only a completed manifest identifies a saved run.

Console output contains run identity, counts, hashes and paths. Prices, coefficients, forecasts, errors and P&L stay inside ignored local output files. These files include subscribed-data derivatives and must not be redistributed without the necessary rights and appropriate disclosure review.

## Projection-to-pricing contract

Conditional origins must equal the declared portfolio baseline quarter and must not predate the end of the validation period that selected the models. Historical replay remains independent of these selections. Each selected specification is refitted using its frozen expanding or rolling training window through the conditional origin. A missing or unfittable selection makes that scenario unavailable; the engine does not substitute a different model. Fitted candidates and selected-model diagnostics are saved. Future market targets are never projection inputs.

For an observable yield or spread in percent, the anchored pricing level is:

`baseline_decimal_level + (forecast_percent_level - observed_origin_percent_level) / 100`.

Equity starts at the configured normalized price and compounds incremental projected changes: `price_h = price_0 * product(1 + return_q / 100)`. Cumulative shocks are always applied once to the original baseline. State-to-state P&L is the difference between successive cumulative P&Ls; it is not a coupon or holding-period return.

The origin state uses observed baseline levels and zero P&L. No fitted-origin state replaces the observation. Static, mean or autoregressive models can move toward their fitted conditional level in the first projected quarter; that movement remains visible rather than being removed by an undisclosed adjustment. Each conditional case records this convention, selected forecasts, implied unemployment levels, model and extrapolation warnings, an explicit origin state and an unanchored comparison using absolute forecast levels. Because the current interface requires the same observed origin as the portfolio baseline, the anchored and unanchored yield/spread levels coincide apart from floating-point rounding. The comparison is an audit of that identity, not a claim of a material adjustment.

Invalid unemployment levels, equity changes at or below minus 100%, negative spreads, invalid yield compounding domains or failed fits are explicit unavailable cases. No clipping is applied. Out-of-training-range warnings remain visible even when pricing succeeds. A completed batch can contain unavailable cases; inspect scenario statuses rather than interpreting process success as universal availability.

The same fixed holdings, contractual cashflows, valuation date, curve construction, attribution and sensitivity functions serve all three modes. See [pricing and replay](PRICING_AND_REPLAY.md) for the par-as-zero approximation, continuous GZ margin, clean/dirty/accrued conventions and maturity limitations. The original `price-scenarios` command retains its replay/direct-only scope and explicitly defers conditional cases; `run-stress` is the integrated command.

## Saved-run schema, version 1

| File | Contract |
|---|---|
| `manifest.json` | Run ID, UTC timestamp, optional parent run, selected scenario IDs, file hashes, source identity, runtime/dependency record, randomness declaration and check outcomes |
| `inputs/data/` | Prepared CSV and its original manifest, including source snapshot/vintage metadata where available |
| `inputs/evaluation/` | Evaluation JSON, Markdown and original hash manifest; preserves the validation decision and comparison evidence |
| `inputs/*.toml`, `inputs/*.json` | Exact model, foundation, portfolio and scenario configurations |
| `valuation.json` | Run ID, snapshot IDs, data/selection hashes, fixed holdings, risks, all scenario statuses and priced paths, conditional diagnostics and reconciliation checks |
| `valuation.md` | Run-identified numerical tables and limitations, generated from the same calculation |
| `models.json` | Run ID and fitted model bundles by origin/window, including model version IDs and training diagnostics |
| `events.json` | Run ID, completed processing steps and scenario statuses; contains no numerical market results |

The source identity hashes every package Python file and `pyproject.toml`, including local modifications, with CRLF normalized to LF so Git checkout line endings do not change the executable fingerprint. Git revision and dirty status are recorded separately when available. Documentation changes alone do not invalidate the executable fingerprint. There are no runtime package dependencies or random operations; the manifest records an empty dependency list and a null seed explicitly. Configuration and model file hashes are part of the complete file inventory. The input manifests carry the source snapshot identities, while the valuation repeats those identities for report provenance.

`reproduce-run` verifies the exact inventory and every file hash, rejects links/junctions inside the bundle, and requires the identical executable fingerprint, Python implementation and exact Python version. It then calculates from the captured inputs into a new directory without contacting a data provider or relying on the original input paths. The new run links to its parent. It compares every calculation and fitted-model field; dollar results use the configured absolute and relative tolerances (defaults: USD 0.01 and 1e-10), and other floating values use absolute 1e-12 or the configured relative tolerance (default: 1e-10). Discrete values, statuses and schemas must agree exactly. Run IDs and timestamps differ by design. A mismatch is an error, never a successful reproduction label.

This requires retaining the matching source checkout and runtime. It is a verified local rerun contract, not a packaged environment, signed archive or guarantee of equivalence across Python versions. The formal technical report is a separate deliverable; this command generates the numerical valuation report that can supply its exhibits.
