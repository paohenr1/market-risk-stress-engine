"""Saved, offline stress cycles with frozen validation selection and fixed holdings."""

from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import platform
import subprocess
import uuid

from market_risk.config import load_foundation
from market_risk.data import DataError, canonical, digest
from market_risk.evaluation import choose_models, validate_settings
from market_risk.models import (DATASET, TARGETS, _unique_macro_object, finite, fit_at_origin,
                               label, load_model_config, load_prepared, parse_quarter, project)
from market_risk.pricing import MarketState, load_portfolio
from market_risk.scenarios import factor_changes, load_scenarios
from market_risk.valuation import observed_baseline, price_scenarios, value_state, valuation_markdown


INPUTS = {"data/quarterly.csv", "data/manifest.json", "evaluation/evaluation.json",
          "evaluation/evaluation.md", "evaluation/manifest.json", "models.toml",
          "foundation.toml", "portfolio.json", "scenarios.json"}
ARTIFACTS = {"valuation.json", "valuation.md", "models.json", "events.json"}


def read_json(path: Path):
    try:
        return json.loads(path.read_bytes(), object_pairs_hook=_unique_macro_object)
    except (OSError, ValueError, TypeError):
        raise DataError("Cannot read valid saved-run JSON") from None


def source_identity() -> dict:
    """Hash executable source, including edits; record Git identity without file contents."""
    package = Path(__file__).resolve().parent
    root = package.parent.parent
    files = {p.relative_to(root).as_posix(): digest(p.read_bytes().replace(b"\r\n", b"\n")) for p in sorted(package.glob("*.py"))}
    files["pyproject.toml"] = digest((root / "pyproject.toml").read_bytes().replace(b"\r\n", b"\n"))
    identity = {"files": files, "sha256": digest(canonical(files)), "git_revision": None, "git_dirty": None}
    try:
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, check=True)
        identity.update(git_revision=revision.stdout.strip(), git_dirty=bool(status.stdout.strip()))
    except (OSError, subprocess.CalledProcessError):
        pass
    return identity


def environment() -> dict:
    return {"python_version": platform.python_version(), "implementation": platform.python_implementation(),
            "runtime_dependencies": [], "random_seed": None,
            "randomness": "none; deterministic fitting and pricing"}


def load_selection(folder: Path, prepared: dict, rows: list[dict], config, rules) -> dict:
    """Bind the saved validation decision to data/configuration, never to holdout scores."""
    result = read_json(folder / "evaluation.json")
    manifest = read_json(folder / "manifest.json")
    try:
        if (manifest["schema_version"] != 1
                or manifest["evaluation_sha256"] != digest((folder / "evaluation.json").read_bytes())
                or manifest["markdown_sha256"] != digest((folder / "evaluation.md").read_bytes())
                or result["schema_version"] != 1 or result["dataset_id"] != DATASET
                or result["prepared_csv_sha256"] != prepared["csv_sha256"]
                or canonical(result["model_config"]) != canonical(asdict(config))
                or canonical(result["evaluation_rules"]) != canonical(asdict(rules))):
            raise DataError("Evaluation artifact integrity or data/configuration binding mismatch")
        validate_settings(result["settings"], rules)
        start = len(rows) - rules.final_holdout_quarters - rules.minimum_validation_quarters
        stop = len(rows) - rules.final_holdout_quarters
        if start < 1 or (result["validation"]["start"], result["validation"]["end"]) != (label(rows[start]["date"]), label(rows[stop - 1]["date"])):
            raise DataError("Evaluation validation calendar does not match prepared data")
        chosen = choose_models(result["validation"], rules)
        if result["selection"] != chosen or result["selection_sha256"] != digest(canonical(chosen)):
            raise DataError("Saved selection differs from the validation-only decision")
        for target, choice in chosen.items():
            if choice["status"] == "selected":
                methods = {"no_change", "historical_mean", "ar1", "static"}
                if target == TARGETS[3]:
                    methods.add("dynamic")
                if choice["method"] not in methods or choice["window"] not in ("rolling", "expanding"):
                    raise DataError("Unknown selected model or training window")
        return result
    except (KeyError, TypeError, IndexError, ValueError) as exc:
        if isinstance(exc, DataError):
            raise
        raise DataError("Invalid saved evaluation or selection schema") from None


