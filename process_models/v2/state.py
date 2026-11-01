"""Multi-representation process state for the v2 process-model contract.

Phase-1 boundaries
------------------
The state is deliberately representation-agnostic:

* ``geometry`` carries an engine-specific level-set handle. Native variants
  may mutate that handle in place; the executor is responsible for
  checkpointing before calling variants that declare ``mutates_input``.
* ``fields`` is reserved for future cell-set/field integration. Phase 1 does
  not define any field producers or consumers beyond the type itself.
* ``voxels`` is the dense height-field grid from :mod:`process_models.voxel`.
  Conversions in and out of voxels live in :class:`process_models.v2.adapters.DefaultStateAdapter`,
  never in a variant.
* ``materials`` describes the native material IDs in level-set order. It may
  be empty for field-only steps.

No backend, worker, or project-schema types appear here. ``ProcessState`` and
every nested value object are frozen; adapters build new states with
``dataclasses.replace`` and never mutate an input state. The engine lease is
passed to variants explicitly, not stored in the state.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping

__all__ = [
    "Artifact",
    "ArtifactKind",
    "FieldState",
    "LevelSetState",
    "MaterialState",
    "ProcessState",
    "StateDelta",
    "StateKind",
    "StateMissingError",
    "VoxelState",
]

StateKind = Literal["geometry", "fields", "voxels", "materials"]
ArtifactKind = Literal["surface_mesh", "cell_set", "vtu", "json", "text", "native"]


class StateMissingError(RuntimeError):
    """Raised when a variant/request needs a state representation that is absent."""


@dataclass(frozen=True)
class MaterialState:
    """Native material IDs in level-set order plus optional human names.

    ``order`` may be empty so that field-only steps do not need a material
    map. ``names`` keys must be a subset of ``order``; names are also unique.
    """

    order: tuple[int, ...] = ()
    names: Mapping[int, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        order = tuple(int(material_id) for material_id in self.order)
        if any(material_id < 0 for material_id in order):
            raise ValueError(f"material ids must be >= 0, got {order}")
        if len(set(order)) != len(order):
            raise ValueError(f"material ids must be unique, got {order}")

        names = {int(material_id): name for material_id, name in dict(self.names).items()}
        unknown = sorted(set(names) - set(order))
        if unknown:
            raise ValueError(f"material names reference ids outside order {order}: {unknown}")
        if any(not isinstance(name, str) or not name.strip() for name in names.values()):
            raise ValueError("material names must be non-empty strings")
        if len(set(names.values())) != len(names):
            raise ValueError(f"material names must be unique, got {sorted(names.values())}")

        object.__setattr__(self, "order", order)
        object.__setattr__(self, "names", names)

    def name_for(self, material_id: int) -> str | None:
        """Return the display name for ``material_id`` (or ``None``)."""
        return self.names.get(int(material_id))

    def id_for(self, name: str) -> int | None:
        """Return the material id registered under ``name`` (or ``None``)."""
        for material_id, candidate in self.names.items():
            if candidate == name:
                return material_id
        return None


@dataclass(frozen=True)
class LevelSetState:
    """An engine-specific level-set handle plus the grid metadata it carries.

    ``handle`` is opaque to this package. For native variants it is the
    ViennaPS ``Domain``; the :class:`process_models.v2.engine.EngineLease`
    handed to the variant provides the module/handle needed to operate on it.
    """

    handle: Any
    dimension: int
    grid_delta: float
    material_order: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if int(self.dimension) not in (2, 3):
            raise ValueError(f"dimension must be 2 or 3, got {self.dimension!r}")
        grid_delta = float(self.grid_delta)
        if not math.isfinite(grid_delta) or grid_delta <= 0.0:
            raise ValueError(f"grid_delta must be a positive finite number, got {self.grid_delta!r}")
        order = tuple(int(material_id) for material_id in self.material_order)
        if any(material_id < 0 for material_id in order):
            raise ValueError(f"material_order must contain ids >= 0, got {order}")
        object.__setattr__(self, "dimension", int(self.dimension))
        object.__setattr__(self, "grid_delta", grid_delta)
        object.__setattr__(self, "material_order", order)


@dataclass(frozen=True)
class FieldState:
    """Named scalar fields.

    Phase 1 keeps this permissive: values are passed through untouched and no
    cell-set integration exists yet. Adapters must not assume JSON values.
    """

    data: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.data, Mapping):
            raise ValueError("field data must be a mapping")
        object.__setattr__(self, "data", dict(self.data))


@dataclass(frozen=True)
class VoxelState:
    """A dense material grid (``process_models.voxel.VoxelGrid``)."""

    grid: Any
    source: str = "height-field-v1"


@dataclass(frozen=True)
class ProcessState:
    """Everything a variant may read.

    The state is immutable. The engine lease is *not* part of it: the executor
    passes the acquired lease to :meth:`EngineVariant.execute` explicitly.
    """

    geometry: LevelSetState | None = None
    fields: FieldState | None = None
    voxels: VoxelState | None = None
    materials: MaterialState = field(default_factory=lambda: MaterialState(()))

    def has(self, kind: StateKind) -> bool:
        """Return whether ``kind`` is populated.

        ``materials`` counts as present only when at least one material id is
        declared; an empty material map is valid for field-only steps.
        """
        if kind == "geometry":
            return self.geometry is not None
        if kind == "fields":
            return self.fields is not None
        if kind == "voxels":
            return self.voxels is not None
        if kind == "materials":
            return bool(self.materials.order)
        raise ValueError(f"unknown state kind {kind!r}")

    def require(self, kind: StateKind) -> Any:
        """Return the representation for ``kind`` or raise :class:`StateMissingError`."""
        if not self.has(kind):
            raise StateMissingError(f"process state is missing {kind!r}")
        if kind == "geometry":
            return self.geometry
        if kind == "fields":
            return self.fields
        if kind == "voxels":
            return self.voxels
        return self.materials


@dataclass(frozen=True)
class Artifact:
    """A named output produced by a step.

    ``path`` is optional so a variant can describe an in-memory artifact that
    the executor later persists.
    """

    kind: ArtifactKind
    name: str
    path: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class StateDelta:
    """The *changes* a variant wants committed.

    ``None`` means "unchanged" for every representation. Artifacts, metadata,
    and warnings are bookkeeping and do not count as a state change.
    """

    geometry: LevelSetState | None = None
    fields: FieldState | None = None
    voxels: VoxelState | None = None
    artifacts: list[Artifact] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        """Return ``True`` when no state representation changes."""
        return self.geometry is None and self.fields is None and self.voxels is None
