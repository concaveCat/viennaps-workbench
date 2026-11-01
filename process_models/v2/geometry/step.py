"""Worker-step parsing for geometry operations."""

from __future__ import annotations

from typing import Any, Mapping

from .params import (
    BooleanParams,
    ParticleShapeParams,
    PlanarizeParams,
    RemoveMaterialParams,
)

__all__ = [
    "parse_boolean",
    "parse_particle_shape",
    "parse_planarize",
    "parse_remove_material",
]


def parse_planarize(project: Mapping[str, Any], step: Mapping[str, Any]) -> PlanarizeParams | None:
    """Return cutoff planarize parameters, or ``None`` for another step.

    The material-aware ``stopMaterial`` path is not part of this slice; it is
    declined so the worker's existing path handles it instead of silently
    substituting a cutoff height.
    """
    del project
    if step.get("model") != "planarize":
        return None
    if step.get("stopMaterial") is not None:
        return None
    cutoff = step.get("cutoffHeight")
    if cutoff is None:
        raise ValueError("planarize requires cutoffHeight on the supported path")
    return PlanarizeParams(cutoffHeight=cutoff)


def parse_particle_shape(project: Mapping[str, Any], step: Mapping[str, Any]) -> ParticleShapeParams | None:
    del project
    if step.get("model") != "particleShape":
        return None
    payload = {name: step[name] for name in ("operation", "shape", "radius", "halfAxes") if name in step}
    return ParticleShapeParams(**payload)


def parse_boolean(project: Mapping[str, Any], step: Mapping[str, Any]) -> BooleanParams | None:
    del project
    if step.get("model") != "boolean":
        return None
    payload = {
        name: step[name]
        for name in (
            "booleanOperation",
            "position",
            "width",
            "height",
            "depth",
            "angle",
            "applyToAll",
            "shape",
            "radius",
        )
        if name in step
    }
    return BooleanParams(**payload)


def parse_remove_material(project: Mapping[str, Any], step: Mapping[str, Any]) -> RemoveMaterialParams | None:
    del project
    if step.get("model") != "removeMaterial":
        return None
    return RemoveMaterialParams(material=step["material"])
