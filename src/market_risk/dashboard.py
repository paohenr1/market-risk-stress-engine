"""Offline dashboard exports from verified saved runs, without refitting or repricing."""

import base64
import hashlib
from importlib.resources import files
import json
from pathlib import Path

from market_risk.data import DataError, canonical, digest
from market_risk.execution import load_saved_run, read_json
from market_risk.models import TARGETS


TARGET_LABELS = dict(zip(TARGETS, ("Equity price change", "2-year Treasury yield", "10-year Treasury yield", "GZ credit spread")))
STATE_FIELDS = (
    "quarter", "pnl_usd", "pnl_percent_baseline", "stressed_value_usd", "state_to_state_pnl_usd",
    "position_pnl_usd", "asset_class_pnl_usd", "factor_pnl_usd", "reverse_factor_pnl_usd",
    "isolated_interaction_residual_usd", "first_order_pnl_usd", "second_order_pnl_usd",
    "first_order_error_usd", "second_order_error_usd", "factor_changes", "checks",
)


def dashboard_payload(manifest: dict, valuation: dict, evaluation: dict, definitions: list[dict],
                      *, synthetic: bool = False) -> dict:
    """Project only the fields needed by the viewer; retain scenario identities and units."""
    definition_map = {r["id"]: r for r in definitions}
    scenarios = []
    for result in valuation["results"]:
        definition = definition_map[result["id"]]
        path = [{key: state[key] for key in STATE_FIELDS} for state in result.get("path", [])]
        if result["status"] == "priced" and not path:
            raise DataError("Priced dashboard scenario has no saved states")
        scenarios.append({
            "id": result["id"], "name": result["id"].replace("_", " ").capitalize(),
            "mode": definition["mode"], "status": result["status"], "reason": result.get("reason"),
            "origin": definition["origin"], "end": result.get("effective_end"),
            "horizon": definition["horizon_quarters"], "narrative": definition["narrative"],
            "macro_inputs": definition.get("quarters", []) if definition["mode"] == "conditional_projection" else [],
            "path": path,
        })
    choices = []
    for target in TARGETS:
        choice = evaluation["selection"][target]
        selected = choice["status"] == "selected"
        choices.append({"target": target, "label": TARGET_LABELS[target], "status": choice["status"],
                        "method": choice.get("method"), "window": choice.get("window"),
                        "observations": choice.get("validation_observations"),
                        "selection_mae": choice.get("selection_mae"),
                        "uses_macros": choice.get("method") in ("static", "dynamic") if selected else None})
    selected_choices = [r for r in choices if r["status"] == "selected"]
    conditional = [r for r in scenarios if r["mode"] == "conditional_projection" and r["status"] == "priced"]
    summary_fields = ("target", "method", "window", "horizon", "observations", "mae", "rmse", "bias", "error_units")
    paths = [state for result in scenarios for state in result["path"]]
    checks = [value for state in paths for value in state["checks"].values()]
    return {
        "schema_version": 1, "data_kind": "synthetic" if synthetic else "saved_results",
        "run": {"id": manifest["run_id"], "created_utc": manifest["timestamp_utc"],
                "source_sha256": manifest["source"]["sha256"],
                "python_version": manifest["environment"]["python_version"],
                "valuation_date": valuation["valuation_date"], "baseline_quarter": valuation["baseline_quarter"],
                "validation_start": evaluation["validation"]["start"], "validation_end": evaluation["validation"]["end"],
                "holdout_start": evaluation["holdout"]["start"], "holdout_end": evaluation["holdout"]["end"]},
        "baseline_value_usd": valuation["baseline_value_usd"],
        "positions": [{"id": p["id"], "kind": p["kind"], "weight": p["weight"]} for p in valuation["portfolio"]["positions"]],
        "scenarios": scenarios, "models": choices,
        "benchmarks": [{k: s.get(k) for k in summary_fields} for s in evaluation["validation"]["summaries"] if s["horizon"] == 1],
        "holdout": [{k: s.get(k) for k in summary_fields} for s in evaluation["selected_holdout_summaries"]],
        "findings": {
            "selected_models_use_macros": any(r["uses_macros"] for r in selected_choices) if selected_choices else None,
            "conditional_paths_identical": all(r["path"] == conditional[0]["path"] for r in conditional[1:]) if len(conditional) > 1 else None,
            "priced_scenarios": sum(r["status"] == "priced" for r in scenarios),
            "unavailable_scenarios": sum(r["status"] != "priced" for r in scenarios),
            "states": len(paths), "reconciliation_checks": len(checks),
            "reconciliation_failures": sum(value is not True for value in checks),
            "zero_shock_identity_passed": valuation["zero_shock_identity_passed"],
        }, "warnings": valuation["warnings"],
    }


def render_dashboard(payload: dict) -> bytes:
    """Embed escaped JSON and a hash-authorized local script; perform no network requests."""
    template = files("market_risk").joinpath("dashboard.html").read_text(encoding="utf-8")
    data = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    # JSON strings cannot terminate the non-executable data element or inject markup.
    data = data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    html = template.replace("__DASHBOARD_DATA__", data)
    script = html.split('<script id="dashboard-app">', 1)[1].split("</script>", 1)[0]
    script_hash = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    return html.replace("__SCRIPT_HASH__", script_hash).encode("utf-8")


def save_dashboard(saved: Path, output: Path, *, synthetic: bool = False) -> dict:
    """Verify a captured bundle and write a new self-contained local dashboard directory."""
    if output.resolve().is_relative_to(saved.resolve()):
        raise DataError("Dashboard output must be outside the saved run")
    manifest, valuation, _ = load_saved_run(saved, require_current_source=False)
    try:
        evaluation = read_json(saved / "inputs/evaluation/evaluation.json")
        definitions = read_json(saved / "inputs/scenarios.json")["scenarios"]
        payload = dashboard_payload(manifest, valuation, evaluation, definitions, synthetic=synthetic)
        body = render_dashboard(payload)
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        if isinstance(exc, DataError):
            raise
        raise DataError("Saved results cannot be displayed as a valid dashboard") from None
    output.mkdir(parents=True, exist_ok=False)
    (output / "index.html").write_bytes(body)
    receipt = {"schema_version": 1, "run_id": manifest["run_id"],
               "use_scope": "synthetic_demonstration" if synthetic else "local_internal_only",
               "saved_run_manifest_sha256": digest((saved / "manifest.json").read_bytes()),
               "saved_run_source_sha256": manifest["source"]["sha256"],
               "dashboard_code_sha256": digest(Path(__file__).read_bytes()),
               "template_sha256": digest(files("market_risk").joinpath("dashboard.html").read_bytes()),
               "files": {"index.html": digest(body)}, "checks": {"saved_run_integrity": True, "revaluation": False},
               "data_kind": payload["data_kind"]}
    (output / "dashboard_manifest.json").write_bytes(canonical(receipt))
    return {"status": "dashboard_generated", "run_id": manifest["run_id"], "output": str(output.resolve()),
            "scenarios": len(payload["scenarios"]), "states": payload["findings"]["states"],
            "html_sha256": digest(body)}
