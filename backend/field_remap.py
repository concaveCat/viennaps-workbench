"""Conservative cell-grid remap for the native field ``DenseCellSet``.

The 4.7.0 implant/anneal/analysis models operate on a native ``DenseCellSet``
that is generated from the level sets. The grid is axis aligned and lives on
the domain lattice (spacing ``gridDelta``), so it can be described exactly by
an origin, a cell count per axis and the grid spacing.

When a surface-moving step runs between two field steps the level sets change
and the cell set must be regenerated. The scalar fields are **not**
redistributed: a new cell either maps to exactly one old cell (same absolute
position on the lattice) or is new and starts at ``0``. Cells that leave the
solid (their new material is the cover material) lose their dopant and the loss
is recorded per label. No interpolation and no nearest-neighbour smoothing is
performed.

This module is pure Python so the mapping can be unit tested without a native
runtime; the worker only performs the native calls and feeds the resulting
arrays back in.
"""
from __future__ import annotations

import hashlib
import math
from array import array
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

#: Cell scalar labels the native generator owns. They are always rebuilt from
#: the current level sets and never remapped.
NATIVE_SYSTEM_LABELS: tuple[str, ...] = ("Material", "FillingFraction")

#: Vertical cell axis per dimension (the 2D grid stores x/depth, 3D x/y/z).
VERTICAL_AXIS: dict[int, int] = {2: 1, 3: 2}


class GridRemapError(RuntimeError):
    """The two grids cannot be remapped without interpolation."""


def _collapse(values: Iterable[float], delta: float) -> list[float]:
    """Return the sorted, de-duplicated lattice coordinates of ``values``."""
    scale = delta if delta > 0.0 else 1.0
    return sorted({round(float(value) / scale) for value in values})


@dataclass(frozen=True)
class CellGrid:
    """Exact description of a native ``DenseCellSet`` regular grid."""

    dimension: int
    delta: float
    origin: tuple[float, float, float]
    shape: tuple[int, int, int]

    @property
    def vertical_axis(self) -> int:
        return VERTICAL_AXIS[int(self.dimension)]

    @property
    def cell_count(self) -> int:
        return self.shape[0] * self.shape[1] * self.shape[2]

    @property
    def bottom(self) -> float:
        return float(self.origin[self.vertical_axis])

    @property
    def top(self) -> float:
        return float(self.origin[self.vertical_axis] + self.shape[self.vertical_axis] * self.delta)

    @property
    def lateral_axes(self) -> tuple[int, ...]:
        return tuple(axis for axis in range(int(self.dimension)) if axis != self.vertical_axis)

    def index(self, i: int, j: int, k: int) -> int:
        nx, ny, _nz = self.shape
        return i + nx * (j + ny * k)

    def coordinates(self, index: int) -> tuple[int, int, int]:
        nx, ny, _nz = self.shape
        i = index % nx
        rest = index // nx
        j = rest % ny
        k = rest // ny
        return i, j, k

    def signature(self) -> dict[str, Any]:
        """Deterministic JSON-friendly description used in the run record."""
        return {
            "dimension": int(self.dimension),
            "gridDelta": float(self.delta),
            "origin": [float(value) for value in self.origin],
            "shape": [int(value) for value in self.shape],
            "numberOfCells": int(self.cell_count),
            "bottom": float(self.bottom),
            "top": float(self.top),
        }

    @classmethod
    def from_points(cls, points: Sequence[Sequence[float]], *, dimension: int,
                    delta: float) -> "CellGrid":
        """Build the grid description from native cell-set corner nodes."""
        dimension = int(dimension)
        delta = float(delta)
        if dimension not in VERTICAL_AXIS:
            raise GridRemapError(f"unsupported dimension {dimension!r} for a cell grid")
        if not delta > 0.0:
            raise GridRemapError("cell grid spacing must be positive")
        if not points:
            raise GridRemapError("cell grid has no nodes")
        counts = []
        origin = [0.0, 0.0, 0.0]
        for axis in range(dimension):
            cells = _collapse((point[axis] for point in points), delta)
            if len(cells) < 2:
                raise GridRemapError(f"cell grid has no extent along axis {axis}")
            # A gap inside one axis means the grid is not a single regular box.
            for left, right in zip(cells, cells[1:]):
                if right - left != 1:
                    raise GridRemapError(
                        "cell grid is not a contiguous regular lattice along axis "
                        f"{axis}: gap of {right - left} cells")
            origin[axis] = cells[0] * delta
            counts.append(len(cells) - 1)
        while len(counts) < 3:
            counts.append(1)
        grid = cls(dimension=dimension, delta=delta,
                   origin=(origin[0], origin[1], origin[2]),
                   shape=(counts[0], counts[1], counts[2]))
        if grid.cell_count != grid.shape[0] * grid.shape[1] * grid.shape[2]:
            raise GridRemapError("cell grid shape is inconsistent")
        return grid


