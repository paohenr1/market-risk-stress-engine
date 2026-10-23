# Data acquisition and preparation

The official FRED acquisition and offline preparation path is implemented. The original index/IG-OAS dataset is **not ready**. The implemented [SPY/GZ proxy track](PROXY_DATA.md) uses explicit monthly-average conventions. Modeling and pricing use that separate dataset.

## Executable walkthrough

Set `FRED_API_KEY` in the local environment, or run `tools/verify_fred_key.py --prompt --save` in an interactive terminal. Never put credentials in TOML, command arguments, source files, or chat.

```powershell
.\.venv\Scripts\python.exe -m market_risk fetch-data --config configs/data.toml
```

The returned absolute snapshot directory is the input to preparation:

```powershell
.\.venv\Scripts\python.exe -m market_risk prepare-data --config configs/data.toml --snapshot <snapshot-directory> --output outputs/data/first
.\.venv\Scripts\python.exe -m market_risk prepare-data --config configs/data.toml --snapshot <snapshot-directory> --output outputs/data/reproduced
```

Preparation requires no network or key. Both runs produce identical `quarterly.csv` bytes and manifests when run with the same code/runtime. Existing output directories are rejected to preserve previous evidence. Raw inputs are ignored by Git; prepared artifacts are under ignored `outputs/`. Keep the saved inputs with the results for actual offline reproduction.

## Source and temporal contracts

The six government-series allowlist is GDPC1, UNRATE, CPIAUCSL, FEDFUNDS, DGS2 and DGS10. The adapter reads metadata and paginated native observations from the [official FRED API](https://fred.stlouisfed.org/docs/api/fred/series_observations.html). It rejects changed native frequency, units or seasonal adjustment, duplicate/unsorted dates, nonfinite values, invalid GDP/CPI levels, period-label errors and observations outside the configured period or vintage. HTTP diagnostics do not print URLs or exception bodies, because FRED's authentication parameter is secret.

Each request sets `realtime_start = realtime_end = vintage_date`, with `units=lin`; transformations happen locally. The snapshot retains metadata and returned pages, a UTC retrieval time, requested period/vintage, and SHA-256 hashes. Its directory name hashes its manifest. Loading verifies the manifest and files before preparing data. These hashes detect accidental changes; they are not signatures proving provider authenticity.

This is one as-of database view used for **retrospective conditioning**. FRED observation dates identify periods, and real-time validity fields identify the database vintage. Neither is assumed to be the original release timestamp. A single 2026 vintage cannot establish what was known at each historical forecast origin. Historical information-set evaluation will require origin-specific vintage retrieval and release-availability checks as a separate extension.

## Preparation rules

All complete calendar quarters within the observation interval remain in the CSV. Missing values stay blank and produce named diagnostics; no forward/backfill occurs. GDP growth uses the exact preceding calendar quarter. Monthly unemployment, CPI and policy rates require all three months. CPI inflation compares quarterly means four calendar quarters apart. Unemployment change uses the preceding calendar quarter; it cannot jump over an incomplete quarter.

Treasury endpoints use the last common valid DGS2/DGS10 date on or before quarter-end, within the configured maximum of five calendar days. Missing endpoints remain unavailable. These dates align only the two currently configured yield factors. This six-series output alone cannot support full-factor replay; use the separately aligned proxy preparation for that purpose. The CSV explicitly sets `full_market_dataset_ready=False`.

Prepared manifests include explicit output units, input/config/CSV/code hashes, package/runtime versions, missing counts, completeness diagnostics and limitations. Rates remain in native percent units; pricing converts them to decimal rates at its input boundary.

## Intrinio access and source coverage

`tools/verify_intrinio_key.py --prompt --save` checks the official account access endpoint using a Bearer header and saves the key only in the Windows user environment. Local entitlement evidence is ignored by Git and excludes account/email fields. Set `INTRINIO_API_KEY` in the local environment before authenticated acquisition. Entitlements depend on the configured account.

`tools/probe_intrinio.py` checks five small windows through the [official historical-index endpoint](https://docs.intrinio.com/documentation/web_api/get_stock_market_index_historical_data_v2). It preserves source nulls and writes only counts/date coverage, not index levels. The accessible `$SPX` metadata starts on 2016-10-10. The 2000Q1 and 2008Q3 probes were empty; 2020Q1, 2022Q1 and 2026Q2 each contained 62 valid observations. This is window evidence, not continuous-history verification or proof of snapshot/redistribution rights. The separate `/indices/SPX/eod` endpoint returned HTTP 403 for all five windows during the initial check.

The original index/IG-OAS targets remain subject to the [source constraints](DATA_FEASIBILITY.md#equity-and-credit-constraints). The subsequent source decision and local snapshot restrictions are recorded in the [proxy track](PROXY_DATA.md). Account access alone does not establish dataset suitability or redistribution rights.

Additional metadata checks found no dated coverage for `$NYA`; `$WILL5000PRFC` identifies a price-return series from 1970-12-31 to 2017-05-25. That older Wilshire series is a possible separately labelled historical proxy, but does not supply a continuous same-index dataset through the current baseline. No splice between different indexes was performed, and its reuse terms and actual observations remain unverified.
