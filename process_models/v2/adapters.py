"""State adapters between representations.

A variant declares what it reads and writes; the adapter is responsible for
making the declared read representation available and for committing the
returned :class:`~process_models.v2.state.StateDelta` back into a
:class:`~process_models.v2.state.ProcessState`.

Boundaries
----------
Only the default adapter exists. It converts height-field level-set geometry
to/from :class:`process_models.voxel.VoxelGrid` on demand. ``layers`` and
``fields`` have no conversion path: requesting a missing typed representation
is an error rather than a silent lossy derivation.

The ViennaPS/viennals import lives behind :mod:`process_models.voxel` and is
performed lazily inside methods, so importing this package does not require
native libraries.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Protocol, runtime_checkable

from .contract import VariantCapabilities
from .state import (
    LevelSetState,
    MaterialState,
    ProcessState,
    StateDelta,
    StateKind,
    StateMissingError,
    VoxelState,
)

__all__ = ["DefaultStateAdapter", "StateAdapter"]


@runtime_checkable
class StateAdapter(Protocol):
    """Converts state representations around one variant execution.

    ``provides`` lists the representations the adapter can materialize from an
    existing state (for example ``"voxels"`` from ``"geometry"``). The resolver
    uses it to admit derived reads without pretending every adapter can derive
    every representation.
    """

    id: str
    provides: frozenset[StateKind]

    def prepare(self, state: ProcessState, capabilities: VariantCapabilities) -> ProcessState: ...

    def commit(
        self,
        state: ProcessState,
        delta: StateDelta,
        capabilities: VariantCapabilities,
    ) -> ProcessState: ...


class DefaultStateAdapter:
    """The v2 default adapter.

    ``prepare`` materializes voxels from geometry when a variant reads them
    and raises :class:`StateMissingError` for missing ``layers``/``fields``
    (stored-only representations). ``commit`` applies a geometry delta,
    otherwise writes a voxel delta back into a *new* geometry, then applies
    ``layers`` and ``fields`` deltas independently. The input state is never
    mutated.
    """

    id = "default-v2"
    #: Derived representations only. Geometry is passed through when present;
    #: it cannot be materialized from nothing. ``layers``/``fields`` are
    #: stored-only and therefore not listed.
    provides = frozenset({"voxels"})

    def prepare(self, state: ProcessState, capabilities: VariantCapabilities) -> ProcessState:
        """Ensure every representation in ``capabilities.reads`` is available."""
        if "layers" in capabilities.reads and state.layers is None:
            raise StateMissingError("layer state is not available and cannot be derived")
        if "fields" in capabilities.reads and state.fields is None:
            raise StateMissingError("field state is not available and cannot be derived")
        if "voxels" in capabilities.reads and state.voxels is None:
            if state.geometry is None:
                raise StateMissingError("voxel state requires geometry to voxelize")
            grid = _read_voxels(state.geometry.handle)
            return replace(state, voxels=VoxelState(grid=grid))

        return state

    def commit(
        self,
        state: ProcessState,
        delta: StateDelta,
        capabilities: VariantCapabilities,
    ) -> ProcessState:
        """Return a new state with ``delta`` applied.

        Geometry wins over voxels when a variant returns both: a variant that
        produced a new native geometry has already superseded any voxel view.
        A committed geometry also drops a stale voxel snapshot so the next
        voxel reader re-derives it. ``layers`` and ``fields`` are independent
        of geometry/voxels and of each other.
        """
        prepared = state

        if delta.geometry is not None:
            if "geometry" not in capabilities.writes:
                raise _resolution_error(
                    "variant returned a geometry delta but does not declare 'geometry' in capabilities.writes"
                )
            prepared = replace(
                prepared,
                geometry=delta.geometry,
                voxels=None,
                materials=_merge_materials(delta.geometry.material_order, prepared.materials),
            )
        elif delta.voxels is not None:
            if "voxels" not in capabilities.writes:
                raise _resolution_error(
                    "variant returned a voxel delta but does not declare 'voxels' in capabilities.writes"
                )
            if state.geometry is None:
                raise StateMissingError("committing voxels requires the original geometry")

            new_handle = _write_voxels(state.geometry.handle, delta.voxels.grid)
            order = tuple(int(material_id) for material_id in getattr(delta.voxels.grid, "material_order", ()))
            if not order:
                order = state.geometry.material_order
            geometry = LevelSetState(
                handle=new_handle,
                dimension=state.geometry.dimension,
                grid_delta=state.geometry.grid_delta,
                material_order=order,
            )
            prepared = replace(
                prepared,
                geometry=geometry,
                voxels=None,
                materials=_merge_materials(order, prepared.materials),
            )

        if delta.layers is not None:
            if "layers" not in capabilities.writes:
                raise _resolution_error(
                    "variant returned a layers delta but does not declare 'layers' in capabilities.writes"
                )
            prepared = replace(prepared, layers=delta.layers)

        if delta.fields is not None:
            if "fields" not in capabilities.writes:
                raise _resolution_error(
                    "variant returned a fields delta but does not declare 'fields' in capabilities.writes"
                )
            prepared = replace(prepared, fields=delta.fields)

        if prepared is state:
            return state
        return prepared


def _resolution_error(message: str) -> Exception:
    """Build the resolver error lazily to avoid a circular import."""
    from .resolver import ExecutionResolutionError

    return ExecutionResolutionError(message)


def _merge_materials(order: tuple[int, ...], current: MaterialState) -> MaterialState:
    """Keep names that still exist when the material order changes."""
    if not order or tuple(order) == tuple(current.order):
        return current
    kept = {material_id: name for material_id, name in current.names.items() if material_id in order}
    return MaterialState(order=tuple(order), names=kept)


def _read_voxels(handle: Any) -> Any:
    """Lazy indirection so core tests can run without native libraries."""
    from process_models.voxel import read_voxels

    return read_voxels(handle)


def _write_voxels(handle: Any, grid: Any) -> Any:
    """Lazy indirection so core tests can run without native libraries."""
    from process_models.voxel import write_voxels

    return write_voxels(handle, grid)