def anchored_changes(baseline: MarketState, observed: dict, projection_rows: list[dict]) -> tuple[list[dict], list[MarketState]]:
    """Anchor percent levels by observed-origin differences, with decimal pricing units."""
    changes = factor_changes(observed, projection_rows)
    gross = 1.0
    unanchored = []
    for row in projection_rows:
        gross *= 1 + row[TARGETS[0]] / 100
        state = MarketState(baseline.equity_price * gross, *(row[k] / 100 for k in TARGETS[1:]))
        state.validate()
        unanchored.append(state)
    return changes, unanchored


def integrated_calculation(inputs: Path, scenario_ids: list[str] | None = None) -> tuple[dict, dict]:
    rows, prepared = load_prepared(inputs / "data")
    config = load_model_config(inputs / "models.toml")
    foundation = load_foundation(inputs / "foundation.toml")
    spec = load_portfolio(inputs / "portfolio.json")
    definitions = load_scenarios(inputs / "scenarios.json")
    evaluation = load_selection(inputs / "evaluation", prepared, rows, config, foundation.evaluation)
    if scenario_ids is not None:
        if not scenario_ids or len(set(scenario_ids)) != len(scenario_ids) or set(scenario_ids) - {r["id"] for r in definitions}:
            raise DataError("Scenario IDs must be nonempty, unique and known")
        definitions = [r for r in definitions if r["id"] in scenario_ids]
    fitted = {}

    def adapter(record, baseline, positions, risks):
        if record["origin"] != spec.baseline_quarter:
            raise DataError("Conditional origin must match the declared portfolio baseline")
        if parse_quarter(record["origin"]) < parse_quarter(evaluation["validation"]["end"]):
            raise DataError("Conditional origin predates the frozen validation decision")
        _, origin = observed_baseline(rows, spec)
        unemployment = origin.get("unemployment_pct")
        if not finite(unemployment):
            raise DataError("Conditional scenario requires an observed unemployment level")
        implied = []
        for row in record["quarters"]:
            unemployment += row["unemployment_change_pp"]
            if not 0 <= unemployment <= 100:
                raise DataError("Conditional scenario implies an invalid unemployment level")
            implied.append(unemployment)
        forecasts, selected = {}, {}
        for target in TARGETS:
            choice = evaluation["selection"][target]
            if choice["status"] != "selected":
                raise DataError("Validation selection unavailable for a required target")
            key = (record["origin"], choice["window"])
            if key not in fitted:
                fitted[key] = fit_at_origin(rows, config, foundation.evaluation, parse_quarter(record["origin"]),
                    evaluation["settings"]["rolling_calendar_quarters"] if choice["window"] == "rolling" else None)
            bundle = fitted[key]
            candidates = [m for m in bundle["models"] if m["target"] == target and m["method"] == choice["method"] and m["status"] == "fitted"]
            if len(candidates) != 1:
                raise DataError("Selected specification cannot be fitted at the declared origin")
            model = candidates[0]
            selected[target] = {"window": choice["window"], "model": model,
                                "training_start": bundle["training_start"], "training_end": bundle["training_end"]}
            projection = project({**bundle, "models": candidates}, {"schema_version": 1, "dataset_id": DATASET,
                                  "origin": record["origin"], "quarters": record["quarters"]})
            forecasts[target] = projection["paths"][0]
        path_rows = [{"date": parse_quarter(r["quarter"]), **{t: forecasts[t]["values"][i]["value"] for t in TARGETS}}
                     for i, r in enumerate(record["quarters"])]
        changes, unanchored = anchored_changes(baseline, origin, path_rows)
        comparison = []
        for row, state in zip(record["quarters"], unanchored):
            value = value_state(positions, baseline, state, spec, foundation, risks)
            comparison.append({"quarter": row["quarter"], "market_state": asdict(state), "pnl_usd": value["pnl_usd"]})
        return changes, {"selected_models": selected, "projections": forecasts, "implied_unemployment_pct": implied,
                         "origin_state": {"quarter": record["origin"], "market_state": asdict(baseline), "pnl_usd": 0.0},
                         "anchoring": "baseline percent level + forecast percent level - observed origin percent level; equity compounds incremental returns",
                         "origin_model_mismatch": "No fitted-origin value is substituted; first forecast movement includes model mean reversion or intercept mismatch",
                         "unanchored_comparison": comparison}

    try:
        report = price_scenarios(rows, spec, definitions, foundation, adapter)
    except (OverflowError, ZeroDivisionError):
        raise DataError("Integrated calculation exceeds its numerical domain") from None
    report.update(prepared_csv_sha256=prepared["csv_sha256"],
                  snapshot_ids={k: prepared.get(k) for k in ("proxy_snapshot_id", "fred_snapshot_id", "fred_vintage_date")},
                  selection_sha256=evaluation["selection_sha256"], selection=evaluation["selection"],
                  tolerances={"absolute_usd": foundation.absolute_usd, "relative": foundation.relative})
    models = {f"{origin}:{window}": bundle for (origin, window), bundle in sorted(fitted.items())}
    return report, models


