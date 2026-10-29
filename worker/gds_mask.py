"""Rebuild GDS geometry on the real simulation grid.

The product domain owns one level set per material, and ViennaPS requires every
level set to contain all lower materials.  GDS bodies must therefore be built on
the simulation grid and inserted with the native cumulative union; appending a
standalone body makes the top level set contain only that body and Advect then
clips earlier materials away.

This module also bypasses two installed-binary quirks in
``ps.d3.GDSGeometry.layerToLevelSet``: it returns an empty level set for some
valid ``baseHeight``/``height`` combinations, and its grid follows the GDS
library bounds instead of the user's simulation grid.  The construction below
extrudes the native 2D polygon interior, closes its finite top/bottom caps,
regrids the surface onto the simulation grid and (for masks) restores the
native ``mask=True`` complement tone.

An empty planar interior is never extruded: ViennaLS ``Extrude`` on a level set
without any point crashes the process (SIGSEGV).  ``build_aligned_gds_layer``
turns that state into an explicit error naming the layer, and the caller in
``worker/simulate.py`` first retries the native 2D read on a one-cell-padded
GDS frame, which recovers the real interior of a polygon that fills the GDS
bounds and leaves every other layer untouched.

It deliberately does not touch the domain, read files, register materials or
import the top-level ``viennaps`` package; the caller owns all of that.
"""

from __future__ import annotations

from typing import Any

CONSTRUCTION = "native-extrude-capped-surface-regrid-union-v1"
EMPTY_MASK_ERROR = "GDS 掩膜在当前计算域内为空，请调整版图位置、保留区域或计算域。"
EMPTY_PLANAR_ERROR = (
    "GDS 图层 {layer} 在当前 GDS 边界内没有可用的多边形内部（多边形贴合库边界、"
    "小于网格间距或退化），无法构造实体。请让该图层多边形相对 GDS 库边界内缩至少"
    "一个 gridDelta，或减小 gridDelta 后重试。"
)
_SURFACE_TOLERANCE = 0.01


def _point_count(level_set: Any) -> int:
    return int(level_set.getNumberOfPoints())


def planar_point_count(planar: Any) -> int:
    """Public readback of the native 2D polygon interior point count."""
    return _point_count(planar)


def require_planar_interior(planar: Any, layer: Any) -> None:
    """Reject an empty 2D interior before it reaches the crashing native Extrude."""
    if _point_count(planar) == 0:
        raise RuntimeError(EMPTY_PLANAR_ERROR.format(layer=layer))


def _lower_half_space(ps: Any, template: Any, height: float) -> Any:
    """Copy ``template`` and replace it with the lower half space at ``height``."""
    height = round(float(height), 12)
    half_space = ps.ls.d3.Domain(template)
    ps.ls.d3.MakeGeometry(
        half_space, ps.ls.d3.Plane([0.0, 0.0, height], [0.0, 0.0, 1.0])).apply()
    return half_space


def _finite_source_slab(ps: Any, template: Any, base_height: float, top_height: float) -> Any:
    """Finite slab between ``base_height`` and ``top_height`` on ``template``'s grid."""
    upper = _lower_half_space(ps, template, top_height)
    lower = _lower_half_space(ps, template, base_height)
    ps.ls.d3.BooleanOperation(
        upper, lower, ps.ls.BooleanOperationEnum.RELATIVE_COMPLEMENT).apply()
    return upper


def _finite_target_slab(ps: Any, factory: Any, base_height: float, top_height: float) -> Any:
    """Finite slab between two substrate half spaces on the factory's grid."""
    upper = factory.makeSubstrate(top_height)
    lower = factory.makeSubstrate(base_height)
    ps.ls.d3.BooleanOperation(
        upper, lower, ps.ls.BooleanOperationEnum.RELATIVE_COMPLEMENT).apply()
    return upper


def _capped_extrusion(ps: Any, domain: Any, planar: Any, base_height: float,
                      top_height: float, boundaries: list[Any] | None = None,
                      *, layer: Any = None) -> tuple[Any, dict[str, int]]:
    """Extrude ``planar`` to a finite body with closed top/bottom caps."""
    # ViennaLS Extrude dereferences the input domain's grid points; an empty
    # planar field (a polygon that fills the GDS bounds has no zero crossing)
    # crashes the process instead of raising.  Fail explicitly instead.
    require_planar_interior(planar, layer if layer is not None else "<unknown>")
    reflective = ps.BoundaryType.REFLECTIVE_BOUNDARY
    if boundaries is None:
        # Stack masks are regridded onto the simulation grid later, so the
        # intermediate extrusion stays decoupled from the final periodicity.
        boundaries = [reflective, reflective, ps.BoundaryType.INFINITE_BOUNDARY]
    extruded = ps.ls.d3.Domain(float(domain.getSetup().gridDelta()))
    ps.ls.Extrude(planar, extruded, [base_height, top_height], 2, boundaries).apply()
    extruded_points = _point_count(extruded)

    # Without explicit caps the sparse planar field leaves long open runs, which
    # breaks full-domain and large-polygon cases after regridding.
    source_slab = _finite_source_slab(ps, extruded, base_height, top_height)
    ps.ls.d3.BooleanOperation(
        extruded, source_slab, ps.ls.BooleanOperationEnum.INTERSECT).apply()
    return extruded, {"extrudedPointCount": extruded_points}


def _surface(ps: Any, body: Any) -> Any:
    surface = ps.ls.Mesh()
    ps.ls.d3.ToSurfaceMesh(ps.ls.d3.Domain(body), surface, _SURFACE_TOLERANCE).apply()
    return surface


