# Data feasibility — October 9, 2026

## Verified official exports

The October 9, 2026 coverage probe retrieved six key-free FRED CSV exports. Machine-readable evidence, hashes, UTC timestamps and local snapshot paths are in [source coverage](evidence/source-coverage.json). Snapshots are local and excluded from Git. Definitions and source attribution were checked against official FRED series pages.

| Series and originating source | Native frequency / units | First–last valid period/date | Rows / missing values | Raw pre-2008 quarters with any observation |
|---|---|---|---|---|
| [GDPC1](https://fred.stlouisfed.org/series/GDPC1), BEA | Quarterly; billions of chained 2017 dollars, SAAR | 1947-01-01–2026-04-01 | 318 / 0 | 244 |
| [UNRATE](https://fred.stlouisfed.org/series/UNRATE), BLS | Monthly; percent, seasonally adjusted | 1948-01-01–2026-09-01 | 945 / 1 | 240 |
| [CPIAUCSL](https://fred.stlouisfed.org/series/CPIAUCSL), BLS | Monthly; index 1982–1984=100, seasonally adjusted | 1947-01-01–2026-08-01 | 956 / 1 | 244 |
| [FEDFUNDS](https://fred.stlouisfed.org/series/FEDFUNDS), Federal Reserve Board | Monthly; percent, daily averages | 1954-07-01–2026-09-01 | 867 / 0 | 214 |
| [DGS2](https://fred.stlouisfed.org/series/DGS2), Federal Reserve Board | Daily; percent, investment-basis constant-maturity yield | 1976-06-01–2026-10-07 | 13,137 / 552 | 127 |
| [DGS10](https://fred.stlouisfed.org/series/DGS10), Federal Reserve Board | Daily; percent, investment-basis constant-maturity yield | 1962-01-02–2026-10-07 | 16,897 / 720 | 184 |

Period dates are observation labels. For example, 2026-04-01 labels GDP for Q2 2026, not a GDP release on April 1. These exports contain current revised data, not historical vintages. FRED marks these six series as public domain with citation requested; retain attribution. Public accessibility of other FRED series does not imply public-domain rights.

**Missing monthly values found:** UNRATE and CPIAUCSL both have a missing value for 2025-10-01. See [preparation rules](DATA_PIPELINE.md#preparation-rules) for retention of incomplete calendar quarters and protection of calendar lags. These observations remain missing; preparation does not infer replacements. Daily Treasury missing values require calendar-aware classification rather than automatic filling.

The six raw series overlap from June 1976 through Q2 2026 when considering the GDP endpoint. A conservative candidate span for complete quarterly preparation begins 1976 Q3 and ends 2026 Q2, subject to missing quarters, lag losses and daily endpoint checks. These export-probe counts are not an aligned dataset; the full proxy preparation coverage is recorded separately in [proxy data](PROXY_DATA.md). Adding equity and spread series can shorten the sample substantially. Counts above are marginal coverage, not usable training observations, out-of-sample evidence, or release-time availability.

## Equity and credit constraints

These constraints record the original equity-index/IG-OAS source assessment as of October 9, 2026. Provider access and terms must be rechecked before new acquisition or distribution. The implementation uses the separate [SPY/GZ proxy track](PROXY_DATA.md); its authentic local inputs, alignment rules and actual coverage are recorded separately. The original targets are still unavailable and are not silently renamed as proxies.

| Candidate | Verified limitation | Decision / fallback |
|---|---|---|
| [FRED SP500](https://fred.stlouisfed.org/series/SP500) | Price index excludes dividends; ten-year daily-history limit; reproduction restrictions | Suitable candidate for recent local price-return diagnostics, not 2008 replay or long-history estimation. Do not redistribute snapshots without established permission. |
| [FRED BAMLC0A0CM](https://fred.stlouisfed.org/series/BAMLC0A0CM) | Investment-grade aggregate OAS; series notes say only three years of observations from April 2026; internal-use and publication restrictions | Preferred target definition, but insufficient for the predeclared 40-quarter training minimum via this endpoint. Historical evaluation requires another permitted authentic input. Do not download/publish restricted snapshots as though unrestricted. |
| [BAA10Y](https://fred.stlouisfed.org/series/BAA10Y) | Baa corporate-yield minus 10y Treasury yield; proprietary input and redistribution/storage restrictions | Potential user-supplied permitted-history proxy. Not broad IG OAS; includes maturity/composition differences. Coverage not measured and rights not established for project publication. |
| [Robert Shiller's data](https://shillerdata.com/) | Broad US historical price data are monthly averages of daily closes rather than daily endpoints | Candidate long-history academic price source. File coverage and permitted reuse remain to be verified. A monthly-average reconstruction must use matched averaging across factors and be labeled distinctly; never substitute it into daily endpoint replay silently. |

Do not substitute NASDAQ Composite for broad US equity exposure without an explicit change of economic scope. Do not substitute a total-return series for price-return valuation unless dividends and valuation conventions are changed consistently.

### Fallback order

1. Official/source-provided historical records with verified permitted use and the required definition/frequency.
2. Authentic locally supplied records with provenance and adequate rights, using the same validated input contract.
3. An economically documented proxy, with separate model identity, diagnostic labels and revised coverage assessment. Do not concatenate unlike definitions silently.
4. Mark unsupported historical factor/scenario/model evaluations unavailable. If necessary, supply a clearly labeled synthetic demonstration separately; never present it as historical evidence.

The original index/IG-OAS targets remain unavailable. Historical replay and model evaluation are implemented separately for the [proxy track](PROXY_DATA.md).

## Official access policy

Use the [FRED series API](https://fred.stlouisfed.org/docs/api/fred/series.html) for metadata and [observations API](https://fred.stlouisfed.org/docs/api/fred/series_observations.html) for values when a registered key is configured. Read the key from the environment, never logs or committed configuration. The API requires a registered key; the documentation's example key is not a usable credential. Configure a local registered key before authenticated acquisition. The earlier access check is not a guarantee of current account access. The earlier CSV snapshots are revised data, not vintage-aware API inputs.

Without a key, the official public CSV export provides the six revised-data snapshots used here. Record source pages for definitions and the retrieval UTC timestamp for audit. Evaluate [vintage-date support](https://fred.stlouisfed.org/docs/api/fred/series_vintagedates.html) before claiming information was available historically. Retrieval time and observation time are different fields.

The acquisition workflow uses official structured sources. It does not require web scraping.
