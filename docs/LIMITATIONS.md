# Limitations register

These limitations remain open by design or because evidence is unavailable. A successful software check does not close them.

| ID | Limitation | Consequence | Required challenge or future work |
|---|---|---|---|
| L01 | SPY split-only price proxy | ETF tracking/expense/NAV effects; dividends excluded; not index total return | Acquire the intended index series under appropriate rights before making index claims |
| L02 | GZ nonfinancial corporate spread | Not investment-grade-only OAS; not the EBP component | Obtain and define the intended IG-OAS dataset before changing that label |
| L03 | Quarterly-average states | Not simultaneous quotes or executable quarter-end returns | A separate endpoint-data track and alignment review are required for holding-period claims |
| L04 | Revised data and realized macro conditions | Conditional reconstruction does not establish historical release-time forecasting | Add release calendars and origin-specific vintages for a forecasting exercise |
| L05 | Small, fixed model families | Misspecification, unstable coefficients and extrapolation remain possible | Challenge diagnostics and benchmark/window/horizon results without selecting on holdout |
| L06 | Selected specifications omit macros in the accepted run | Invented recession/stagflation paths coincide despite differing macro conditions | Report the outcome; any alternative selection objective requires a declared new experiment |
| L07 | Two-node par-as-zero proxy with flat extrapolation | Compounding conversion is not bootstrapping; maturity regions are poorly identified | Compare with a fuller calibrated curve and realistic instrument prices |
| L08 | Flat continuous GZ margin on one fictional corporate bond | No issuer/default/recovery or OAS calibration | Use issuer/sector/maturity credit inputs and a declared credit valuation model |
| L09 | Fixed time and holdings | P&L excludes coupons, dividends, maturity, reinvestment and rebalancing | Implement a separate time-passing return engine before total-return claims |
| L10 | Sequential nonlinear attribution | Individual factor allocations depend on order | Retain reverse-order results and isolated interaction residuals |
| L11 | Descriptive error quantiles and curve overlays | Neither supplies a calibrated prediction or pricing confidence interval | Calibrate uncertainty under explicit assumptions and dependency treatment |
| L12 | Defaults, liquidity, counterparty, funding, optionality and management actions omitted | Portfolio risk coverage is incomplete | Extend only with explicit scope, data and tests |
| L13 | No scenario probabilities | No expected loss, probability ranking or regulatory capital result | A separate probability model and validation would be needed |
| L14 | Saved source/runtime required for numerical reproduction | No container or cross-runtime equivalence guarantee | Retain matching source/runtime; version upgrades require new verification |
| L15 | Provider rights are separate from local access | Numerical inputs, charts and derived reports are not public deliverables by default | Establish redistribution rights before distribution |
| L16 | Software verification is distinct from financial validation | Tests and CI do not establish independent financial validation or operational fitness | Independent challenge and governance are required before operational use |