def _regrid(ps: Any, domain: Any, surface: Any) -> Any:
    """Reconstruct a capped body on the real simulation grid."""
    aligned = ps.ls.d3.Domain(domain.getGrid())
    reconstruction = ps.ls.d3.FromSurfaceMesh(aligned, surface)
    reconstruction.setRemoveBoundaryTriangles(False)
    reconstruction.apply()
    return aligned


def _capped_and_regrid(ps: Any, domain: Any, planar: Any, base_height: float,
                       top_height: float, *, layer: Any = None) -> tuple[Any, dict[str, int]]:
    extruded, stats = _capped_extrusion(
        ps, domain, planar, base_height, top_height, layer=layer)
    surface = _surface(ps, extruded)
    stats["sourceSurfacePointCount"] = len(surface.getNodes())
    stats["sourceSurfaceTriangleCount"] = len(surface.getTriangles())
    return _regrid(ps, domain, surface), stats


def build_aligned_gds_layer(ps: Any, domain: Any, planar_inside: Any, *, base_height: float,
                            height: float, mask_tone: bool = False,
                            layer: Any = None) -> tuple[Any, dict[str, Any]]:
    """Return one generic GDS layer body plus diagnostics.

    ``planar_inside`` is the native 2D polygon interior.  The body stays on the
    shared GDS grid, matching the native layer grid; every mapping uses the same
    grid, so the caller can wrap cumulatively.  ``mask_tone`` keeps the installed
    ``layerToLevelSet(mask=True)`` semantics: a finite slab over the GDS bounds
    with the polygons removed.  Building that slab on the extruded body's native
    grid (instead of inverting the sparse 2D field) keeps every surface closed
    for Advect and particle processes.

    ``layer`` only labels error messages and diagnostics.  An empty planar
    interior raises instead of being extruded (which would crash natively).
    """
    base_height = round(float(base_height), 12)
    top_height = round(base_height + float(height), 12)
    require_planar_interior(planar_inside, layer if layer is not None else "<unknown>")
    # Generic layers are inserted on their own GDS grid, so they must carry the
    # simulation domain's lateral boundary conditions (periodic stays periodic).
    extruded, stats = _capped_extrusion(
        ps, domain, planar_inside, base_height, top_height,
        list(domain.getSetup().boundaryCons()), layer=layer)
    body = extruded
    if mask_tone:
        target_slab = _finite_source_slab(ps, extruded, base_height, top_height)
        if _point_count(extruded):
            ps.ls.d3.BooleanOperation(
                target_slab, extruded, ps.ls.BooleanOperationEnum.RELATIVE_COMPLEMENT).apply()
        body = target_slab
    surface = _surface(ps, body)
    stats["sourceSurfacePointCount"] = len(surface.getNodes())
    stats["sourceSurfaceTriangleCount"] = len(surface.getTriangles())
    # Generic GDS layers stay on their native GDS grid, exactly like the native
    # layerToLevelSet output.  All mappings share that grid, so the cumulative
    # union is safe; clipping to the user domain is reserved for the stack mask.
    aligned = body
    points = _point_count(aligned)
    diagnostics: dict[str, Any] = {
        "gridSource": "gds-layer-grid",
        "construction": CONSTRUCTION,
        "maskTone": bool(mask_tone),
        "planarPointCount": _point_count(planar_inside),
        "alignedLayerPointCount": points,
        **stats,
    }
    if points == 0:
        raise RuntimeError("GDS 图层在当前计算域内为空，请调整版图位置或计算域。")
    return aligned, diagnostics


def build_aligned_gds_mask(ps: Any, domain: Any, planar_inside: Any, *, base_height: float,
                           height: float, retained_region: str,
                           layer: Any = None) -> tuple[Any, dict[str, Any]]:
    """Return a mask level set on ``domain``'s grid plus construction diagnostics.

    ``planar_inside`` must be the polygon interior read from the native 2D
    ``GDSGeometry.layerToLevelSet`` call (not inverted).  ``retained_region`` is
    ``"inside"`` or ``"outside"``.  No material is inserted into ``domain``.
    ``layer`` only labels error messages, so an empty interior never reaches
    the crashing native Extrude.
    """
    if retained_region not in {"inside", "outside"}:
        raise ValueError(f"unsupported GDS retained region: {retained_region!r}")
    base_height = round(float(base_height), 12)
    top_height = round(base_height + float(height), 12)
    require_planar_interior(planar_inside, layer if layer is not None else "<unknown>")
    aligned, stats = _capped_and_regrid(
        ps, domain, planar_inside, base_height, top_height, layer=layer)

    diagnostics: dict[str, Any] = {
        "gridSource": "simulation-domain",
        "wrapLowerLevelSet": True,
        "retainedRegion": retained_region,
        "construction": CONSTRUCTION,
        "planarPointCount": _point_count(planar_inside),
        **stats,
    }

    # Polarity.  "inside" is the regridded body; "outside" is the finite target
    # slab minus that body, built on the same compatible grid.
    if retained_region == "outside":
        inside_points = _point_count(aligned)
        diagnostics["insideMaskPointCount"] = inside_points
        target_slab = _finite_target_slab(
            ps, ps.GeometryFactory(domain.getSetup(), "GdsMaskComplement"),
            base_height, top_height)
        if inside_points:
            ps.ls.d3.BooleanOperation(
                target_slab, aligned, ps.ls.BooleanOperationEnum.RELATIVE_COMPLEMENT).apply()
        aligned = target_slab

    aligned_points = _point_count(aligned)
    diagnostics["alignedMaskPointCount"] = aligned_points
    # An empty result is an explicit run failure; a full-domain inside mask is
    # valid.  The check runs after polarity so full-coverage outside is empty.
    if aligned_points == 0:
        raise RuntimeError(EMPTY_MASK_ERROR)
    return aligned, diagnostics