def _axis_offset(old: CellGrid, new: CellGrid, axis: int) -> int:
    """Return ``new_origin - old_origin`` in whole cells along ``axis``."""
    delta = old.delta
    raw = (float(new.origin[axis]) - float(old.origin[axis])) / delta
    whole = round(raw)
    if abs(raw - whole) > 1e-6:
        raise GridRemapError(
            "conservative grid remap not implemented: the regenerated cell grid is not "
            f"aligned to the captured lattice along axis {axis} "
            f"(offset {raw * delta:g} um is not a multiple of gridDelta {delta:g} um)")
    return int(whole)


def _axis_overlap(old_cells: int, new_cells: int, offset: int) -> tuple[int, int]:
    """Return ``(start, count)`` of new indices that map into the old range.

    A new cell at index ``i`` corresponds to old index ``i + offset``.
    """
    start = max(0, -offset)
    stop = min(new_cells, old_cells - offset)
    return start, max(0, stop - start)


def _digest(values: Sequence[float]) -> str:
    return hashlib.sha256(array("d", values).tobytes()).hexdigest()


@dataclass
class RemapResult:
    """Remapped scalar arrays plus the audit record for one remap."""

    labels: dict[str, list[float]] = field(default_factory=dict)
    record: dict[str, Any] = field(default_factory=dict)