def save_run(data: Path, evaluation: Path, model_config: Path, foundation: Path, portfolio: Path,
             scenarios: Path, output: Path, scenario_ids: list[str] | None = None) -> dict:
    """Capture only explicit permitted inputs; never copy credentials or raw data folders."""
    sources = {"data/quarterly.csv": data / "quarterly.csv", "data/manifest.json": data / "manifest.json",
               **{f"evaluation/{n}": evaluation / n for n in ("evaluation.json", "evaluation.md", "manifest.json")},
               "models.toml": model_config, "foundation.toml": foundation, "portfolio.json": portfolio, "scenarios.json": scenarios}
    content = {name: path.read_bytes() for name, path in sources.items()}
    return write_run(content, output, scenario_ids)


def write_run(content: dict[str, bytes], output: Path, scenario_ids: list[str] | None,
              expected: tuple[dict, dict] | None = None, parent_run: str | None = None) -> dict:
    source = source_identity()
    run_id = uuid.uuid4().hex
    timestamp = datetime.now(timezone.utc).isoformat()
    output.mkdir(parents=True, exist_ok=False)
    for name, body in content.items():
        target = output / "inputs" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    report, models = integrated_calculation(output / "inputs", scenario_ids)
    # Compare persisted JSON contracts, where tuples are represented as arrays.
    report, models = json.loads(canonical(report)), json.loads(canonical(models))
    if expected is not None:
        assert_equivalent(expected[0], report, report["tolerances"])
        assert_equivalent(expected[1], models, report["tolerances"])
    if source_identity()["sha256"] != source["sha256"]:
        raise DataError("Executable source changed during the run")
    report["run_id"] = run_id
    (output / "valuation.json").write_bytes(canonical(report))
    (output / "models.json").write_bytes(canonical({"run_id": run_id, "fits": models}))
    (output / "valuation.md").write_text(f"Saved run: {run_id}\n\n" + valuation_markdown(report), encoding="utf-8")
    events = {"run_id": run_id, "events": ["inputs_captured", "input_integrity_verified", "validation_selection_verified",
              "fixed_time_pricing_completed", "numerical_reproduction_passed" if expected is not None else "initial_execution_completed"],
              "scenario_statuses": [{"id": r["id"], "status": r["status"]} for r in report["results"]]}
    (output / "events.json").write_bytes(canonical(events))
    names = {f"inputs/{name}" for name in INPUTS} | ARTIFACTS
    manifest = {"schema_version": 1, "run_id": run_id, "timestamp_utc": timestamp, "parent_run_id": parent_run,
                "use_scope": "local_internal_only", "source": source, "environment": environment(),
                "scenario_ids": scenario_ids, "files": {n: digest((output / n).read_bytes()) for n in sorted(names)},
                "checks": {"input_integrity": True, "validation_selection": True,
                           "zero_shock_identity": report["zero_shock_identity_passed"],
                           "offline_numerical_reproduction": True if expected is not None else None}}
    (output / "manifest.json").write_bytes(canonical(manifest))
    return {"status": "reproduced" if expected is not None else "completed", "run_id": run_id,
            "output": str(output.resolve()), "priced_scenarios": sum(r["status"] == "priced" for r in report["results"]),
            "unavailable_scenarios": sum(r["status"] == "unavailable" for r in report["results"]),
            "priced_states": sum(len(r.get("path", [])) for r in report["results"]),
            "manifest_sha256": digest(canonical(manifest))}


