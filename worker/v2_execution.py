"""Opt-in worker execution path for v2-claimed steps.

This is the state bridge between the worker loop and :class:`FlowExecutor`.
It keeps one ``ProcessState`` in ``ctx.v2_state``, runs the claimed step, and
publishes the committed state back to ``ctx.domain``.  The legacy worker path
remains the default; it is invalidated explicitly when it mutates the Domain.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from process_models.v2.dispatch import DispatchDecision
from process_models.v2.geometry import register_geometry
from process_models.v2.registry import ProcessRegistry
from worker.process_models_v2 import V2ProfileUnavailable, run_v2_step
from worker.v2_state import WorkerV2State

__all__ = ["EXECUTION_ENV", "execution_enabled", "geometry_assets", "run_v2_claimed_step"]

EXECUTION_ENV = "VIENNAPS_WORKBENCH_V2_EXECUTION"
_GPU_ENV = "VIENNAPS_WORKBENCH_V2_PLUGIN_GPU"


def execution_enabled() -> bool:
    return os.environ.get(EXECUTION_ENV) == "1"


def geometry_assets(ctx: Any, step: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Supply the verified GDS operand builder for one geometry step."""
    if step.get("model") != "gdsBoolean":
        return None
    from worker import simulate

    def build(ps: Any, domain: Any, project: dict[str, Any], raw_step: dict[str, Any], context: Any) -> Any:
        del context
        # Keep the v2 filtered GDS/JSON artifacts separate from the worker's
        # own step-id files.
        return simulate._gds_boolean_operand(
            ps,
            domain,
            project,
            {**raw_step, "id": f"{raw_step['id']}-v2"},
            config=ctx.config,
            run_dir=ctx.run_dir,
        )

    return {"gdsBooleanOperand": build}


def run_v2_claimed_step(
    ctx: Any,
    index: int,
    step: dict[str, Any],
    decision: DispatchDecision,
    registry: ProcessRegistry,
    *,
    field_sequence_end: bool = True,
) -> dict[str, Any]:
    """Execute one claimed process/geometry step and return worker snapshot metadata."""
    if not decision.claimed or decision.spec is None or decision.params is None:
        raise RuntimeError(f"step {step['id']!r} is not a runnable v2 claim")
    bridge = WorkerV2State.from_context(ctx)
    previous_tops = ctx.snapshots[-1].get("materialTops") if ctx.snapshots else None
    try:
        outcome = run_v2_step(
            ctx,
            step,
            ctx.domain,
            registry=registry,
            allow_gpu=os.environ.get(_GPU_ENV) == "1",
            previous_tops=previous_tops,
            assets=geometry_assets(ctx, step),
            state=bridge.state,
            field_sequence_end=field_sequence_end,
        )
    except V2ProfileUnavailable as error:
        # A claimed step that cannot honour its resolved profile is a hard
        # error in execution mode; there is no silent CPU fallback.
        raise RuntimeError(f"v2 execution could not honour step {step['id']!r}: {error}") from error
    bridge.commit(ctx, outcome.state)

    plan = outcome.plan
    kernel = plan.engine.profile.kernel or "not-applicable"
    actual_engine = "not-applicable" if decision.kind in ("field", "geometry") else kernel
    ctx.actual_execution.append({"stepId": step["id"], "engine": actual_engine, "seed": ctx.run_seed})
    ctx.event(
        {
            "type": "step-configured",
            "stepId": step["id"],
            "operation": step.get("operation", "native"),
            "model": step.get("model"),
            "noOp": False,
            "actualEngine": actual_engine,
            "engineSelectionReason": "v2 execution registry claim",
            "requestedEnginePreference": ctx.engine_preference,
            "actualSeed": ctx.run_seed,
            "actualRate": None,
            "duration": step.get("duration", 0.0),
            "actualParameters": outcome.metadata,
            "v2Plan": plan.identity(),
        }
    )
    metadata = ctx.save_snapshot(
        ctx.domain,
        step_id=step["id"],
        step_name=step["name"],
        step_index=index,
        requested=ctx.project,
        step=step,
        previous_tops=previous_tops,
        extra={"v2Plan": plan.identity(), "v2Metadata": outcome.metadata},
    )
    metadata["nativeState"] = ctx.save_native_state(ctx.domain, step["id"])
    return metadata


def prepare_registry(registry: ProcessRegistry) -> ProcessRegistry:
    """Ensure the caller-supplied registry also has the built-in geometry specs."""
    register_geometry(registry)
    return registry
