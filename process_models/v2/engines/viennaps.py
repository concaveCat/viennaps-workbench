"""ViennaPS helpers shared by the worker and v2 plugin variants.

These functions were extracted from :mod:`worker.simulate` so a plugin variant
can drive a native ``ps`` module without importing the worker. They are pure
Python glue: the native module is always passed in as ``ps`` and nothing here
imports ViennaPS, ViennaLS, :mod:`backend`, or :mod:`worker`.

:func:`signed_rates` and :func:`geometry_mask_material_names` are defined here
because a plugin needs them; :mod:`backend.rates` and :mod:`backend.materials`
re-export them for existing API/worker callers.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "GEO_MASK_MATERIAL_DEFAULT",
    "configure_advection",
    "geometry_mask_material_names",
    "material_handle",
    "material_map",
    "protected_material_names",
    "signed_rates",
]

#: Material assumed for the built-in trench/hole/fin mask geometry.
GEO_MASK_MATERIAL_DEFAULT = "Mask"


def _step_value(step: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a step mapping or object."""
    return step.get(key, default) if isinstance(step, dict) else getattr(step, key, default)


def signed_rates(step: Any) -> tuple[float, dict[str, float]]:
    """Return native default and explicit material rates, signed for etch."""
    sign = -1.0 if _step_value(step, "operation") == "etch" else 1.0
    fallback = float(_step_value(step, "rateMagnitude", 0.0)) * sign
    rates = {name: float(rate) * sign for name, rate in (_step_value(step, "materialRates", {}) or {}).items()}
    return fallback, rates


def geometry_mask_material_names(geometry: Any) -> list[str]:
    """Material names exposed as masks by the project geometry alone.

    This is the single derivation shared by the scalar particle/directional
    paths, the CSV rate-grid path and the per-rate-set mask default. It covers
    the built-in trench/hole/fin mask, stack mask layers and the legacy stack
    ``gdsMask`` overlay (only when it protects etch), plus GDS geometry layers
    explicitly marked as masks.
    """
    if not isinstance(geometry, dict):
        return []
    kind = geometry.get("kind")
    if kind in ("trench", "hole", "fin") and float(geometry.get("maskHeight", 0) or 0) > 0:
        return [str(geometry.get("maskMaterial", GEO_MASK_MATERIAL_DEFAULT))]
    if kind == "stack":
        values = [str(layer["material"]) for layer in geometry.get("layers", []) if layer.get("role") == "mask"]
        gds_mask = geometry.get("gdsMask") or {}
        if gds_mask.get("protectsEtch") and gds_mask.get("material"):
            values.append(str(gds_mask["material"]))
        return list(dict.fromkeys(values))
    if kind == "gds":
        return list(
            dict.fromkeys(str(layer["material"]) for layer in geometry.get("layers", []) if layer.get("mask", False))
        )
    return []


def material_handle(ps: Any, name: str) -> Any:
    """Return the installed ViennaPS material handle for ``name``.

    Raises ``RuntimeError`` instead of silently substituting a default when the
    name is absent from the native ``MaterialRegistry``.
    """
    registry = ps.MaterialRegistry.instance()
    material = registry.findMaterial(name)
    if material is None:
        raise RuntimeError(f"material {name!r} is absent from the native MaterialRegistry")
    return registry.getMaterial(name)


def material_map(domain: Any) -> list[int]:
    """Return the material id of every level set in ``domain``, in order."""
    mapping = domain.getMaterialMap()
    return [int(mapping.getMaterialIdAtIdx(index)) for index in range(mapping.size())]


def protected_material_names(project: dict[str, Any], step: dict[str, Any] | None = None) -> list[str]:
    """Material names that a native process must not move.

    The geometry derivation lives next to this helper (and is re-exported by
    ``backend.materials``) so the API validation, the worker and the plugins
    share one implementation. This helper keeps the ``rateMode == "material"``
    rule: the rate table is authoritative there, so geometry masks are not
    applied.
    """
    if project["schemaVersion"] in (4, 5, 6, 7) and step is not None and step.get("rateMode") == "material":
        return []
    return geometry_mask_material_names(project["geometry"])


def configure_advection(
    ps: Any, project: dict[str, Any], time_step_ratio: float | None = None
) -> tuple[Any, dict[str, Any]]:
    """Build the step's native AdvectionParameters.

    ``time_step_ratio`` is the step-level ``isotropicTimeStepRatio`` override.
    It is applied to a freshly configured parameter object (never to the
    saved project's solver defaults) and recorded under ``timeStepRatio`` so a
    run remains auditable.
    """
    params = ps.AdvectionParameters()
    params.timeStepRatio = float(project["solver"]["timeStepRatio"])
    actual: dict[str, Any] = {"timeStepRatio": float(params.timeStepRatio)}
    if time_step_ratio is not None:
        params.timeStepRatio = float(time_step_ratio)
        actual["timeStepRatio"] = float(params.timeStepRatio)
        actual["solverTimeStepRatio"] = float(project["solver"]["timeStepRatio"])
        actual["timeStepRatioSource"] = "step.isotropicTimeStepRatio"
    if project["schemaVersion"] >= 7:
        requested = project["solver"]["advection"]
        params.spatialScheme = getattr(ps.ls.SpatialSchemeEnum, requested["spatialScheme"])
        params.temporalScheme = getattr(ps.ls.TemporalSchemeEnum, requested["temporalScheme"])
        for name in (
            "adaptiveTimeStepping",
            "adaptiveTimeStepSubdivisions",
            "calculateIntermediateVelocities",
            "checkDissipation",
            "dissipationAlpha",
            "ignoreVoids",
            "velocityOutput",
        ):
            setattr(params, name, requested[name])
        actual.update(
            {
                name: getattr(params, name)
                for name in (
                    "adaptiveTimeStepping",
                    "adaptiveTimeStepSubdivisions",
                    "calculateIntermediateVelocities",
                    "checkDissipation",
                    "dissipationAlpha",
                    "ignoreVoids",
                    "velocityOutput",
                )
            }
        )
        actual["spatialScheme"] = params.spatialScheme.name
        actual["spatialSchemeValue"] = int(params.spatialScheme)
        actual["temporalScheme"] = params.temporalScheme.name
        actual["temporalSchemeValue"] = int(params.temporalScheme)
        actual["nativeAdvectionMetadata"] = params.toMetaData()
    return params, actual
