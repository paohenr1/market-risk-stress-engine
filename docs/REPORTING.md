# Report generation

The report is generated from a verified saved stress run. It contains twelve main sections and two appendices, with seven CSV exhibits and three static charts for the default six-scenario batch. It covers data, models, evaluation, valuation, attribution, approximation error, uncertainty and reproduction. The report does not download data, fit new models or silently calculate a different valuation.

## Generate the local deliverables

After following [integrated execution](INTEGRATED_EXECUTION.md), generate report source with the installed standard-library engine:

```powershell
.\.venv\Scripts\python.exe -m market_risk generate-report `
  --run outputs/runs/stress-cycle `
  --output outputs/reports/technical
```

This creates `report.md`, a structured `document.json`, seven CSV files and `report_manifest.json`. Every CSV row and numerical exhibit identifies the parent run. The manifest hashes the source artifacts and binds the saved-run manifest and reporting source fingerprints. This command can report an archived run whose executable version differs from the current code; it verifies its integrity without claiming current-source numerical reproduction. `reproduce-run` continues to require matching source and runtime.

Report assumptions come from the captured preparation metadata, model/evaluation configuration, portfolio, tolerances and scenario definitions. Report labels such as Replay 1 and Conditional 1 map to saved IDs in the scenario table and `scenario_labels` record. Scenario definitions and macro-condition exports include only scenarios selected in the saved run, retaining selected unavailable cases; a direct-only run has a header-only `macro_conditions.csv`. Missing preparation thresholds in older manifests are disclosed rather than replaced with defaults. Fractional sensitivity shifts retain their numerical coordinates, and chart values use a single declared USD-thousands scale. A one-point line series uses a filled-circle marker at its saved coordinates, including when other series contain multiple points.

PDF/SVG rendering is optional and separate from calculation. Install its pinned dependencies locally, then render:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-report.txt
.\.venv\Scripts\python.exe tools/render_report.py --report outputs/reports/technical
```

The renderer writes `rendered/technical_report.pdf`, one SVG per chart in `document.json` and `rendered/render_manifest.json`, after checking report-source hashes and identities. Endpoint and conditional charts require priced scenarios of the corresponding kind; the government-bond approximation chart requires a government holding. It rejects existing output directories and rejects layout overflow when the actual PDF page count differs from the planned count. It records rendering dependencies and its own source hash. The Markdown links to these generated SVGs. Keep the source bundle and its `rendered` directory together. Fresh output directories are required for revisions.

The renderer is tested with ReportLab 4.4.9, Pillow 12.3.0 and charset-normalizer 3.5.2. These optional dependencies are pinned separately; the core package still has no third-party runtime dependency. PDF checks use locally available extraction/rendering tools and are recorded separately from rendering success. A generated PDF is not automatically a visually reviewed PDF.

All numerical report files are ignored local artifacts containing subscribed-data derivatives. Establish applicable provider rights before sharing numerical artifacts. The report's data-source limitations remain explicit even when every software check passes.

## Read the evidence

Use the report alongside the [code walkthrough](CODE_WALKTHROUGH.md), [limitations register](LIMITATIONS.md), [validation issue register](VALIDATION_ISSUES.md), [monitoring specification](MONITORING.md) and [actual validation status](VALIDATION_STATUS.md). The verification record distinguishes calculation checks from report-rendering checks. Future monitoring is specified, not deployed.

For the evaluated dataset, the selected AR(1)/no-change specifications do not contain macro predictors. The two invented conditional macro scenarios therefore produce identical market and P&L paths. The report discloses this result; no model is substituted to manufacture a difference. All macro challengers and window comparisons remain in the evaluation CSV and saved evaluation artifact.
