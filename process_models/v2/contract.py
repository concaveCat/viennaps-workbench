"""Stable process identity and implementation registration for v2.

The v2 split mirrors the two questions a caller can ask:

* :class:`ProcessSpec` - *what is this process* (id, version, label, canonical
  parameter model, supported dimensions/operations).
* :class:`VariantRegistration` - *which implementation of it is available*,
  backed by an :class:`EngineRequirement`.

A project step references ``process_id`` and optionally ``variant_id``; it
never contains a module path or callable. :class:`ProcessRegistry` is the only
place that turns those IDs into an implementation.

Phase-1 boundaries
------------------
Fields, geometry constraints, and determinism are declarative metadata. The
resolver enforces reads/writes/dimensions; nothing else is a runtime guarantee.
No project JSON schema is imported here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Protocol

from pydantic import BaseModel

from .engine import EngineKind, EngineRequirement
from .state import MaterialState, StateKind

__all__ = [
    "EngineVariant",
    "Issue",
    "ModelDescriptor",
    "ProcessContext",
    "ProcessSpec",
    "VariantCapabilities",
    "VariantDescriptor",
    "VariantRegistration",
]

Severity = Literal["error", "warning"]
Determinism = Literal["strict", "tolerance"]


@dataclass(frozen=True)
class Issue:
    """One validation finding.

    ``path`` points at the offending parameter (for example ``"thickness"``)
    so callers can attach the issue to the right field.
    """

    path: str
    message: str
    severity: Severity = "error"


@dataclass(frozen=True)
class ProcessContext:
    """Read-only project/step context used for validation.

    ``project`` and ``step`` are raw JSON mappings, not typed project models,
    so this package does not depend on the backend schema.
    """

    dimension: int
    grid_delta: float
    project: Mapping[str, Any] = field(default_factory=dict)
    step: Mapping[str, Any] = field(default_factory=dict)
    materials: MaterialState = field(default_factory=lambda: MaterialState(()))

    def __post_init__(self) -> None:
        if int(self.dimension) not in (2, 3):
            raise ValueError(f"dimension must be 2 or 3, got {self.dimension!r}")
        grid_delta = float(self.grid_delta)
        if not math.isfinite(grid_delta) or grid_delta <= 0.0:
            raise ValueError(f"grid_delta must be a positive finite number, got {self.grid_delta!r}")
        if not isinstance(self.materials, MaterialState):
            raise ValueError("materials must be a MaterialState")
        object.__setattr__(self, "dimension", int(self.dimension))
        object.__setattr__(self, "grid_delta", grid_delta)


@dataclass(frozen=True)
class VariantCapabilities:
    """Declarative limits of a variant.

    ``reads``/``writes`` drive the resolver's state checks and the adapter's
    prepare/commit behaviour. ``geometry_constraints`` is descriptive only in
    phase 1 (for example ``("height-field",)``).
    """

    dimensions: tuple[int, ...]
    reads: frozenset[StateKind]
    writes: frozenset[StateKind]
    geometry_constraints: tuple[str, ...] = ()
    supports_fields: bool = False
    supports_masks: bool = False
    experimental: bool = False
    mutates_input: bool = False
    determinism: Determinism = "strict"

    def __post_init__(self) -> None:
        dimensions = tuple(int(dimension) for dimension in self.dimensions)
        if not dimensions:
            raise ValueError("dimensions must not be empty")
        if any(dimension not in (2, 3) for dimension in dimensions):
            raise ValueError(f"dimensions must be a subset of (2, 3), got {dimensions!r}")
        if self.determinism not in ("strict", "tolerance"):
            raise ValueError(f"unknown determinism {self.determinism!r}")
        reads = frozenset(self.reads)
        writes = frozenset(self.writes)
        known = {"geometry", "fields", "voxels", "materials"}
        unknown = sorted((reads | writes) - known)
        if unknown:
            raise ValueError(f"unknown state kinds {unknown!r}; expected a subset of {sorted(known)!r}")
        object.__setattr__(self, "dimensions", dimensions)
        object.__setattr__(self, "reads", reads)
        object.__setattr__(self, "writes", writes)
        object.__setattr__(self, "geometry_constraints", tuple(self.geometry_constraints))


class EngineVariant(Protocol):
    """One swappable implementation of a process.

    ``capabilities`` must be identical to the value on the corresponding
    :class:`VariantRegistration`; the registry rejects a mismatch.
    """

    variant_id: str
    version: str
    capabilities: VariantCapabilities
    implementation_hash: str | None

    def validate(self, ctx: ProcessContext, params: BaseModel) -> list[Issue]:
        """Return issues for ``params`` in ``ctx``; empty means valid."""

    def execute(self, state: Any, params: BaseModel, ctx: ProcessContext) -> Any:
        """Run one step and return a :class:`process_models.v2.state.StateDelta`."""


@dataclass(frozen=True)
class ProcessSpec:
    """Stable process identity and its canonical parameter model.

    Multiple versions of the same ``id`` may be registered at once. Variants
    are registered separately and are not tied to a single process version.
    """

    id: str
    version: str
    label: str
    params_model: type[BaseModel]
    dimensions: tuple[int, ...]
    operations: tuple[str, ...]
    description: str = ""

    def __post_init__(self) -> None:
        for name in ("id", "version", "label"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if not (isinstance(self.params_model, type) and issubclass(self.params_model, BaseModel)):
            raise TypeError("params_model must be a pydantic BaseModel subclass")
        dimensions = tuple(int(dimension) for dimension in self.dimensions)
        if not dimensions or any(dimension not in (2, 3) for dimension in dimensions):
            raise ValueError(f"dimensions must be a non-empty subset of (2, 3), got {dimensions!r}")
        operations = tuple(str(operation) for operation in self.operations)
        if not operations:
            raise ValueError("operations must not be empty")
        object.__setattr__(self, "dimensions", dimensions)
        object.__setattr__(self, "operations", operations)

    def describe(self) -> ModelDescriptor:
        """Return the public descriptor for this process version.

        ``variants`` is empty here because variants are registered separately;
        :class:`process_models.v2.registry.ProcessRegistry` fills it in.
        """
        return ModelDescriptor(
            id=self.id,
            version=self.version,
            label=self.label,
            dimensions=self.dimensions,
            operations=self.operations,
            variants=(),
            description=self.description,
        )


@dataclass(frozen=True)
class VariantRegistration:
    """One implementation of a process, bound to an engine requirement.

    Registration is independent of the process version: adding a variant does
    not require a new :class:`ProcessSpec`.
    """

    process_id: str
    variant_id: str
    version: str
    variant: EngineVariant
    capabilities: VariantCapabilities
    engine: EngineRequirement
    implementation_hash: str = ""

    def __post_init__(self) -> None:
        for name in ("process_id", "variant_id", "version"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(self.capabilities, VariantCapabilities):
            raise TypeError("capabilities must be a VariantCapabilities")
        variant_capabilities = getattr(self.variant, "capabilities", None)
        if variant_capabilities != self.capabilities:
            raise ValueError(
                f"{self.process_id}: registration capabilities do not match variant {self.variant_id!r} capabilities"
            )
        declared_variant_id = getattr(self.variant, "variant_id", None)
        if declared_variant_id is not None and declared_variant_id != self.variant_id:
            raise ValueError(
                f"registration variant_id {self.variant_id!r} does not match implementation {declared_variant_id!r}"
            )
        declared_version = getattr(self.variant, "version", None)
        if declared_version is not None and declared_version != self.version:
            raise ValueError(
                f"registration version {self.version!r} does not match implementation {declared_version!r}"
            )
        if not isinstance(self.engine, EngineRequirement):
            raise TypeError("engine must be an EngineRequirement")
        if not isinstance(self.implementation_hash, str):
            raise ValueError("implementation_hash must be a string")


@dataclass(frozen=True)
class VariantDescriptor:
    """Public, serializable description of one registered variant."""

    id: str
    version: str
    kind: EngineKind
    engine_id: str
    reads: tuple[StateKind, ...]
    writes: tuple[StateKind, ...]
    experimental: bool
    implementation_hash: str


@dataclass(frozen=True)
class ModelDescriptor:
    """Public, serializable description of one process version.

    Descriptors never contain module paths or callables.
    """

    id: str
    version: str
    label: str
    dimensions: tuple[int, ...]
    operations: tuple[str, ...]
    variants: tuple[VariantDescriptor, ...]
    description: str = ""
