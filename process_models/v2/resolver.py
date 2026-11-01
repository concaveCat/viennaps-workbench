"""Execution resolution for v2 process models.

``resolve_execution`` is the admission gate between a saved project step and a
concrete implementation. It performs only cheap, deterministic checks and
acquires an engine lease. It never calls ``variant.execute``: the executor
does that with the returned :class:`ExecutionPlan`, then calls the adapter's
``commit``.

Phase-1 boundaries
------------------
No scheduling, persistence, retries, or GPU arbitration. Engine acquisition is
strict; an unavailable engine is an error and never silently falls back to
another version, backend, or implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel

from .adapters import DefaultStateAdapter
from .contract import Issue, ProcessContext, ProcessSpec, VariantRegistration
from .engine import EngineLease, EngineProvider, EngineUnavailableError
from .registry import ProcessRegistry
from .state import Artifact, ProcessState, StateDelta, StateKind

if TYPE_CHECKING:
    from .adapters import StateAdapter

__all__ = [
    "ExecutionOutcome",
    "ExecutionPlan",
    "ExecutionRequest",
    "ExecutionResolutionError",
    "execute_plan",
    "resolve_execution",
    "run_step",
]


class ExecutionResolutionError(RuntimeError):
    """Raised when a request cannot be mapped to a runnable execution plan."""


@dataclass(frozen=True)
class ExecutionRequest:
    """Everything needed to admit one step.

    ``params`` must already be an instance of the process's ``params_model``;
    the resolver does not coerce or re-validate Pydantic input.
    """

    process_id: str
    variant_id: str
    params: BaseModel
    context: ProcessContext
    state: ProcessState
    expected_process_version: str | None = None
    expected_variant_version: str | None = None


@dataclass(frozen=True)
class ExecutionOutcome:
    """The committed state plus the delta and plan that produced it.

    The executor keeps ``delta.metadata``, ``delta.warnings`` and
    ``delta.artifacts`` for the run record; the state adapter only commits the
    representation changes.
    """

    plan: ExecutionPlan
    state: ProcessState
    delta: StateDelta

    @property
    def metadata(self) -> dict[str, object]:
        return self.delta.metadata

    @property
    def warnings(self) -> list[str]:
        return self.delta.warnings

    @property
    def artifacts(self) -> list[Artifact]:
        return list(self.delta.artifacts)


@dataclass(frozen=True)
class ExecutionPlan:
    """A resolved, engine-backed execution without side effects.

    The plan owns the :class:`ExecutionRequest` it was resolved from, so
    :meth:`run` and :func:`execute_plan` never need duplicate parameters.
    """

    request: ExecutionRequest
    process: ProcessSpec
    variant: VariantRegistration
    engine: EngineLease
    state_adapter: StateAdapter
    process_version: str
    variant_version: str
    implementation_hash: str
    engine_version: str
    engine_fingerprint: str

    def identity(self) -> dict[str, str]:
        """Return the stable, JSON-safe identity of this plan.

        These keys are the replay/caching contract: two plans with the same
        identity ran the same process version through the same implementation
        on the same engine build, with the same adapter.
        """
        return {
            "process_id": self.process.id,
            "process_version": self.process_version,
            "variant_id": self.variant.variant_id,
            "variant_version": self.variant_version,
            "implementation_hash": self.implementation_hash,
            "engine_id": self.engine.engine_id,
            "engine_version": self.engine_version,
            "engine_fingerprint": self.engine_fingerprint,
            "adapter": self.state_adapter.id,
        }

    def run(self) -> ExecutionOutcome:
        """Execute this plan, equivalent to ``execute_plan(self)``."""
        return execute_plan(self)


def resolve_execution(
    request: ExecutionRequest,
    registry: ProcessRegistry,
    provider: EngineProvider,
    adapter: StateAdapter | None = None,
) -> ExecutionPlan:
    """Resolve ``request`` to an :class:`ExecutionPlan` or raise.

    Checks, in order: registry identity, parameter type, dimension, required
    state representations, material-order consistency, variant validation, and
    engine availability. A failed check raises :class:`ExecutionResolutionError`
    without retrying a different variant or engine.
    """
    resolved = registry.get_variant(
        request.process_id,
        request.variant_id,
        expected_process_version=request.expected_process_version,
        expected_variant_version=request.expected_variant_version,
    )
    process = resolved.process
    variant = resolved.variant
    capabilities = variant.capabilities

    if not isinstance(request.params, process.params_model):
        raise ExecutionResolutionError(
            f"process {process.id!r} version {process.version!r} expects params "
            f"{process.params_model.__name__}, got {type(request.params).__name__}"
        )

    dimension = request.context.dimension
    if dimension not in capabilities.dimensions:
        raise ExecutionResolutionError(
            f"variant {process.id!r}/{variant.variant_id!r} does not support dimension {dimension}; "
            f"supported dimensions are {list(capabilities.dimensions)}"
        )

    _check_geometry_grid(request.state, request.context)
    selected_adapter = adapter or DefaultStateAdapter()
    missing = sorted(kind for kind in capabilities.reads if not _state_provides(request.state, kind, selected_adapter))
    if missing:
        raise ExecutionResolutionError(
            f"variant {process.id!r}/{variant.variant_id!r} requires state kinds {missing}, "
            f"but the process state does not provide them"
        )

    _check_materials(request.state)

    issues = list(variant.variant.validate(request.context, request.params))
    errors = [issue for issue in issues if issue.severity == "error"]
    if errors:
        raise ExecutionResolutionError(_format_issues(process.id, variant.variant_id, errors))

    try:
        lease = provider.acquire(variant.engine)
    except EngineUnavailableError as error:
        raise ExecutionResolutionError(
            f"variant {process.id!r}/{variant.variant_id!r} requires engine "
            f"{variant.engine.kind!r} {variant.engine.engine_id!r} "
            f"[{variant.engine.min_version!r}, {variant.engine.max_version!r}]: {error}"
        ) from error

    return ExecutionPlan(
        request=request,
        process=process,
        variant=variant,
        engine=lease,
        state_adapter=selected_adapter,
        process_version=resolved.process_version,
        variant_version=resolved.variant_version,
        implementation_hash=resolved.implementation_hash,
        engine_version=lease.engine_version,
        engine_fingerprint=lease.fingerprint,
    )


def execute_plan(plan: ExecutionPlan) -> ExecutionOutcome:
    """Run one resolved plan end to end.

    This is the single execution entry point: it uses only the plan's own
    request (state, params, context), passes the acquired engine lease to the
    variant explicitly, materializes any derived representation, and commits
    the returned delta. It deliberately does not checkpoint or roll back;
    variants that declare ``mutates_input=True`` must be checkpointed by the
    caller before this function is invoked.
    """
    request = plan.request
    if not isinstance(request.params, plan.process.params_model):
        raise ExecutionResolutionError(
            f"process {plan.process.id!r} version {plan.process_version!r} expects params "
            f"{plan.process.params_model.__name__}, got {type(request.params).__name__}"
        )

    prepared = plan.state_adapter.prepare(request.state, plan.variant.capabilities)
    delta = plan.variant.variant.execute(prepared, request.params, request.context, plan.engine)
    if not isinstance(delta, StateDelta):
        raise ExecutionResolutionError(
            f"variant {plan.process.id!r}/{plan.variant.variant_id!r} returned "
            f"{type(delta).__name__}, expected StateDelta"
        )
    committed = plan.state_adapter.commit(prepared, delta, plan.variant.capabilities)
    return ExecutionOutcome(plan=plan, state=committed, delta=delta)


def run_step(
    registry: ProcessRegistry,
    provider: EngineProvider,
    process_id: str,
    variant_id: str,
    params: BaseModel,
    context: ProcessContext,
    state: ProcessState,
    *,
    expected_process_version: str | None = None,
    expected_variant_version: str | None = None,
    adapter: StateAdapter | None = None,
) -> ExecutionOutcome:
    """Resolve and execute one step in a single call.

    This is the convenient entry point for callers that do not need to inspect
    or cache the :class:`ExecutionPlan`. Admission checks are unchanged because
    resolution goes through :func:`resolve_execution`.
    """
    request = ExecutionRequest(
        process_id=process_id,
        variant_id=variant_id,
        params=params,
        context=context,
        state=state,
        expected_process_version=expected_process_version,
        expected_variant_version=expected_variant_version,
    )
    return execute_plan(resolve_execution(request, registry, provider, adapter=adapter))


def _check_geometry_grid(state: ProcessState, context: ProcessContext) -> None:
    """Reject geometry that does not match the request's dimension/grid."""
    geometry = state.geometry
    if geometry is None:
        return
    if int(geometry.dimension) != int(context.dimension):
        raise ExecutionResolutionError(
            f"state geometry is {geometry.dimension}D but the request context is {context.dimension}D"
        )
    tolerance = 1e-9 * max(1.0, abs(float(context.grid_delta)))
    if abs(float(geometry.grid_delta) - float(context.grid_delta)) > tolerance:
        raise ExecutionResolutionError(
            f"state geometry grid delta {geometry.grid_delta!r} does not match "
            f"the request context grid delta {context.grid_delta!r}"
        )


def _state_provides(state: ProcessState, kind: StateKind, adapter: StateAdapter) -> bool:
    """Return whether ``state`` plus ``adapter`` can supply ``kind``.

    Stored representations always count. A derived read counts only when the
    selected adapter declares it in ``provides`` and its prerequisite state is
    present. Voxels are the only derived read in phase 1: the default adapter
    materializes them from an existing geometry before the variant runs.
    """
    if state.has(kind):
        return True
    if kind != "voxels" or kind not in adapter.provides:
        return False
    return state.has("geometry")


def _check_materials(state: ProcessState) -> None:
    """Reject a state whose material order contradicts its geometry."""
    geometry = state.geometry
    if geometry is None or not geometry.material_order:
        return
    if not state.materials.order:
        return
    if tuple(geometry.material_order) != tuple(state.materials.order):
        raise ExecutionResolutionError(
            f"geometry material order {list(geometry.material_order)} does not match "
            f"state material order {list(state.materials.order)}"
        )


def _format_issues(process_id: str, variant_id: str, issues: list[Issue]) -> str:
    details = "; ".join(f"{issue.path}: {issue.message}" if issue.path else issue.message for issue in issues)
    return f"variant {process_id!r}/{variant_id!r} rejected the request: {details}"
