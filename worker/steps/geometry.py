"""Native geometry operations (extrude, boolean, mask, ...)."""

from __future__ import annotations

from typing import Any

from worker.steps.context import RunContext, simulate_module


def run_geometry_step(ctx: RunContext, index: int, step: dict[str, Any]) -> dict[str, Any]:
    simulate = simulate_module()
    previous_tops = ctx.snapshots[-1].get("materialTops")
    actual_parameters = simulate.apply_geometry_operation(
        ctx.ps, ctx.domain, ctx.project, step,
        previous_tops=previous_tops,
        config=ctx.config, run_dir=ctx.run_dir)
    ctx.event({"type": "step-configured", "stepId": step["id"], "operation": step["model"], "model": step["model"],
               "noOp": False, "actualEngine": "not-applicable", "engineSelectionReason": "native geometry operation; no process engine or physical duration",
               "actualSeed": None, "actualParameters": actual_parameters})
    metadata = ctx.save_snapshot(ctx.domain, step_id=step["id"], step_name=step["name"], step_index=index, requested=ctx.project,
                                 step=step, previous_tops=previous_tops,
                                 defer_payload=simulate.LAZY_SNAPSHOTS)
    metadata["nativeState"] = ctx.save_native_state(ctx.domain, step["id"])
    ctx.actual_execution.append({"stepId": step["id"], "engine": "not-applicable", "seed": None})
    return metadata
