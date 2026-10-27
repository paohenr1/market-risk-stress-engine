# Market Risk Variable Projection and Portfolio Stress Testing

A Python research pipeline for studying market-variable projections and fixed-time portfolio stress tests. It is intended for researchers and readers inspecting reproducible financial software. The pipeline prepares source snapshots, compares models chronologically, constructs scenarios, reprices a fictional USD portfolio and generates reports from saved runs.

The implemented dataset uses SPY split-only price changes and the Federal Reserve's GZ nonfinancial corporate spread, together with FRED macroeconomic and Treasury data. SPY is an ETF price proxy; GZ is not investment-grade-only option-adjusted spread. Quarterly-average market states are not simultaneous quotes or realized holding-period returns. The originally intended equity-index/IG-OAS dataset remains unavailable.

## Synthetic demonstration

Explore scenario comparisons, portfolio attribution and model evaluation in the [synthetic dashboard](https://paohenr1.github.io/market-risk-stress-engine/demo/). All inputs and results in the demo are fictional. It demonstrates software behavior, not performance on real market data. See [how the demo is generated](docs/DEMO.md) for offline use and reproduction.

## Install and test

From the repository root in PowerShell, with Python 3.11 or newer:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --no-deps -r requirements-bootstrap.txt
.\.venv\Scripts\python.exe -m pip install --no-build-isolation --no-deps -e .
.\.venv\Scripts\python.exe -m market_risk --version
.\.venv\Scripts\python.exe -m market_risk check-config --config configs/foundation.toml
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The calculation engine uses only the Python standard library. Build tooling is pinned separately. The recorded local verification used Python 3.14.8. Tests use fictional fixtures and need no API keys or subscribed data. Three renderer tests are skipped unless the optional report dependencies are installed. See [verification](docs/VALIDATION_STATUS.md) for the tested scope and remaining gaps.

## Automated checks

The [CI workflow](.github/workflows/ci.yml) runs Python 3.11–3.14 on Ubuntu 24.04 and Windows 2025 for pushes to `main`, pull requests targeting `main` and manual runs. Each job installs the pinned optional renderer, checks dependencies and CLI configuration, runs the tests without accepting skips, and compiles the Python modules. It uses fictional fixtures and requires no data-provider credentials or subscribed inputs. Repository access is read-only, and no test artifacts are uploaded. See [GitHub Actions](https://github.com/paohenr1/market-risk-stress-engine/actions/workflows/ci.yml) for hosted results; a workflow definition alone is not evidence of a passing run.

## Acquire and prepare data

The empirical workflow requires a FRED API key and appropriate Intrinio access. Read [data acquisition](docs/DATA_PIPELINE.md) and [proxy definitions and preparation](docs/PROXY_DATA.md) before running it. Access to an API does not establish permission to redistribute its data. Credentials, source snapshots and numerical outputs are not included in the repository.

```powershell
.\.venv\Scripts\python.exe -m market_risk fetch-data --config configs/data.toml
.\.venv\Scripts\python.exe -m market_risk fetch-proxy --config configs/proxy.toml
```

Each acquisition returns a snapshot location. Pass those locations to `prepare-proxy`, as shown in the [preparation walkthrough](docs/PROXY_DATA.md#commands-and-reproduction), and use `outputs/proxy/checked` as the prepared output directory for the commands below. Preparation and subsequent calculations work offline from saved inputs.

## Evaluate, stress and reproduce

Use new output directories for repeat executions. From the repository root:

```powershell
.\.venv\Scripts\python.exe -m market_risk fit-models --config configs/models.toml --foundation configs/foundation.toml --data outputs/proxy/checked --output outputs/models/research
.\.venv\Scripts\python.exe -m market_risk evaluate-models --config configs/models.toml --foundation configs/foundation.toml --evaluation-config configs/evaluation.toml --data outputs/proxy/checked --output outputs/evaluation/research
.\.venv\Scripts\python.exe -m market_risk run-stress --data outputs/proxy/checked --evaluation outputs/evaluation/research --config configs/models.toml --foundation configs/foundation.toml --portfolio configs/portfolio.json --scenarios configs/scenarios.json --output outputs/runs/research
.\.venv\Scripts\python.exe -m market_risk reproduce-run --run outputs/runs/research --output outputs/runs/research-repeat
.\.venv\Scripts\python.exe -m market_risk generate-report --run outputs/runs/research --output outputs/reports/research
```

Model choices use validation errors and are frozen before holdout comparison. Historical, conditional and direct scenarios use the same pricing functions. Saved runs capture inputs, configuration, source/runtime identity and hashes; reproduction checks their numerical agreement. Hashes detect changes but do not independently authenticate a provider.

For PDF and SVG output, install the optional renderer and run:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-report.txt
.\.venv\Scripts\python.exe tools/render_report.py --report outputs/reports/research
```

The PDF is `outputs/reports/research/rendered/technical_report.pdf`. Reports, charts and other numerical derivatives remain under ignored local directories and require applicable redistribution rights before sharing.

## Interactive dashboard

Generate a standalone HTML dashboard from a saved run. No API calls, model fitting or repricing are performed:

```powershell
.\.venv\Scripts\python.exe -m market_risk generate-dashboard --run outputs/runs/research --output outputs/dashboards/research
```

Open `outputs/dashboards/research/index.html` in a modern browser. Use a new output directory for each export. The dashboard provides scenario comparisons, a quarter inspector, holding and factor attribution, model selection and validation summaries. It explicitly identifies selected models that omit macro predictors and conditional paths that coincide.

The viewer verifies the saved bundle and records its identity in `dashboard_manifest.json`. It can display archived runs from an earlier source version; displaying a run does not reproduce its calculations or independently validate the model. The HTML embeds numerical results, works offline and remains a local derivative subject to the source data's redistribution terms.

## Methods and limitations

Start with the [code walkthrough](docs/CODE_WALKTHROUGH.md), then read the guides for [data](docs/PROXY_DATA.md), [models](docs/PROJECTION_MODELS.md), [evaluation and scenarios](docs/SCENARIOS_AND_EVALUATION.md), [pricing](docs/PRICING_AND_REPLAY.md), [saved runs](docs/INTEGRATED_EXECUTION.md) and [reporting](docs/REPORTING.md). The [specification](SPECIFICATION.md) records the research scope and acceptance criteria.

The study conditions on revised data and supplied future macro paths; it does not establish real-time forecast accuracy. In the evaluated dataset, selected AR(1)/no-change models omit macro predictors, so the recession and stagflation illustrations produce identical conditional paths. Pricing uses a two-node par-as-zero approximation, fixed time and fixed holdings. See the [limitations register](docs/LIMITATIONS.md) for the full boundaries. Software checks do not establish independent financial validation, regulatory compliance or production readiness.

## License

The project source code and original documentation are provided under the [MIT License](LICENSE). Third-party data and dependencies remain subject to their respective terms. API access does not grant redistribution rights; source snapshots and numerical derivatives are excluded from this repository.
