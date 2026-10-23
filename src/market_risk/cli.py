"""Small command interface; completed commands return machine-readable JSON.

Data acquisition and offline preparation are separate executable commands.
"""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

from market_risk import __version__
from market_risk.config import ConfigurationError, load_foundation
from market_risk.data import DataError, fetch_snapshot, load_data_config
from market_risk.preparation import prepare_snapshot
from market_risk.proxy import fetch_proxy, load_proxy_config, prepare_proxy
from market_risk.models import load_model_config, save_fit, save_projection
from market_risk.evaluation import load_evaluation_config, save_evaluation
from market_risk.scenarios import save_scenarios
from market_risk.pricing import load_portfolio
from market_risk.valuation import save_valuation
from market_risk.execution import save_run, reproduce_run
from market_risk.reporting import save_report


def main(argv: list[str] | None = None) -> int:
    """Execute a command; return 0 on success and 2 for invalid input."""
    parser = argparse.ArgumentParser(description="Market risk stress engine")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check-config", help="Validate foundation assumptions")
    check.add_argument("--config", type=Path, required=True, help="Foundation TOML file")
    fetch = commands.add_parser("fetch-data", help="Save an official FRED as-of snapshot")
    fetch.add_argument("--config", type=Path, required=True)
    fetch.add_argument("--output", type=Path, default=Path("data/raw/fred"))
    prepare = commands.add_parser("prepare-data", help="Recreate quarterly data from a verified saved snapshot")
    prepare.add_argument("--config", type=Path, required=True)
    prepare.add_argument("--snapshot", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    proxy_fetch = commands.add_parser("fetch-proxy", help="Save approved SPY/GZ inputs for local internal use")
    proxy_fetch.add_argument("--config", type=Path, required=True)
    proxy_fetch.add_argument("--output", type=Path, default=Path("data/raw/proxy"))
    proxy_prepare = commands.add_parser("prepare-proxy", help="Recreate labelled quarterly proxy data offline")
    proxy_prepare.add_argument("--config", type=Path, required=True)
    proxy_prepare.add_argument("--snapshot", type=Path, required=True)
    proxy_prepare.add_argument("--fred-config", type=Path, required=True)
    proxy_prepare.add_argument("--fred-snapshot", type=Path, required=True)
    proxy_prepare.add_argument("--output", type=Path, required=True)
    fit = commands.add_parser("fit-models", help="Fit local proxy candidates with reserved validation and holdout")
    fit.add_argument("--config", type=Path, required=True)
    fit.add_argument("--foundation", type=Path, required=True)
    fit.add_argument("--data", type=Path, required=True)
    fit.add_argument("--train-end", help="YYYYQn; may only move the cutoff earlier")
    fit.add_argument("--output", type=Path, required=True)
    projection = commands.add_parser("project-models", help="Project candidates under an explicit conditional macro path")
    projection.add_argument("--models", type=Path, required=True)
    projection.add_argument("--macro-path", type=Path, required=True)
    projection.add_argument("--output", type=Path, required=True)
    evaluation = commands.add_parser("evaluate-models", help="Chronological conditional reconstruction with frozen validation selection")
    evaluation.add_argument("--evaluation-config", type=Path, required=True)
    scenarios = commands.add_parser("construct-scenarios", help="Construct explicit proxy factor scenarios without pricing")
    scenarios.add_argument("--scenarios", type=Path, required=True)
    for command in (evaluation, scenarios):
        command.add_argument("--config", type=Path, required=True, help="Model TOML")
        command.add_argument("--foundation", type=Path, required=True)
        command.add_argument("--data", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
    pricing = commands.add_parser("price-scenarios", help="Fixed-time fictional portfolio replay/direct shocks and reconciled P&L")
    pricing.add_argument("--portfolio", type=Path, required=True)
    pricing.add_argument("--foundation", type=Path, required=True)
    pricing.add_argument("--data", type=Path, required=True)
    pricing.add_argument("--scenarios", type=Path, required=True)
    pricing.add_argument("--output", type=Path, required=True)
    cycle = commands.add_parser("run-stress", help="Save an integrated offline stress cycle with frozen validation selections")
    cycle.add_argument("--data", type=Path, required=True)
    cycle.add_argument("--evaluation", type=Path, required=True)
    cycle.add_argument("--config", type=Path, required=True, help="Model TOML")
    cycle.add_argument("--foundation", type=Path, required=True)
    cycle.add_argument("--portfolio", type=Path, required=True)
    cycle.add_argument("--scenarios", type=Path, required=True)
    cycle.add_argument("--scenario", action="append", help="Scenario ID; repeat for a batch; omit for all")
    cycle.add_argument("--output", type=Path, required=True)
    repeat = commands.add_parser("reproduce-run", help="Verify and rerun a saved cycle offline using identical executable source/runtime")
    repeat.add_argument("--run", type=Path, required=True)
    repeat.add_argument("--output", type=Path, required=True)
    report = commands.add_parser("generate-report", help="Generate run-bound technical report source and CSV exhibits locally")
    report.add_argument("--run", type=Path, required=True)
    report.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "check-config":
            config = load_foundation(args.config)
            result = {"status": "valid", "configuration": asdict(config)}
        elif args.command == "fetch-data":
            snapshot = fetch_snapshot(load_data_config(args.config), args.output)
            result = {"status": "saved", "snapshot": str(snapshot.resolve())}
        elif args.command == "prepare-data":
            report = prepare_snapshot(args.snapshot, load_data_config(args.config), args.output)
            result = {"status": "prepared", "report": report}
        elif args.command == "fetch-proxy":
            snapshot = fetch_proxy(load_proxy_config(args.config), args.output)
            result = {"status": "saved", "snapshot": str(snapshot.resolve())}
        elif args.command == "fit-models":
            result = save_fit(args.data, load_model_config(args.config), load_foundation(args.foundation).evaluation,
                              args.output, args.train_end)
        elif args.command == "project-models":
            result = save_projection(args.models, args.macro_path, args.output)
        elif args.command == "evaluate-models":
            rules = load_foundation(args.foundation).evaluation
            result = save_evaluation(args.data, load_model_config(args.config), rules,
                                     load_evaluation_config(args.evaluation_config, rules), args.output)
        elif args.command == "construct-scenarios":
            result = save_scenarios(args.scenarios, args.data, load_model_config(args.config),
                                    load_foundation(args.foundation).evaluation, args.output)
        elif args.command == "price-scenarios":
            result = save_valuation(args.data, load_portfolio(args.portfolio), args.scenarios,
                                    load_foundation(args.foundation), args.output)
        elif args.command == "run-stress":
            result = save_run(args.data, args.evaluation, args.config, args.foundation, args.portfolio,
                              args.scenarios, args.output, args.scenario)
        elif args.command == "reproduce-run":
            result = reproduce_run(args.run, args.output)
        elif args.command == "generate-report":
            result = save_report(args.run, args.output)
        else:
            report = prepare_proxy(args.snapshot, args.fred_snapshot, load_proxy_config(args.config),
                                   load_data_config(args.fred_config), args.output)
            # Restricted price observations and derived values never enter stdout.
            result = {"status": "prepared", "dataset_id": report["dataset_id"],
                      "output": str(args.output.resolve()), "calendar_quarters": report["calendar_quarters"],
                      "complete_proxy_quarters": report["complete_proxy_quarters"],
                      "csv_sha256": report["csv_sha256"], "original_index_ig_oas_ready": False}
    except (ConfigurationError, DataError) as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}), file=sys.stderr)
        return 2
    except OSError:
        print(json.dumps({"status": "invalid", "error": "Output unavailable or already exists"}), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0
