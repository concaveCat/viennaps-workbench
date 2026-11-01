"""One native geometry engine carrying the supported operation capabilities.

``GeometryEngine`` operates on the canonical level-set ``Domain``. It never
constructs a ``DenseCellSet`` and never calls a physical process model. The
per-operation parameter models are supplied by the thin geometry variants; a
variant delegates here instead of reimplementing native calls.
"""

from __future__ import annotations

import hashlib
import math
import struct
from collections.abc import Mapping
from typing import Any

from ..contract import Issue, ProcessContext
from ..engine import EngineLease
from ..engines.viennaps import (
    geometry_mask_material_names,
    material_handle,
    material_map,
)
from ..state import (
    LevelSetState,
    MaterialState,
    ProcessState,
    StateDelta,
    StepResult,
)
from .params import (
    BooleanParams,
    ParticleShapeParams,
    PlanarizeParams,
    RemoveMaterialParams,
)

__all__ = ["GeometryEngine", "GeometryEngineError"]


class GeometryEngineError(RuntimeError):
    """Raised when a geometry operation cannot run on the current domain."""


class GeometryEngine:
    """Native ViennaPS geometry operations on one level-set domain."""

    id = "viennaps-geometry"
    version = "1.0.0"
    capabilities = frozenset({"planarize", "particleShape", "boolean", "removeMaterial"})

    def validate(self, operation: str, ctx: ProcessContext, params: Any) -> list[Issue]:
        """Return semantic issues the per-operation parameters cannot express."""
        if operation not in self.capabilities:
            raise GeometryEngineError(f"unsupported geometry operation {operation!r}")
        if operation == "particleShape":
            return self._validate_particle_shape(ctx, params)
        if operation == "boolean":
            return self._validate_boolean(ctx, params)
        return []

    def execute(
        self,
        operation: str,
        state: ProcessState,
        params: Any,
        ctx: ProcessContext,
        engine: EngineLease,
    ) -> StepResult:
        """Apply one geometry operation to the canonical domain."""
        if operation not in self.capabilities:
            raise GeometryEngineError(f"unsupported geometry operation {operation!r}")
        geometry = state.require("geometry")
        domain = geometry.handle
        ps = engine.handle
        warnings: list[str] = []
        if operation == "planarize":
            metadata = self._planarize(ps, domain, params)
        elif operation == "particleShape":
            metadata, warnings = self._particle_shape(ps, domain, state, ctx, params)
        elif operation == "boolean":
            metadata = self._boolean(ps, domain, ctx, params)
        else:
            metadata = self._remove_material(ps, domain, params)

        order = tuple(material_map(domain))
        materials = MaterialState(
            order=order,
            names={material_id: name for material_id, name in state.materials.names.items() if material_id in order},
        )
        metadata.update(
            {
                "dimension": int(geometry.dimension),
                "gridDelta": float(geometry.grid_delta),
                "materialMap": list(order),
                "levelSetPointCounts": [int(level_set.getNumberOfPoints()) for level_set in domain.getLevelSets()],
            }
        )
        return StepResult(
            delta=StateDelta(
                geometry=LevelSetState(
                    handle=domain,
                    dimension=geometry.dimension,
                    grid_delta=geometry.grid_delta,
                ),
                materials=materials,
            ),
            metadata=metadata,
            warnings=warnings,
        )

    # -- validation ------------------------------------------------------

    def _validate_particle_shape(self, ctx: ProcessContext, params: ParticleShapeParams) -> list[Issue]:
        issues: list[Issue] = []
        dimension = int(ctx.dimension)
        if params.shape == "box":
            axes = [float(value) for value in params.halfAxes or ()]
            if len(axes) != dimension:
                issues.append(
                    Issue(
                        path="halfAxes",
                        message=f"box particleShape needs {dimension} half-axes, got {len(axes)}",
                    )
                )
            for index, axis in enumerate(axes):
                if not math.isfinite(axis) or axis < float(ctx.grid_delta) - 1e-12:
                    issues.append(
                        Issue(
                            path=f"halfAxes[{index}]",
                            message=f"half-axis {axis} must be at least one grid interval ({ctx.grid_delta})",
                        )
                    )
        else:
            radius = float(params.radius) if params.radius is not None else 0.0
            if not math.isfinite(radius) or radius < float(ctx.grid_delta) - 1e-12:
                issues.append(
                    Issue(
                        path="radius",
                        message=f"radius {radius} must be at least one grid interval ({ctx.grid_delta})",
                    )
                )
        if params.operation == "deposit":
            masks = self._requested_masks(ctx)
            if masks:
                issues.append(
                    Issue(
                        path="operation",
                        message=(
                            "masked particleShape deposition is not supported: ViennaPS 4.6.2/4.7.0 "
                            f"SIGSEGV on a non-empty mask with positive geometric advection (mask materials {masks})"
                        ),
                    )
                )
        return issues

    def _validate_boolean(self, ctx: ProcessContext, params: BooleanParams) -> list[Issue]:
        issues: list[Issue] = []
        dimension = int(ctx.dimension)
        if dimension == 2:
            if abs(float(params.position[2])) > 1e-12:
                issues.append(Issue(path="position", message="2D boolean position must be [x, h, 0]"))
            if params.depth is not None:
                issues.append(Issue(path="depth", message="depth applies only to 3D boolean"))
            if params.shape == "cylinder":
                issues.append(Issue(path="shape", message="cylinder boolean is supported only in 3D"))
        elif params.shape == "box" and params.depth is None:
            issues.append(Issue(path="depth", message="3D box boolean requires depth"))
        if params.shape == "cylinder" and params.radius is None:
            issues.append(Issue(path="radius", message="cylinder boolean requires radius"))
        if params.shape == "cylinder" and params.depth is not None:
            issues.append(Issue(path="depth", message="depth applies only to a box boolean"))
        return issues

    @staticmethod
    def _requested_masks(ctx: ProcessContext) -> list[str]:
        geometry = ctx.project.get("geometry") if isinstance(ctx.project, Mapping) else None
        if not isinstance(geometry, Mapping):
            return []
        return geometry_mask_material_names(dict(geometry))

    # -- native operations ----------------------------------------------

    @staticmethod
    def _planarize(ps: Any, domain: Any, params: PlanarizeParams) -> dict[str, Any]:
        cutoff = float(params.cutoffHeight)
        ps.Planarize(domain, cutoff).apply()
        return {"geometryOperation": "planarize", "cutoffHeight": cutoff}

    def _particle_shape(
        self,
        ps: Any,
        domain: Any,
        state: ProcessState,
        ctx: ProcessContext,
        params: ParticleShapeParams,
    ) -> tuple[dict[str, Any], list[str]]:
        del state
        dimension = int(ctx.dimension)
        sign = -1.0 if params.operation == "etch" else 1.0
        requested_masks = self._requested_masks(ctx)
        applied_masks: list[tuple[str, Any]] = []
        omitted_masks: list[str] = []
        warnings: list[str] = []
        if sign > 0.0:
            if requested_masks:
                raise GeometryEngineError(
                    "masked particleShape deposition is not supported on this runtime: " + ", ".join(requested_masks)
                )
        else:
            for name in requested_masks:
                handle = material_handle(ps, name)
                level_set = domain.getMaterialLevelSet(handle)
                if level_set is None or int(level_set.getNumberOfPoints()) == 0:
                    omitted_masks.append(name)
                    continue
                applied_masks.append((name, handle))

        if params.shape == "box":
            axes = [float(value) for value in params.halfAxes or ()]
            if len(axes) != dimension:
                raise GeometryEngineError(f"particleShape box needs {dimension} half-axes, got {axes}")
            signed = [sign * value for value in axes]
            native_half_axes = signed + [0.0] if dimension == 2 else signed
            model = ps.BoxDistribution(native_half_axes)
            native_class = "BoxDistribution"
            expected_offset = max(axes)
        else:
            radius = float(params.radius) if params.radius is not None else 0.0
            signed_radius = sign * radius
            model = ps.SphereDistribution(signed_radius)
            native_class = "SphereDistribution"
            native_half_axes = None
            expected_offset = radius

        before = _surface_digest(domain)
        before_counts = [int(level_set.getNumberOfPoints()) for level_set in domain.getLevelSets()]
        for _name, handle in applied_masks:
            model.addMaskMaterial(handle)
        process = ps.Process(domain, model, 0.0)
        process.setFluxEngineType(ps.FluxEngineType.CPU_DISK)
        process.apply()
        after = _surface_digest(domain)
        if before == after:
            raise GeometryEngineError(
                "native particle-shape distribution left the surface unchanged; refusing to record a silent no-op"
            )
        if omitted_masks:
            warnings.append(
                "particleShape omitted mask materials with no exposed level set: " + ", ".join(omitted_masks)
            )
        metadata = {
            "geometryOperation": "particleShape",
            "nativeClass": native_class,
            "dimension": dimension,
            "operation": params.operation,
            "shape": params.shape,
            "expectedOffsetUm": expected_offset,
            "surfaceSignatureBefore": before,
            "surfaceSignatureAfter": after,
            "levelSetPointCountsBefore": before_counts,
            "nativeMask": {
                "mode": "material" if applied_masks else "none",
                "applied": bool(applied_masks),
                "materials": [name for name, _ in applied_masks],
                "omittedMaterials": omitted_masks,
            },
            "nativeDurationIgnored": True,
        }
        if params.shape == "box":
            metadata["halfAxes"] = [float(value) for value in params.halfAxes or ()]
            metadata["nativeSignedHalfAxesUm"] = [sign * value for value in (params.halfAxes or ())]
            if native_half_axes is not None:
                metadata["nativeHalfAxesUm"] = native_half_axes
        else:
            metadata["radius"] = float(params.radius) if params.radius is not None else 0.0
            metadata["nativeSignedRadiusUm"] = signed_radius
        return metadata, warnings

    @staticmethod
    def _boolean(ps: Any, domain: Any, ctx: ProcessContext, params: BooleanParams) -> dict[str, Any]:
        dimension = int(ctx.dimension)
        step_id = str(ctx.step.get("id") or "geometry")
        factory = ps.GeometryFactory(domain.getSetup(), f"WorkbenchBoolean_{step_id}")
        position = [float(value) for value in params.position]
        if dimension == 2:
            if params.shape == "cylinder":
                raise GeometryEngineError("cylinder boolean is supported only in 3D")
            operand = factory.makeBoxStencil(
                position[:2],
                float(params.width),
                float(params.height),
                float(params.angle),
            )
            operand_kind = "box"
        elif params.shape == "cylinder":
            if params.radius is None:
                raise GeometryEngineError("cylinder boolean requires radius")
            operand = factory.makeCylinderStencil(
                position,
                float(params.radius),
                float(params.height),
                float(params.angle),
            )
            operand_kind = "cylinder"
        else:
            if params.depth is None:
                raise GeometryEngineError("3D box boolean requires depth")
            operand = factory.makeBoxStencil(
                position,
                float(params.width),
                float(params.height),
                float(params.angle),
                float(params.depth),
            )
            operand_kind = "box"
        enum_name = {
            "union": "UNION",
            "intersection": "INTERSECT",
            "difference": "RELATIVE_COMPLEMENT",
        }[params.booleanOperation]
        operation = getattr(ps.ls.BooleanOperationEnum, enum_name)
        domain.applyBooleanOperation(operand, operation, bool(params.applyToAll))
        return {
            "geometryOperation": "boolean",
            "booleanOperation": params.booleanOperation,
            "operand": operand_kind,
            "position": position,
            "width": float(params.width),
            "height": float(params.height),
            "depth": float(params.depth) if params.depth is not None else None,
            "radius": float(params.radius) if params.radius is not None else None,
            "angle": float(params.angle),
            "applyToAll": bool(params.applyToAll),
            "nativeBooleanEnum": enum_name,
        }

    @staticmethod
    def _remove_material(ps: Any, domain: Any, params: RemoveMaterialParams) -> dict[str, Any]:
        handle = material_handle(ps, params.material)
        domain.removeMaterial(handle)
        return {
            "geometryOperation": "removeMaterial",
            "material": params.material,
            "materialId": int(handle),
        }


def _surface_digest(domain: Any) -> str:
    """Hash the exposed surface nodes without relying on object identity."""
    mesh = domain.getSurfaceMesh(
        addInterfaces=True,
        sharpCorners=False,
        minNodeDistanceFactor=0.01,
    )
    points = sorted(
        (round(float(point[0]), 9), round(float(point[1]), 9), round(float(point[2]), 9)) for point in mesh.getNodes()
    )
    digest = hashlib.sha256()
    for point in points:
        digest.update(struct.pack("<ddd", *point))
    return digest.hexdigest()
