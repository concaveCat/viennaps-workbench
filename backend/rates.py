"""Pure Python rate interpretation shared by API, hashing, supervision and worker."""
from __future__ import annotations

from typing import Any


GEOMETRY_MODELS = {"removeMaterial", "planarize", "boolean", "gdsBoolean"}


def value(step: Any, key: str, default: Any = None) -> Any:
    return step.get(key, default) if isinstance(step, dict) else getattr(step, key, default)


def is_geometry_step(step: Any) -> bool:
    return value(step, "model") in GEOMETRY_MODELS


def has_motion(step: Any) -> bool:
    if value(step, "model") in {"specialized", "csvProcess"}:
        # Native specialized/CSV models own their physical rate law. A
        # schema-level scalar rate is intentionally absent; always execute.
        return True
    if value(step, "model") == "multiParticle":
        table = value(step, "rateCoefficients", {}) or {}
        defaults = value(table, "defaultBySpecies", {}) or {}
        overrides = value(table, "byMaterial", {}) or {}
        return any(float(rate) > 0.0 for rate in defaults.values()) or any(
            float(rate) > 0.0 for row in overrides.values() for rate in row.values()
        )
    if value(step, "model") == "directional":
        return (
            float(value(step, "directionalRateMagnitude", 0.0)) > 0.0
            or float(value(step, "isotropicRateMagnitude", 0.0)) > 0.0
        )
    fallback = float(value(step, "rateMagnitude", 0.0))
    mode = value(step, "rateMode", "uniform")
    if mode == "material":
        rates = value(step, "materialRates", {}) or {}
        return fallback > 0.0 or any(float(rate) > 0.0 for rate in rates.values())
    return fallback > 0.0


def uses_flux(step: Any) -> bool:
    if value(step, "model") == "specialized":
        from .specialized_models import model_spec
        spec = model_spec(str(value(step, "nativeModel", "")))
        return bool(spec and spec["usesFlux"])
    return has_motion(step) and value(step, "model") in {"singleParticle", "multiParticle"}


def signed_rates(step: Any) -> tuple[float, dict[str, float]]:
    """Return native default and explicit material rates, signed for etch."""
    sign = -1.0 if value(step, "operation") == "etch" else 1.0
    fallback = float(value(step, "rateMagnitude", 0.0)) * sign
    rates = {name: float(rate) * sign for name, rate in (value(step, "materialRates", {}) or {}).items()}
    return fallback, rates


def signed_directional_rates(step: Any) -> tuple[float, float]:
    """Return signed native DirectionalProcess rates."""
    sign = -1.0 if value(step, "operation") == "etch" else 1.0
    return (
        float(value(step, "directionalRateMagnitude", 0.0)) * sign,
        float(value(step, "isotropicRateMagnitude", 0.0)) * sign,
    )
