"""Allow the same command interface through ``python -m market_risk``."""

from market_risk.cli import main

raise SystemExit(main())
