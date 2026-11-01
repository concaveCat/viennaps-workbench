"""General multi-step flow executor for v2 process models.

The executor owns the chain

    S_i -> resolve -> checkpoint (mutates_input) -> prepare -> execute
        -> commit -> S_{i+1} -> store

It is a thin, deterministic layer above :func:`resolve_execution` and
:func:`execute_plan`: every step is resolved against the *current* state, its
result is persisted, and the committed state is threaded into the next step.

Checkpointing
-------------
Variants that declare ``VariantCapabilities.mutates_input`` may change the
native geometry handle in place. Before such a step runs, the executor copies
the handle with ``engine.handle.Domain(handle)`` (the ViennaPS copy
constructor) and re-resolves the request against the copy, so
``plan.request.state`` and the plan's execution both point at the copy and the
previous step's state is left untouched. An engine that does not expose
``Domain`` cannot be checkpointed; that is a hard
:class:`ExecutionResolutionError`, never a "mutates_input is probably fine"
fallback.

Persistence
-----------
For every step the executor writes ``state.json``/``fields.npz``,
``outputs.json``, and ``plan.json`` through :class:`StateStore`, plus
``geometry.vpsd`` when the engine exposes ``Writer`` (the step's
``geometry_ref`` is then ``"geometry.vpsd"``, otherwise ``None``). After the
last step one ``flow.json`` records the run::

    {
      "schemaVersion": 1,
      "runDir": "<absolute run directory>",
      "steps": [<plan identity>, ...],       # in execution order
      "versions": {"<engine id>": "<engine version>"}
    }

Per-step process/variant versions already live in the plan identity, so the
flow-level ``versions`` map only summarises which engine builds the run used.

This module does not snapshot, preview, call an API, or acquire external
engines; it is the runtime foundation only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from pydantic import BaseModel

from .adapters import StateAdapter
from .contract import ProcessContext
from .engine import EngineProvider
from .registry import ProcessRegistry
from .resolver import (
    ExecutionOutcome,
    ExecutionPlan,
    ExecutionRequest,
    ExecutionResolutionError,
    execute_plan,
    resolve_execution,
)
from .state import ProcessState
from .storage import StateStore

__all__ = ["FlowExecutor", "FlowRun", "FlowStep"]

FLOW_SCHEMA_VERSION = 1
GEOMETRY_FILE = "geometry.vpsd"


@dataclass(frozen=True)
class FlowStep:
    """One step in a flow: a process variant plus its parameters and context."""

    step_id: str
    process_id: str
    variant_id: str
    params: BaseModel
    context: ProcessContext


@dataclass(frozen=True)
class FlowRun:
    """The result of :meth:`FlowExecutor.run`.

    ``outcomes`` follow the input step order; ``state`` is the state committed
    by the last step (or the initial state for an empty flow).
    """

    run_dir: Path
    outcomes: tuple[ExecutionOutcome, ...]
    state: ProcessState


class FlowExecutor:
    """Resolve, execute, checkpoint, and persist a sequence of v2 steps."""

    def __init__(
        self,
        registry: ProcessRegistry,
        provider: EngineProvider,
        store: StateStore,
        adapter: StateAdapter | None = None,
    ) -> None:
        self._registry = registry
        self._provider = provider
        self._store = store
        self._adapter = adapter

    @property
    def store(self) -> StateStore:
        """Return the state store this executor writes to."""
        return self._store

    def run(self, initial_state: ProcessState, steps: Sequence[FlowStep]) -> FlowRun:
        """Execute ``steps`` from ``initial_state`` and persist every result.

        Duplicate or empty step ids are rejected up front, before anything is
        written to disk.
        """
        ordered = tuple(steps)
        _check_steps(ordered)

        state = initial_state
        outcomes: list[ExecutionOutcome] = []
        identities: list[dict[str, str]] = []
        versions: dict[str, str] = {}

        for step in ordered:
            plan = self._resolve(step, state)
            outcome = execute_plan(plan)

            geometry_ref = self._write_geometry(step, plan, outcome.state)
            self._store.save_state(step.step_id, outcome.state, geometry_ref=geometry_ref)
            self._store.save_outputs(step.step_id, outcome.outputs)
            identities.append(self._store.save_plan(step.step_id, plan))

            outcomes.append(outcome)
            versions.setdefault(plan.engine.engine_id, plan.engine_version)
            state = outcome.state

        self._store.save_flow(
            {
                "schemaVersion": FLOW_SCHEMA_VERSION,
                "runDir": str(self._store.root),
                "steps": identities,
                "versions": versions,
            }
        )
        return FlowRun(run_dir=self._store.root, outcomes=tuple(outcomes), state=state)

    def _resolve(self, step: FlowStep, state: ProcessState) -> ExecutionPlan:
        """Resolve ``step`` against ``state``, checkpointing native geometry first."""
        plan = resolve_execution(self._request(step, state), self._registry, self._provider, adapter=self._adapter)
        if not plan.variant.capabilities.mutates_input:
            return plan

        checkpointed = self._checkpoint(plan, state)
        if checkpointed is state:
            return plan
        return resolve_execution(
            self._request(step, checkpointed),
            self._registry,
            self._provider,
            adapter=self._adapter,
        )

    @staticmethod
    def _request(step: FlowStep, state: ProcessState) -> ExecutionRequest:
        return ExecutionRequest(
            process_id=step.process_id,
            variant_id=step.variant_id,
            params=step.params,
            context=step.context,
            state=state,
        )

    @staticmethod
    def _checkpoint(plan: ExecutionPlan, state: ProcessState) -> ProcessState:
        """Return ``state`` with a copied native geometry handle.

        The copy comes from ``engine.handle.Domain(handle)``. Derived voxels
        belong to the old handle, so they are dropped just like a committed
        geometry delta does. A state without geometry has nothing native to
        checkpoint and is returned unchanged.
        """
        geometry = state.geometry
        if geometry is None:
            return state

        domain_factory = getattr(plan.engine.handle, "Domain", None)
        if domain_factory is None or not callable(domain_factory):
            raise ExecutionResolutionError(
                f"variant {plan.process.id!r}/{plan.variant.variant_id!r} declares "
                f"mutates_input=True but engine {plan.engine.engine_id!r} exposes no Domain "
                "constructor for checkpointing the native geometry"
            )
        copied = domain_factory(geometry.handle)
        if copied is None:
            raise ExecutionResolutionError(
                f"engine {plan.engine.engine_id!r} could not checkpoint the native geometry "
                f"for variant {plan.process.id!r}/{plan.variant.variant_id!r}: Domain() returned None"
            )
        return replace(state, geometry=replace(geometry, handle=copied), voxels=None)

    def _write_geometry(self, step: FlowStep, plan: ExecutionPlan, state: ProcessState) -> str | None:
        """Write ``<step>/geometry.vpsd`` when the engine exposes ``Writer``.

        Returns the file name relative to the step directory, or ``None`` when
        the engine has no writer or the step has no geometry.
        """
        writer_factory = getattr(plan.engine.handle, "Writer", None)
        if writer_factory is None or not callable(writer_factory) or state.geometry is None:
            return None

        path = self._store.step_dir(step.step_id) / GEOMETRY_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        writer_factory(state.geometry.handle, str(path)).apply()
        return path.name


def _check_steps(steps: tuple[FlowStep, ...]) -> None:
    """Reject non-step entries and duplicate/blank step ids."""
    seen: set[str] = set()
    for step in steps:
        if not isinstance(step, FlowStep):
            raise TypeError(f"flow steps must be FlowStep instances, got {type(step).__name__}")
        if not isinstance(step.step_id, str) or not step.step_id.strip():
            raise ValueError(f"step_id must be a non-empty string, got {step.step_id!r}")
        if step.step_id in seen:
            raise ValueError(f"duplicate step_id {step.step_id!r} in one flow")
        seen.add(step.step_id)