def remap_cell_fields(
    old_grid: CellGrid,
    new_grid: CellGrid,
    *,
    old_values: Mapping[str, Sequence[float]],
    old_material: Sequence[float],
    new_material: Sequence[float],
    cover_material_id: int,
    labels: Sequence[str] | None = None,
    cell_volume: float | None = None,
    source_step_id: str | None = None,
    previous_remap_count: int = 0,
) -> RemapResult:
    """Conservatively remap ``old_values`` onto ``new_grid``.

    * same grid -> direct index copy (``O(N)``),
    * integer lattice shift/shrink/grow -> axis-aligned overlap copy,
    * anything else -> :class:`GridRemapError` (no interpolation).
    """
    dimension = int(old_grid.dimension)
    if int(new_grid.dimension) != dimension:
        raise GridRemapError("cell grid dimension changed; a field grid cannot change dimension")
    if abs(float(new_grid.delta) - float(old_grid.delta)) > 1e-12:
        raise GridRemapError(
            "conservative grid remap not implemented: the regenerated cell grid has a different "
            f"gridDelta ({new_grid.delta:g} vs {old_grid.delta:g} um)")
    old_cells = old_grid.cell_count
    new_cells = new_grid.cell_count
    if len(old_material) != old_cells:
        raise GridRemapError("old cell-set material array length does not match the grid")
    if len(new_material) != new_cells:
        raise GridRemapError("new cell-set material array length does not match the grid")
    for label, values in old_values.items():
        if len(values) != old_cells:
            raise GridRemapError(
                f"old cell-set scalar {label!r} length {len(values)} does not match the "
                f"{old_cells}-cell grid")

    active_axes = list(range(dimension))
    offsets = [_axis_offset(old_grid, new_grid, axis) for axis in active_axes]
    if dimension == 2:
        offsets.append(0)
    spans = []
    overlap_total = 1
    for axis in range(3):
        old_axis = old_grid.shape[axis]
        new_axis = new_grid.shape[axis]
        if axis >= dimension:
            start, count = 0, min(old_axis, new_axis)
        else:
            start, count = _axis_overlap(old_axis, new_axis, offsets[axis])
        spans.append((start, count))
        overlap_total *= count

    same_grid = (old_grid.shape == new_grid.shape
                 and all(abs(float(old_grid.origin[axis]) - float(new_grid.origin[axis])) <= 1e-9
                         for axis in range(3)))
    label_names = list(labels if labels is not None else old_values.keys())
    dv = float(cell_volume) if cell_volume is not None else float(old_grid.delta) ** dimension
    cover = int(cover_material_id)

    arrays: dict[str, list[float]] = {}
    masses: dict[str, dict[str, float | None]] = {}
    removed_indices: list[int] = []
    reactivated = 0
    # A new cell is "removed material" when the regenerated grid says it is
    # cover but it used to be a solid cell. Its dopant is dropped, never moved.
    if same_grid:
        for new_index in range(new_cells):
            if int(new_material[new_index]) == cover and int(old_material[new_index]) != cover:
                removed_indices.append(new_index)
            elif int(new_material[new_index]) != cover and int(old_material[new_index]) == cover:
                reactivated += 1
        removed_set = None if not removed_indices else set(removed_indices)
        for label in label_names:
            old = [float(value) for value in old_values[label]]
            if removed_set is None:
                arrays[label] = list(old)
            else:
                arrays[label] = [0.0 if index in removed_set else old[index]
                                 for index in range(new_cells)]
    else:
        removed_set = set()
        for new_index in range(new_cells):
            i, j, k = new_grid.coordinates(new_index)
            if not (spans[0][0] <= i < spans[0][0] + spans[0][1]
                    and spans[1][0] <= j < spans[1][0] + spans[1][1]
                    and spans[2][0] <= k < spans[2][0] + spans[2][1]):
                continue
            old_index = old_grid.index(i + offsets[0], j + offsets[1], k + offsets[2])
            if int(new_material[new_index]) == cover and int(old_material[old_index]) != cover:
                removed_set.add(new_index)
            elif int(new_material[new_index]) != cover and int(old_material[old_index]) == cover:
                reactivated += 1
        for label in label_names:
            old = [float(value) for value in old_values[label]]
            values = [0.0] * new_cells
            for new_index in range(new_cells):
                i, j, k = new_grid.coordinates(new_index)
                if (spans[0][0] <= i < spans[0][0] + spans[0][1]
                        and spans[1][0] <= j < spans[1][0] + spans[1][1]
                        and spans[2][0] <= k < spans[2][0] + spans[2][1]):
                    values[new_index] = old[old_grid.index(
                        i + offsets[0], j + offsets[1], k + offsets[2])]
            if removed_set:
                for index in removed_set:
                    values[index] = 0.0
            arrays[label] = values

    for label in label_names:
        old = [float(value) for value in old_values[label]]
        before = sum(old) * dv
        after = sum(arrays[label]) * dv
        lost = before - after
        masses[label] = {
            "before": before,
            "after": after,
            "lost": lost,
            "lostMass": lost,
            "unit": f"concentration*{'um^2' if dimension == 2 else 'um^3'}",
        }

    new_count = new_cells - overlap_total
    removed_topology = old_cells - overlap_total
    removed_material = len(removed_indices) if same_grid else len(removed_set)
    if same_grid:
        method = "same-grid-index-copy"
    else:
        method = "conservative-axis-aligned-integer-overlap"
    record: dict[str, Any] = {
        "sourceStepId": source_step_id,
        "remapIndex": int(previous_remap_count) + 1,
        "method": method,
        "exact": True,
        "interpolation": "none; every new cell maps to one old cell or starts at zero",
        "sameGrid": bool(same_grid),
        "oldGrid": old_grid.signature(),
        "newGrid": new_grid.signature(),
        "cellOffset": {"x": offsets[0], "y": offsets[1], "z": offsets[2]},
        "oldCellCount": old_cells,
        "newCellCount": new_cells,
        "remappedCells": overlap_total,
        "newCells": new_count,
        "removedCells": removed_topology + removed_material,
        "removedByGrid": removed_topology,
        "removedByMaterial": removed_material,
        "reactivatedCells": reactivated,
        "coverMaterialId": cover,
        "cellVolume": dv,
        "labels": list(label_names),
        "mass": masses,
        "fieldDigests": {label: _digest(arrays[label]) for label in label_names},
        "labelsPreserved": True,
        "materialAndFilling": "rebuilt by the native fromLevelSets generator; not remapped",
    }
    return RemapResult(labels=arrays, record=record)


def check_captured_grid(captured: CellGrid, regenerated: CellGrid) -> None:
    """Reject a regeneration that changed the lateral grid or the spacing.

    The vertical extent may grow (an etch removes substrate rows, a deposit
    adds cover rows); the overlap remap handles that conservatively. The
    lateral extent, spacing and origin must stay identical because the domain
    does not change inside a run.
    """
    if int(captured.dimension) != int(regenerated.dimension):
        raise GridRemapError("conservative grid remap not implemented: cell grid dimension changed")
    if abs(captured.delta - regenerated.delta) > 1e-12:
        raise GridRemapError(
            "conservative grid remap not implemented: gridDelta changed "
            f"({captured.delta:g} -> {regenerated.delta:g} um)")
    for axis in captured.lateral_axes:
        if abs(captured.origin[axis] - regenerated.origin[axis]) > 1e-9:
            raise GridRemapError(
                "conservative grid remap not implemented: lateral cell grid origin changed along "
                f"axis {axis} ({captured.origin[axis]:g} -> {regenerated.origin[axis]:g} um)")
        if captured.shape[axis] != regenerated.shape[axis]:
            raise GridRemapError(
                "conservative grid remap not implemented: lateral cell count changed along "
                f"axis {axis} ({captured.shape[axis]} -> {regenerated.shape[axis]})")
