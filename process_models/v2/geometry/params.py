"""Canonical parameters for geometry operations.

Geometry operations are *not* physical process models: they have no duration,
flux engine, rate, or cell set. Each operation has its own parameter model so a
``cutoffHeight`` cannot be confused with a ``radius`` or a boolean position.
The shared :class:`process_models.v2.geometry.engine.GeometryEngine` consumes
the discriminated operation together with these models.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "BooleanParams",
    "ParticleShapeParams",
    "PlanarizeParams",
    "RemoveMaterialParams",
]

_CONFIG = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class PlanarizeParams(BaseModel):
    """One ``viennaps.Planarize(domain, cutoffHeight)`` call."""

    model_config = _CONFIG

    cutoffHeight: float


class ParticleShapeParams(BaseModel):
    """One native ``BoxDistribution`` / ``SphereDistribution`` displacement."""

    model_config = _CONFIG

    operation: Literal["etch", "deposit"]
    shape: Literal["box", "sphere"]
    radius: float | None = Field(default=None, gt=0, le=2)
    halfAxes: Annotated[list[float], Field(min_length=2, max_length=3)] | None = None

    @model_validator(mode="after")
    def check_shape_parameters(self) -> "ParticleShapeParams":
        if self.shape == "sphere":
            if self.radius is None:
                raise ValueError("sphere particleShape requires radius")
            if self.halfAxes is not None:
                raise ValueError("halfAxes applies only to shape=box")
        else:
            if self.halfAxes is None:
                raise ValueError("box particleShape requires halfAxes")
            if self.radius is not None:
                raise ValueError("radius applies only to shape=sphere")
        return self


class BooleanParams(BaseModel):
    """One native boolean with a box or cylinder stencil operand.

    ``position`` is ``[x, h, 0]`` in 2D and ``[x, y, z]`` in 3D, matching the
    project schema. ``shape="box"`` uses width/height/depth; ``shape="cylinder"``
    uses ``radius`` for a vertical cylinder (3D only).
    """

    model_config = _CONFIG

    booleanOperation: Literal["union", "intersection", "difference"]
    position: Annotated[list[float], Field(min_length=3, max_length=3)]
    width: float = Field(gt=0, le=40)
    height: float = Field(gt=0, le=100)
    depth: float | None = Field(default=None, gt=0, le=40)
    angle: float = Field(default=0, ge=-45, le=45)
    applyToAll: bool = True
    shape: Literal["box", "cylinder"] = "box"
    radius: float | None = Field(default=None, gt=0, le=40)

    @model_validator(mode="after")
    def check_shape_parameters(self) -> "BooleanParams":
        if self.shape == "cylinder":
            if self.radius is None:
                raise ValueError("cylinder boolean requires radius")
        elif self.radius is not None:
            raise ValueError("radius applies only to shape=cylinder")
        return self


class RemoveMaterialParams(BaseModel):
    """One ``domain.removeMaterial(material)`` call."""

    model_config = _CONFIG

    material: str = Field(min_length=1, max_length=80)
