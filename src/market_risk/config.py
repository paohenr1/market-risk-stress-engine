"""Read the foundation contract without mixing configuration with financial logic.

TOML keeps editable assumptions readable. Frozen dataclasses are explicit records:
callers receive validated values rather than an unstructured dictionary.
"""

from dataclasses import dataclass
import math
from pathlib import Path
import tomllib


class ConfigurationError(ValueError):
    """A supplied configuration cannot be interpreted under schema version 1."""


@dataclass(frozen=True)
class EvaluationRules:
    """Minimum sample counts and recursive evaluation horizons, all in quarters."""

    minimum_training_quarters: int
    minimum_validation_quarters: int
    final_holdout_quarters: int
    horizons_quarters: tuple[int, ...]

    def validate_counts(self) -> None:
        for name, minimum in (("minimum_training_quarters", 40),
                              ("minimum_validation_quarters", 12),
                              ("final_holdout_quarters", 20)):
            value = getattr(self, name)
            if type(value) is not int or value < minimum:
                raise ConfigurationError(f"{name} must be an integer >= {minimum}")


@dataclass(frozen=True)
class FoundationConfig:
    """Validated project conventions and numerical reconciliation tolerances."""

    schema_version: int
    project_name: str
    currency: str
    valuation_mode: str
    equity_return_convention: str
    data_access_policy: str
    firecrawl_requires_credit_approval: bool
    evaluation: EvaluationRules
    absolute_usd: float
    relative: float


def _require_keys(record: dict, expected: set[str], context: str) -> None:
    """Reject missing and unknown fields so misspelled assumptions never go unused."""
    if not isinstance(record, dict):
        raise ConfigurationError(f"{context} must be a TOML table")
    missing = expected - record.keys()
    unknown = record.keys() - expected
    if missing or unknown:
        raise ConfigurationError(
            f"{context}: missing fields {sorted(missing)}; unknown fields {sorted(unknown)}"
        )


def _positive_integer(value: object, label: str) -> int:
    # bool is a subclass of int in Python; exact type checking excludes true/false.
    if type(value) is not int or value <= 0:
        raise ConfigurationError(f"{label} must be a positive integer")
    return value


def _positive_finite(value: object, label: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ConfigurationError(f"{label} must be a positive finite number")
    return float(value)


def load_foundation(path: Path) -> FoundationConfig:
    """Load UTF-8 TOML; raise ConfigurationError for unreadable or invalid inputs.

    This validates only the foundation schema. Scenario, portfolio and market-state
    schemas will be implemented with their corresponding project increments.
    """
    try:
        with path.open("rb") as stream:
            raw = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"Cannot read configuration: {exc}") from exc

    _require_keys(raw, {
        "schema_version", "project_name", "currency", "valuation_mode",
        "equity_return_convention", "data_access_policy",
        "firecrawl_requires_credit_approval", "evaluation", "tolerances",
    }, "foundation")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise ConfigurationError("Only schema_version = 1 is supported")
    if not isinstance(raw["project_name"], str) or not raw["project_name"].strip():
        raise ConfigurationError("project_name must be a nonempty string")
    conventions = {
        "currency": "USD", "valuation_mode": "fixed_time",
        "equity_return_convention": "price_simple",
        "data_access_policy": "official_structured_first",
    }
    for key, required in conventions.items():
        if raw[key] != required:
            raise ConfigurationError(f"{key} must be {required!r} for version 1")
    if raw["firecrawl_requires_credit_approval"] is not True:
        raise ConfigurationError("Firecrawl credit consumption requires explicit approval")

    evaluation = raw["evaluation"]
    counts = {
        "minimum_training_quarters", "minimum_validation_quarters", "final_holdout_quarters"
    }
    _require_keys(evaluation, counts | {"horizons_quarters"}, "evaluation")
    horizons = evaluation["horizons_quarters"]
    if not isinstance(horizons, list) or not horizons:
        raise ConfigurationError("horizons_quarters must be a nonempty array")
    for horizon in horizons:
        _positive_integer(horizon, "horizon")
    if horizons != sorted(set(horizons)):
        raise ConfigurationError("horizons_quarters must be increasing and unique")

    rules = EvaluationRules(
        **{key: evaluation[key] for key in sorted(counts)},
        horizons_quarters=tuple(horizons),
    )
    rules.validate_counts()

    tolerances = raw["tolerances"]
    _require_keys(tolerances, {"absolute_usd", "relative"}, "tolerances")
    return FoundationConfig(
        **{key: raw[key] for key in (
            "schema_version", "project_name", "currency", "valuation_mode",
            "equity_return_convention", "data_access_policy",
            "firecrawl_requires_credit_approval",
        )},
        evaluation=rules,
        absolute_usd=_positive_finite(tolerances["absolute_usd"], "absolute_usd"),
        relative=_positive_finite(tolerances["relative"], "relative"),
    )
