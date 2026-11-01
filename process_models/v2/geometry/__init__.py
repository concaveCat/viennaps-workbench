"""Native geometry operations as first-class execution steps.

The operations are ordinary :class:`~process_models.v2.contract.ProcessSpec`
instances with ``execution_kind="geometry"``. They reuse the same registry,
resolver, executor, engine lease, and persistence as process steps, but one
shared :class:`GeometryEngine` implements every operation and each operation
keeps its own parameter model.
"""

from __future__ import annotations

from .engine import GeometryEngine, GeometryEngineError
from .params import (
    BooleanParams,
    GdsBooleanMaskParams,
    GdsBooleanParams,
    ParticleShapeParams,
    PlanarizeParams,
    RemoveMaterialParams,
)
from .register import register_geometry
from .specs import (
    BooleanProcessSpec,
    GdsBooleanProcessSpec,
    GeometryProcessSpecs,
    ParticleShapeProcessSpec,
    PlanarizeProcessSpec,
    RemoveMaterialProcessSpec,
)
from .step import (
    parse_boolean,
    parse_gds_boolean,
    parse_particle_shape,
    parse_planarize,
    parse_remove_material,
)
from .variants import GeometryVariant

__all__ = [
    "BooleanParams",
    "BooleanProcessSpec",
    "GdsBooleanMaskParams",
    "GdsBooleanParams",
    "GdsBooleanProcessSpec",
    "GeometryEngine",
    "GeometryEngineError",
    "GeometryProcessSpecs",
    "GeometryVariant",
    "ParticleShapeParams",
    "ParticleShapeProcessSpec",
    "PlanarizeParams",
    "PlanarizeProcessSpec",
    "RemoveMaterialParams",
    "RemoveMaterialProcessSpec",
    "parse_boolean",
    "parse_gds_boolean",
    "parse_particle_shape",
    "parse_planarize",
    "parse_remove_material",
    "register_geometry",
]