def assert_equivalent(expected, actual, tolerances: dict, key: str = "") -> None:
    """Compare every result and fit field; dollars use the portfolio USD tolerance."""
    if type(expected) is not type(actual):
        raise DataError("Offline reproduction result type differs")
    if isinstance(expected, dict):
        if expected.keys() != actual.keys():
            raise DataError("Offline reproduction result schema differs")
        for name in expected:
            assert_equivalent(expected[name], actual[name], tolerances, name if "usd" in name else key)
    elif isinstance(expected, list):
        if len(expected) != len(actual):
            raise DataError("Offline reproduction result length differs")
        for left, right in zip(expected, actual):
            assert_equivalent(left, right, tolerances, key)
    elif isinstance(expected, float):
        absolute = tolerances["absolute_usd"] if "usd" in key else 1e-12
        if not math.isfinite(expected) or not math.isfinite(actual) or abs(expected - actual) > max(absolute, tolerances["relative"] * max(abs(expected), abs(actual))):
            raise DataError("Offline reproduction numeric result differs")
    elif expected != actual:
        raise DataError("Offline reproduction result differs")


def load_saved_run(saved: Path, require_current_source: bool = True) -> tuple[dict, dict, dict]:
    """Verify a bundle; historical reporting may read an older executable version."""
    manifest = read_json(saved / "manifest.json")
    expected_names = {f"inputs/{name}" for name in INPUTS} | ARTIFACTS
    try:
        if (manifest["schema_version"] != 1 or set(manifest["files"]) != expected_names
                or (require_current_source and (manifest["source"]["sha256"] != source_identity()["sha256"]
                    or manifest["environment"] != environment()))):
            raise DataError("Saved run schema, source or runtime mismatch")
        if any(p.is_symlink() or getattr(p, "is_junction", lambda: False)() for p in saved.rglob("*")):
            raise DataError("Saved run must not contain links or junctions")
        actual_names = {p.relative_to(saved).as_posix() for p in saved.rglob("*") if p.is_file()}
        if actual_names != expected_names | {"manifest.json"}:
            raise DataError("Saved run has missing or unexpected files")
        if any(digest((saved / name).read_bytes()) != value for name, value in manifest["files"].items()):
            raise DataError("Saved run content hash mismatch")
        report = read_json(saved / "valuation.json")
        models = read_json(saved / "models.json")
        if report["run_id"] != manifest["run_id"] or models["run_id"] != manifest["run_id"]:
            raise DataError("Saved run artifact identity mismatch")
        return manifest, report, models
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, DataError):
            raise
        raise DataError("Invalid saved run manifest") from None


def reproduce_run(saved: Path, output: Path) -> dict:
    """Fail closed on changed source/runtime, altered artifacts or unexpected files."""
    manifest, report, models = load_saved_run(saved)
    report.pop("run_id")
    content = {n: (saved / "inputs" / n).read_bytes() for n in INPUTS}
    return write_run(content, output, manifest["scenario_ids"], (report, models["fits"]), manifest["run_id"])
