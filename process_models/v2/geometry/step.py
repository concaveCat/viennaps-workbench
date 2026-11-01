"""Worker-step parsing for geometry operations."""

from __future__ import annotations

from typing import Any, Mapping

from .params import (
    BooleanParams,
    GdsBooleanParams,
    ParticleShapeParams,
    PlanarizeParams,
    RemoveMaterialParams,
)

__all__ = [
    "parse_boolean",
    "parse_gds_boolean",
    "parse_particle_shape",
    "parse_planarize",
    "parse_remove_material",
]


def parse_planarize(project: Mapping[str, Any], step: Mapping[str, Any]) -> PlanarizeParams | None:
    """Return planarize parameters for either cutoff source."""
    del project
    if step.get("model") != "planarize":
        return None
    payload = {name: step[name] for name in ("cutoffHeight", "stopMaterial", "overPolish") if name in step}
    return PlanarizeParams(**payload)


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


def parse_gds_boolean(project: Mapping[str, Any], step: Mapping[str, Any]) -> GdsBooleanParams | None:
    del project
    if step.get("model") != "gdsBoolean":
        return None
    payload = {name: step[name] for name in ("booleanOperation", "gdsMask", "applyToAll") if name in step}
    return GdsBooleanParams(**payload)


def parse_remove_material(project: Mapping[str, Any], step: Mapping[str, Any]) -> RemoveMaterialParams | None:
    del project
    if step.get("model") != "removeMaterial":
        return None
    return RemoveMaterialParams(material=step["material"])
