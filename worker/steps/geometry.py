"""Native geometry operations (extrude, boolean, mask, ...)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from worker.steps.context import RunContext, simulate_module

V2_PLUGIN_SHADOW_ENV = "VIENNAPS_WORKBENCH_V2_PLUGIN_SHADOW"
V2_PLUGIN_APPLY_ENV = "VIENNAPS_WORKBENCH_V2_PLUGIN_APPLY"


def _v2_plugin_shadow_enabled() -> bool:
    return os.environ.get(V2_PLUGIN_SHADOW_ENV) == "1"


def _v2_plugin_apply_enabled() -> bool:
    return os.environ.get(V2_PLUGIN_APPLY_ENV) == "1"


def _geometry_assets(ctx: RunContext, step: dict[str, Any]) -> Mapping[str, Any] | None:
    """Supply the verified GDS operand builder for one geometry step."""
    if step.get("model") != "gdsBoolean":
        return None
    from worker import simulate

    def build(ps: Any, domain: Any, project: dict[str, Any], raw_step: dict[str, Any], context: Any) -> Any:
        del context
        # Keep the shadow's filtered GDS/JSON artifacts separate from the
        # worker path's own step-id files.
        return simulate._gds_boolean_operand(
            ps,
            domain,
            project,
            {**raw_step, "id": f"{raw_step['id']}-v2"},
            config=ctx.config,
            run_dir=ctx.run_dir,
        )

    return {"gdsBooleanOperand": build}


def _run_v2_geometry_shadow(
    ctx: RunContext,
    step: dict[str, Any],
    shadow_domain: Any,
    registry: Any,
    previous_tops: Mapping[str, Any] | None,
    assets: Mapping[str, Any] | None,
) -> None:
    """Run one geometry step through the v2 bridge and compare it with the worker.

    The comparison event is written before a mismatch raises, so a failed run
    keeps the digests, plan identity, and method records that explain it.
    """
    from worker.process_models_v2 import V2ProfileUnavailable, compare_domains, run_v2_step

    try:
        outcome = run_v2_step(
            ctx,
            step,
            shadow_domain,
            registry=registry,
            previous_tops=previous_tops,
            assets=assets,
        )
    except V2ProfileUnavailable as error:
        ctx.event({"type": "v2-plugin-shadow-skipped", "stepId": step["id"], "reason": str(error)})
        return
    shadow_handle = outcome.state.geometry.handle
    comparison = compare_domains(ctx.domain, shadow_handle, int(ctx.project["dimension"]))
    plan_identity = outcome.plan.identity()
    ctx.event(
        {
            "type": "v2-plugin-shadow",
            "stepId": step["id"],
            **comparison,
            "v2Plan": plan_identity,
            "v2Metadata": outcome.metadata,
            "v2Warnings": outcome.warnings,
        }
    )
    if not comparison["matched"]:
        raise RuntimeError(
            f"v2 geometry shadow for step {step['id']!r} did not match the worker path: "
            + "; ".join(comparison["reasons"])
        )
    if not _v2_plugin_apply_enabled():
        return
    ctx.domain = shadow_handle
    ctx.event(
        {
            "type": "v2-plugin-applied",
            "stepId": step["id"],
            "oldDigest": comparison["oldDigest"],
            "newDigest": comparison["newDigest"],
            "v2Plan": plan_identity,
            "v2Metadata": outcome.metadata,
        }
    )


def run_geometry_step(ctx: RunContext, index: int, step: dict[str, Any]) -> dict[str, Any]:
    simulate = simulate_module()
    shadow_domain = None
    v2_registry = None
    if _v2_plugin_shadow_enabled() or _v2_plugin_apply_enabled():
        from process_models.plugins import load_registry
        from process_models.v2.geometry import register_geometry
        from worker.process_models_v2 import find_v2_process

        v2_registry = load_registry()
        register_geometry(v2_registry)
        if find_v2_process(ctx.project, step, registry=v2_registry) is not None:
            shadow_domain = ctx.ps.Domain(ctx.domain)
        else:
            ctx.event(
                {
                    "type": "v2-plugin-shadow-skipped",
                    "stepId": step["id"],
                    "reason": f"no registered v2 geometry or process matches model={step.get('model')!r}",
                }
            )
    previous_tops = ctx.snapshots[-1].get("materialTops")
    actual_parameters = simulate.apply_geometry_operation(
        ctx.ps, ctx.domain, ctx.project, step, previous_tops=previous_tops, config=ctx.config, run_dir=ctx.run_dir
    )
    if shadow_domain is not None:
        _run_v2_geometry_shadow(ctx, step, shadow_domain, v2_registry, previous_tops, _geometry_assets(ctx, step))
    ctx.event(
        {
            "type": "step-configured",
            "stepId": step["id"],
            "operation": step["model"],
            "model": step["model"],
            "noOp": False,
            "actualEngine": "not-applicable",
            "engineSelectionReason": "native geometry operation; no process engine or physical duration",
            "actualSeed": None,
            "actualParameters": actual_parameters,
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
        defer_payload=simulate.LAZY_SNAPSHOTS,
    )
    metadata["nativeState"] = ctx.save_native_state(ctx.domain, step["id"])
    ctx.actual_execution.append({"stepId": step["id"], "engine": "not-applicable", "seed": None})
    return metadata
