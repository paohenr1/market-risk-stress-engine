# Implemented scope and remaining work

The local research pipeline implements all seven stages below for the SPY/GZ proxy dataset. See [verification](VALIDATION_STATUS.md) for the evidence and its limits.

| Stage | Implemented capability | Method guide |
|---|---|---|
| Foundation | Configuration, schemas, units and domain checks | [Conventions](FOUNDATION.md) |
| Data | Official-source snapshots, integrity checks and calendar-safe proxy preparation | [Proxy data](PROXY_DATA.md) |
| Models | Benchmarks, dynamic credit regression and recursive projection | [Projection](PROJECTION_MODELS.md) |
| Evaluation | Chronological comparison, validation-only selection and scenario definitions | [Evaluation](SCENARIOS_AND_EVALUATION.md) |
| Pricing | Fixed-time fictional holdings, repricing, attribution and sensitivities | [Pricing](PRICING_AND_REPLAY.md) |
| Execution | Integrated batches, captured inputs and offline reproduction | [Saved runs](INTEGRATED_EXECUTION.md) |
| Reporting | Run-linked Markdown/CSV and optional PDF/SVG exhibits | [Reports](REPORTING.md) |

Remaining work includes obtaining permitted exact index/IG-OAS data, evaluating origin-specific information sets, challenging curve/credit approximations and carrying out independent financial validation. Dedicated static analysis remains unconfigured; [hosted CI results](https://github.com/paohenr1/market-risk-stress-engine/actions/workflows/ci.yml) are recorded separately from local verification. Monitoring is specified but not deployed. These are limitations or separate extensions, not functionality implied by completion of the local pipeline.
