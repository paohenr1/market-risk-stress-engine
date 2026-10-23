# Verification results

Verification covers software behavior and reproducibility. It does not establish independent financial validation, forecasting improvement or operational fitness. The [verification summary](evidence/validation-summary.json) records the checked versions and scope. The empirical artifacts themselves are excluded because they contain subscribed-data derivatives.

## Local test suite

On October 23, 2026, all 91 tests passed without skips using Python 3.14.8 and the optional pinned report renderer. Tests exercise fictional inputs, analytical bond references, chronological boundaries, missing observations, domain rejection, recursive projections, attribution, saved-run reproduction, report binding and integrity guards.

Run from the repository root after installation:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m compileall -q src tools tests
git diff --check
```

The renderer requires `requirements-report.txt`; otherwise three optional renderer tests are skipped. A skipped test is not a pass. The calculation engine has no third-party runtime dependencies. The installed environment passed its dependency check. The documented module commands worked; Windows Application Control blocked the generated executable launcher, so examples use `python.exe -m market_risk`. Fresh installation and other supported Python versions were not reverified during repository cleanup.

## Review and empirical reproduction

The completed implementation received local automated code review and separate focused tests/live CLI checks. The verification included all-mode stress execution, individual/direct-only exports, offline reproduction, deterministic reports and integrity guards. Documentation and Git whitespace checks also passed. The independent test phase was focused verification, not a second full-suite run. The reviewed implementation was `bcb0afe4104f928edfefd18116566add0056cc2c`; the completed verification head was `76c563c71a7efe557644695bf16c05b3fd105b9d`. These results precede the documentation-only repository cleanup and are not a review of later edits.

Saved empirical inputs produced six priced scenarios and 29 states with successful offline reproduction. The default report contains fourteen pages, seven CSV exhibits and three charts. Hashes and output counts are retained as integrity evidence; the inputs, coefficients, forecast errors, P&L and numerical exhibits remain local. Reproducing empirical results requires the same permitted inputs and matching source/runtime, not only this source repository.

PDF geometry and value binding were checked programmatically. Redacted actual-data page layouts and unrestricted synthetic charts were visually inspected. Protected numerical text and actual-data plotted geometry were not visually read. Rendering success alone is not a complete visual-review claim.

## Remaining gaps

- Source snapshots and derived numerical artifacts are excluded from this repository. Pattern-based credential checks are not a complete security audit.
- Remote CI has not run; push and pull-request stages were skipped.
- Git whitespace checks are not dedicated static analysis.
- The exact equity-index/IG-OAS dataset remains unavailable.
- Revised-data conditioning does not establish historical release-time forecasting accuracy.
- The selected models omit macros, so the two conditional illustrations coincide.
- Independent financial validation, operational deployment and calibrated scenario probabilities are not established.

See [limitations](LIMITATIONS.md) and [validation issues](VALIDATION_ISSUES.md) for the consequences and possible extensions.
