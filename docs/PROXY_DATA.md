# Proxy data definitions

The executable dataset uses SPY split-only price changes plus the Federal Reserve's GZ nonfinancial corporate spread, aligned as quarterly averages of monthly states. It does not establish an exact equity-index/IG-OAS dataset or daily historical replay.

## Definitions and permitted use

[SPY](https://www.ssga.com/us/en/individual/etfs/state-street-spdr-sp-500-etf-trust-spy) is an ETF tracking the S&P 500. Its share price contains fund expenses, tracking differences and market/NAV deviations. Use Intrinio `close`, never `adj_close`: the latter adjusts for both dividends and splits. Query split events separately and apply only `split_ratio` to prices strictly before each split date. Intrinio's convention was verified against the public AAPL 2020-08-31 four-for-one event: its ratio is 0.25. SPY had no returned split events in the configured interval. Dividends are excluded. This is a price proxy, not an S&P 500 index series or realized total return.

The [Federal Reserve GZ methodology](https://www.federalreserve.gov/econresdata/notes/feds-notes/2016/recession-risk-and-the-excess-bond-premium-20160408.html) constructs spreads on bonds issued by nonfinancial firms relative to matching synthetic risk-free cash flows. Use `gz_spread`, not `ebp`: the excess bond premium removes the expected-default component and is not the full spread. The monthly series is not restricted to investment-grade bonds and is not the ICE aggregate IG OAS. Its link to pricing a fictional corporate instrument is an approximation that is disclosed in pricing and reporting.

The [official research-data catalog](https://www.federalreserve.gov/econres/economic-research-data.htm) links a CSV containing date, gz_spread, ebp and est_prob. The retrieved file spans 1973-01 through 2026-09 (645 monthly rows). It is a revised staff research product, not an official statistical release or origin-specific information set. The [update note](https://www.federalreserve.gov/econres/notes/feds-notes/updating-the-recession-risk-and-the-excess-bond-premium-20161006.html) reports a source-provider change beginning with the September 2026 release and retention of earlier observations; the configured analysis ends in June 2026.

Intrinio's [usage requirements](https://help.intrinio.com/licensing-data-usage-requirements) and [terms](https://docs.intrinio.com/terms) distinguish subscribed internal use from redistribution. Raw SPY observations, transformed price levels/returns, output CSVs and full local manifests stay in ignored local directories. No protected price values are printed by the proxy CLI or included in committed evidence. Public code and evidence contain contracts, counts, dates and hashes. Any later publication of data, charts or derived protected outputs requires the applicable rights.

## Alignment and missing observations

For each month, take the exact intersection of valid SPY, DGS2 and DGS10 dates. Compute all three daily-factor means from that same set. The configured quality rules require at least 15 common dates and exclude at most 10% of the union of valid dates across those three series. Preserve counts and first/last common dates. Union exclusions can arise from different market holidays/closures or source gaps; they are diagnostics, not automatic holiday classifications.

Every quarterly market state is the equal-weight mean of its three monthly means. All three months must pass. GZ is the mean of its three monthly reported spreads; it uses a separate bond panel, so exact daily identity with equity/Treasury observations cannot be claimed. The resulting time alignment is shared calendar months/quarters, not synchronized daily quotes.

Equity change is `100 * (quarter_average_price / previous_calendar_quarter_average_price - 1)`. It describes changes between average states, not a buy-at-quarter-end holding-period return. The previous calendar quarter must exist and be complete: do not skip missing quarters. GDP, unemployment, CPI and policy transformations retain the existing definitions and preserve their missing calendar rows.

The June 2026 baseline is an illustrative average-period state, not observed simultaneous June 30 market quotes. Projection and pricing identify this average state separately from the fixed valuation date and disclose the mapping. Original daily endpoint conventions remain available for the separate official six-series track; they are not applied silently to this proxy track.

## Commands and reproduction

```powershell
.\.venv\Scripts\python.exe -m market_risk fetch-proxy --config configs/proxy.toml
.\.venv\Scripts\python.exe -m market_risk prepare-proxy --config configs/proxy.toml --snapshot <proxy-snapshot> --fred-config configs/data.toml --fred-snapshot <fred-snapshot> --output outputs/proxy/new-run
```

Acquisition uses the locally configured Intrinio key and the official Fed CSV. It validates completed daily SPY observations, USD/security identity, pagination cursors, duplicates, split events, positive finite closes, GZ schema and monthly labels. Snapshot manifests bind exact payload hashes to configuration, sources, retrieval time and units. Preparation is offline and rejects changed inputs or existing output directories.

Actual preparation retained 134 calendar quarters (1993Q1–2026Q2), with 128 complete proxy rows and 56 complete rows before 2008. These counts precede model-specific lag and partition losses. The 2008Q3/Q4, 2020Q1, 2022Q1 and 2026Q2 states are complete. Initial SPY launch coverage makes 1993Q1 incomplete and its return lag makes 1993Q2 incomplete. September 2001 fails the fixed overlap threshold, making 2001Q3 and its subsequent return lag incomplete. The existing missing 2025Q4 macro inputs and 2026Q1 unemployment lag remain unavailable. No threshold was relaxed to rescue those observations.

Two offline runs produced identical CSV and manifest bytes. CSV SHA-256: `aaa37529da35a93850e013af155634d049c2d89e9a04579a0dcde2744a2176b3`. See [source coverage](evidence/source-coverage.json). `original_index_ig_oas_ready` remains false; `proxy_factors_complete` identifies row-level proxy availability. Counts do not prove forecasting accuracy, eligible rolling-origin samples, or model validity.
