"""Process-model contract v2.

Public surface:

* :mod:`process_models.v2.state` - multi-representation state and deltas.
* :mod:`process_models.v2.engine` - engine requirements, leases, providers.
* :mod:`process_models.v2.contract` - process specs, variant registrations,
  capabilities, descriptors, issues, and context.
* :mod:`process_models.v2.registry` - explicit ID-based registry and hashing.
* :mod:`process_models.v2.resolver` - ``resolve_execution`` and plans.
* :mod:`process_models.v2.adapters` - state representation adapters.

Plugins (native isotropic, surrogate voxel probe) live in subpackages and must
be registered explicitly; importing this package never imports native
libraries. Projects reference stable process/variant IDs only - never Python
modules, paths, or callables.
"""

from __future__ import annotations

from .adapters import DefaultStateAdapter, StateAdapter
from .contract import (
    EngineVariant,
    Issue,
    ModelDescriptor,
    ProcessContext,
    ProcessSpec,
    VariantCapabilities,
    VariantDescriptor,
    VariantRegistration,
)
from .engine import (
    Backend,
    Engine,
    EngineCapabilities,
    EngineKind,
    EngineLease,
    EngineProvider,
    EngineRequirement,
    EngineUnavailableError,
    StaticEngine,
    StaticEngineProvider,
    version_satisfies,
)
from .registry import ProcessRegistry, ResolvedVariant, compute_implementation_hash
from .resolver import (
    ExecutionOutcome,
    ExecutionPlan,
    ExecutionRequest,
    ExecutionResolutionError,
    execute_plan,
    resolve_execution,
    run_step,
)
from .state import (
    Artifact,
    ArtifactKind,
    FieldState,
    LevelSetState,
    MaterialState,
    ProcessState,
    StateDelta,
    StateKind,
    StateMissingError,
    VoxelState,
)

__all__ = [
    "Artifact",
    "ArtifactKind",
    "Backend",
    "DefaultStateAdapter",
    "Engine",
    "EngineCapabilities",
    "EngineKind",
    "EngineLease",
    "EngineProvider",
    "EngineRequirement",
    "EngineUnavailableError",
    "EngineVariant",
    "ExecutionOutcome",
    "ExecutionPlan",
    "ExecutionRequest",
    "ExecutionResolutionError",
    "FieldState",
    "Issue",
    "LevelSetState",
    "MaterialState",
    "ModelDescriptor",
    "ProcessContext",
    "ProcessRegistry",
    "ProcessSpec",
    "ProcessState",
    "ResolvedVariant",
    "StateAdapter",
    "StateDelta",
    "StateKind",
    "StateMissingError",
    "StaticEngine",
    "StaticEngineProvider",
    "VariantCapabilities",
    "VariantDescriptor",
    "VariantRegistration",
    "VoxelState",
    "compute_implementation_hash",
    "execute_plan",
    "resolve_execution",
    "run_step",
    "version_satisfies",
]
