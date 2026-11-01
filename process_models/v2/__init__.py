"""Process-model contract v2.

Public surface:

* :mod:`process_models.v2.state` - multi-representation state, deltas, and
  structured step outputs (``StepResult``/``OutputSet``).
* :mod:`process_models.v2.engine` - engine requirements, execution profiles,
  leases, and providers.
* :mod:`process_models.v2.state_backend` - engine-neutral checkpoint/write/load
  and material-registry boundary.
* :mod:`process_models.v2.cellset` - on-demand native cell-set field domain,
  its canonical config, and the ``ensure``/``snapshot``/``rehydrate`` protocol.
* :mod:`process_models.v2.contract` - process specs, variant registrations,
  capabilities, descriptors, issues, and context.
* :mod:`process_models.v2.registry` - explicit ID-based registry and hashing.
* :mod:`process_models.v2.resolver` - ``resolve_execution`` and plans.
* :mod:`process_models.v2.adapters` - state representation adapters.
* :mod:`process_models.v2.storage` - on-disk state/output/plan persistence.
* :mod:`process_models.v2.executor` - multi-step ``FlowExecutor``.
* :mod:`process_models.v2.conformance` - mechanical variant conformance checks.
* :mod:`process_models.v2.output_schema` - JSON-serializable output contract.

Installed plugins live in ``plugins/<id>/`` next to their ``plugin.toml`` and
are loaded by :mod:`process_models.plugins`. The voxel-probe proof remains in
``surrogate/`` and is not installed. Importing this package never imports
native libraries. Projects reference stable process/variant IDs only - never
Python modules, paths, or callables.
"""

from __future__ import annotations

from .adapters import DefaultStateAdapter, StateAdapter
from .cellset import (
    CELLSET_CONVERSION,
    CellSetBackend,
    CellSetBackendError,
    CellSetCapabilities,
    CellSetConfig,
)
from .conformance import ConformanceReport, check_variant
from .contract import (
    EngineVariant,
    Issue,
    ModelDescriptor,
    ProcessContext,
    ProcessSpec,
    StateProfile,
    VariantCapabilities,
    VariantDescriptor,
    VariantRegistration,
)
from .engine import (
    Backend,
    ComputePolicy,
    Engine,
    EngineCapabilities,
    EngineKind,
    EngineLease,
    EngineProvider,
    EngineRequirement,
    EngineUnavailableError,
    ExecutionProfile,
    StaticEngine,
    StaticEngineProvider,
    version_satisfies,
)
from .executor import FlowExecutor, FlowRun, FlowStep
from .materials import MaterialRegistry, MaterialRegistryError, StaticMaterialRegistry
from .output_schema import output_contract_schema
from .registry import ProcessRegistry, ResolvedVariant, compute_implementation_hash
from .resolver import (
    ExecutionOutcome,
    ExecutionPlan,
    ExecutionRequest,
    ExecutionResolutionError,
    StateContract,
    execute_plan,
    resolve_execution,
    run_step,
)
from .state import (
    Artifact,
    ArtifactKind,
    CellSetState,
    Field,
    FieldAssociation,
    GridSpec,
    Layer,
    LayerState,
    LevelSetState,
    MaterialState,
    MetrologyRecord,
    OutputSet,
    ProcessState,
    StateDelta,
    StateKind,
    StateMissingError,
    StepResult,
    VoxelState,
)
from .state_backend import (
    GeometryRef,
    HandleStateBackend,
    StateBackend,
    StateBackendError,
    StateCapabilities,
)
from .storage import StateStore, StateStoreError

__all__ = [
    "Artifact",
    "ArtifactKind",
    "Backend",
    "CELLSET_CONVERSION",
    "CellSetBackend",
    "CellSetBackendError",
    "CellSetCapabilities",
    "CellSetConfig",
    "CellSetState",
    "ComputePolicy",
    "ConformanceReport",
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
    "ExecutionProfile",
    "ExecutionRequest",
    "ExecutionResolutionError",
    "Field",
    "FieldAssociation",
    "FlowExecutor",
    "FlowRun",
    "FlowStep",
    "GeometryRef",
    "GridSpec",
    "HandleStateBackend",
    "Issue",
    "Layer",
    "LayerState",
    "LevelSetState",
    "MaterialRegistry",
    "MaterialRegistryError",
    "MaterialState",
    "MetrologyRecord",
    "ModelDescriptor",
    "OutputSet",
    "ProcessContext",
    "ProcessRegistry",
    "ProcessSpec",
    "ProcessState",
    "ResolvedVariant",
    "StateAdapter",
    "StateBackend",
    "StateBackendError",
    "StateCapabilities",
    "StateContract",
    "StateDelta",
    "StateKind",
    "StateMissingError",
    "StateProfile",
    "StateStore",
    "StaticMaterialRegistry",
    "StateStoreError",
    "StaticEngine",
    "StaticEngineProvider",
    "StepResult",
    "VariantCapabilities",
    "VariantDescriptor",
    "VariantRegistration",
    "VoxelState",
    "check_variant",
    "compute_implementation_hash",
    "execute_plan",
    "output_contract_schema",
    "resolve_execution",
    "run_step",
    "version_satisfies",
]
