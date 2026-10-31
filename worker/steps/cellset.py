"""Native cell-set field steps (implant / anneal / analysis)."""

from __future__ import annotations

from typing import Any

from worker.steps.context import RunContext, simulate_module


def run_cell_set_step(ctx: RunContext, index: int, step: dict[str, Any]) -> dict[str, Any]:
    # Native 4.7.0 cell-set field steps. They never advance the level
    # sets; the surface snapshot is the unchanged Domain and the real
    # output is the native DenseCellSet plus its compact summary.
    from backend.implant_models import numeric_guard_messages

    simulate = simulate_module()
    guard_messages = numeric_guard_messages(
        step, dimension=ctx.project["dimension"], grid_delta=float(ctx.domain.getGridDelta())
    )
    if guard_messages:
        # Defense in depth: a hand-edited input.json bypasses the API
        # validator, but must never reach a native constructor with a
        # value the schema would reject.
        raise RuntimeError(" ".join(guard_messages))
    cell_set_applied = {
        "implant": simulate.apply_implant_step,
        "anneal": simulate.apply_anneal_step,
        "analysis": simulate.apply_analysis_step,
    }[step["model"]]
    record, cell_set_outputs = cell_set_applied(
        ctx.ps, ctx.domain, ctx.project, step, run_dir=ctx.run_dir, run_id=ctx.run_id, state=ctx.field_state
    )
    ctx.event(
        {
            "type": "step-configured",
            "stepId": step["id"],
            "operation": "native",
            "model": step["model"],
            "noOp": False,
            "actualEngine": "not-applicable",
            "engineSelectionReason": "native cell-set field model; no ray flux engine or surface advection",
            "requestedEnginePreference": ctx.engine_preference,
            "actualSeed": None,
            "actualRate": None,
            "duration": 0.0,
            "actualParameters": record,
        }
    )
    previous_tops = ctx.snapshots[-1].get("materialTops")
    metadata = ctx.save_snapshot(
        ctx.domain,
        step_id=step["id"],
        step_name=step["name"],
        step_index=index,
        requested=ctx.project,
        step=step,
        previous_tops=previous_tops,
        extra={"cellSetProcess": record},
        extra_outputs=cell_set_outputs,
        defer_payload=simulate.LAZY_SNAPSHOTS,
    )
    metadata["nativeState"] = ctx.save_native_state(ctx.domain, step["id"])
    ctx.actual_execution.append({"stepId": step["id"], "engine": "not-applicable", "seed": None})
    return metadata
