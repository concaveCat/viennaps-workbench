"""Worker-side bridge to the generic v2 ``FlowExecutor``.

A v2 plugin declares one optional :class:`~process_models.v2.contract.ProcessSpec`
hook: ``parse_step(project, step)``. It is the only plugin knowledge this
module has: nothing here names a concrete process, and adding a process never
touches the worker.

:func:`find_v2_process` asks the plugin registry which process claims a raw
worker step; :func:`run_v2_step` turns the worker's live ``viennaps`` module,
project dict, step dict, and ``ps.Domain`` into a one-step v2 flow and returns
the committed :class:`~process_models.v2.resolver.ExecutionOutcome`.
:func:`compare_domains` is the generic digest/material-map comparison the
shadow hook uses to check the two paths agree.

Import safety
-------------
``process_models.v2`` is imported at module top; it never imports the worker.
The worker's ``simulate`` module is resolved lazily inside
:func:`compare_domains` through :func:`worker.steps.context.simulate_module`,
which returns the already-loaded copy even when the bounded child runs
``simulate.py`` as ``__main__``. That keeps this module importable from
``worker.steps.process`` without creating a circular import or a second copy of
the worker's module-level native state.

Persistence
-----------
Every call writes the executor's artifacts below
``<run_dir>/v2/<step_id>/`` (``steps/<step_id>/{state,outputs,plan}.json``,
optionally ``geometry.vpsd``, plus ``flow.json``). The flow runs on a
checkpointed copy of the handle because the native variant declares
``mutates_input``; the caller's domain is left untouched.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from process_models.plugins import load_registry
from process_models.v2.contract import ProcessContext, ProcessSpec
from process_models.v2.engine import (
    ComputePolicy,
    EngineCapabilities,
    StaticEngine,
    StaticEngineProvider,
)
from process_models.v2.engines.viennaps import ViennaPSMaterialRegistry, material_map
from process_models.v2.executor import FlowExecutor, FlowStep
from process_models.v2.registry import ProcessRegistry
from process_models.v2.resolver import ExecutionOutcome
from process_models.v2.state import LevelSetState, MaterialState, ProcessState
from process_models.v2.state_backend import HandleStateBackend
from process_models.v2.storage import StateStore

__all__ = [
    "V2ProfileUnavailable",
    "compare_domains",
    "compute_policy_for_step",
    "find_v2_process",
    "material_order",
    "run_v2_step",
]

DEFAULT_ENGINE_FINGERPRINT = "worker-v2-plugin"
_CPU_KERNELS = frozenset({"CPU_DISK"})


class V2ProfileUnavailable(RuntimeError):
    """The v2 bridge cannot honour the engine profile resolved for a step.

    The shadow hook catches this and records an auditable skip. It must never
    silently run the step on a different backend/kernel.
    """


def compute_policy_for_step(ctx: Any, step: Mapping[str, Any]) -> ComputePolicy:
    """Return the concrete v2 compute policy for one worker step.

    ``ctx.step_engines`` is populated by the worker before steps run. A step
    without an entry (for example a non-flux model) uses the CPU_DISK pin the
    current plugins declare. GPU kernels carry the worker-selected device so
    the provider can match the exact GPU lease.
    """
    engine = getattr(ctx, "step_engines", {}).get(step["id"])
    if engine in (None, "not-applicable"):
        return ComputePolicy(backend="cpu", kernel="CPU_DISK")
    if engine in {"CPU_DISK", "CPU_TRIANGLE"}:
        return ComputePolicy(backend="cpu", kernel=str(engine))
    if engine in {"GPU_DISK", "GPU_TRIANGLE"}:
        selected = getattr(ctx, "selected_gpu_device", None)
        device = selected.get("uuid") if isinstance(selected, Mapping) else None
        return ComputePolicy(backend="gpu", kernel=str(engine), device=device)
    raise V2ProfileUnavailable(
        f"step {step['id']!r} resolved to unknown engine {engine!r}; the v2 bridge does not guess"
    )


def material_order(domain: Any) -> tuple[int, ...]:
    """Return the native material IDs in level-set order."""
    return tuple(int(material_id) for material_id in material_map(domain))


def find_v2_process(
    project: Mapping[str, Any], step: Mapping[str, Any], *, registry: ProcessRegistry | None = None
) -> ProcessSpec | None:
    """Return the single registered process whose ``parse_step`` accepts ``step``.

    ``None`` means no plugin claims the step. An ambiguous match raises
    ``ValueError`` from the registry rather than guessing. Pass an existing
    ``registry`` to avoid rebuilding and re-verifying the lock twice. The
    returned spec is the one whose ``parse_step`` produced the parameters;
    :func:`run_v2_step` resolves the step again (one parse per call) because it
    needs the parameters, not just the identity.
    """
    if registry is None:
        registry = load_registry()
    resolved = registry.resolve_step(project, step)
    return None if resolved is None else resolved[0]


def run_v2_step(
    ctx: Any,
    step: dict[str, Any],
    domain: Any,
    *,
    registry: ProcessRegistry | None = None,
    allow_gpu: bool = False,
) -> ExecutionOutcome:
    """Run one worker step through the v2 registry and return its outcome.

    ``ctx`` is a :class:`worker.steps.context.RunContext` (or any object with
    ``project``, ``ps``, ``ps_version``, ``native_engine_fingerprint``, and
    ``run_dir``). The registry is loaded once per call unless the caller passes
    one (the worker hook does); the provider and state store are built per
    call, so the bridge owns no global state and cannot inherit another
    caller's registrations. The step is parsed exactly once through
    ``registry.resolve_step``. Only the last step of the one-step flow is
    returned.

    The native variant mutates its input handle, so the executor checkpoints a
    copy through the ViennaPS state backend first: ``domain`` is never modified
    and ``outcome.state.geometry.handle`` is the processed copy.

    GPU execution is explicitly opt-in via ``allow_gpu`` (the worker hook sets
    it from ``VIENNAPS_WORKBENCH_V2_PLUGIN_GPU``). A GPU step requires the
    worker's selected device and CUDA readback, and ``GPU_TRIANGLE`` is gated by
    the same capability evidence as the worker path. A step the bridge cannot
    honour raises :class:`V2ProfileUnavailable` instead of silently running on
    CPU.
    """
    if registry is None:
        registry = load_registry()
    resolved = registry.resolve_step(ctx.project, step)
    if resolved is None:
        raise RuntimeError(f"no registered v2 process matches step {step['id']!r}")
    spec, params = resolved

    variant_id = registry.default_variant_id(spec.id)
    policy = compute_policy_for_step(ctx, step)
    if policy.backend == "gpu":
        if not allow_gpu:
            raise V2ProfileUnavailable(
                f"step {step['id']!r} resolved to {policy.backend}/{policy.kernel!r}; "
                "set VIENNAPS_WORKBENCH_V2_PLUGIN_GPU=1 to enable the v2 GPU execution path"
            )
        selected = getattr(ctx, "selected_gpu_device", None)
        readback = getattr(ctx, "cuda_readback", None)
        if not isinstance(selected, Mapping) or not isinstance(readback, Mapping):
            raise V2ProfileUnavailable(
                f"step {step['id']!r} requires a worker-selected CUDA device and readback for GPU execution"
            )
        if selected.get("uuid") != readback.get("uuid"):
            raise V2ProfileUnavailable(
                f"step {step['id']!r} CUDA readback UUID {readback.get('uuid')!r} "
                f"does not match selected device {selected.get('uuid')!r}"
            )
        try:
            from worker.engine_policy import verify_worker_engine_evidence

            verify_worker_engine_evidence(ctx.project, step, str(policy.kernel))
        except RuntimeError as error:
            raise V2ProfileUnavailable(str(error)) from error
    elif policy.kernel not in _CPU_KERNELS:
        raise V2ProfileUnavailable(
            f"step {step['id']!r} resolved to {policy.backend}/{policy.kernel!r}; "
            f"the v2 worker bridge only provides {sorted(_CPU_KERNELS)!r}"
        )

    context = ProcessContext(
        dimension=int(ctx.project["dimension"]),
        grid_delta=float(ctx.project["domain"]["gridDelta"]),
        project=ctx.project,
        step=step,
        seed=getattr(ctx, "run_seed", None),
    )
    order = material_order(domain)
    state = ProcessState(
        geometry=LevelSetState(
            handle=domain,
            dimension=context.dimension,
            grid_delta=context.grid_delta,
        ),
        materials=MaterialState(order=order),
    )
    state_backend = HandleStateBackend(
        ctx.ps,
        id="viennaps",
        version=str(ctx.ps_version),
        material_registry=ViennaPSMaterialRegistry(ctx.ps),
    )
    engines = [
        StaticEngine(
            capabilities=EngineCapabilities(
                engine_id="viennaps",
                version=ctx.ps_version,
                kind="native",
                backends=("cpu",),
                kernels=tuple(sorted(_CPU_KERNELS)),
            ),
            handle=ctx.ps,
            fingerprint=ctx.native_engine_fingerprint or DEFAULT_ENGINE_FINGERPRINT,
            state_backend=state_backend,
        )
    ]
    selected_device = getattr(ctx, "selected_gpu_device", None)
    if isinstance(selected_device, Mapping) and selected_device.get("uuid"):
        engines.append(
            StaticEngine(
                capabilities=EngineCapabilities(
                    engine_id="viennaps",
                    version=ctx.ps_version,
                    kind="native",
                    backends=("gpu",),
                    kernels=("GPU_DISK", "GPU_TRIANGLE"),
                    devices=(str(selected_device["uuid"]),),
                ),
                handle=ctx.ps,
                fingerprint=ctx.native_engine_fingerprint or DEFAULT_ENGINE_FINGERPRINT,
                state_backend=state_backend,
            )
        )
    provider = StaticEngineProvider(engines)
    store = StateStore(Path(ctx.run_dir) / "v2" / step["id"])
    flow = FlowExecutor(registry, provider, store).run(
        state,
        [
            FlowStep(
                step_id=step["id"],
                process_id=spec.id,
                variant_id=variant_id,
                params=params,
                context=context,
                compute=policy,
            )
        ],
    )
    return flow.outcomes[-1]


def compare_domains(old_domain: Any, new_domain: Any, dimension: int) -> dict[str, Any]:
    """Compare two domains by exposed-surface digest and material map.

    The result is a plain JSON-safe dict for run records and applies to any
    native level-set geometry. A mismatch is data, not an exception: the caller
    decides whether to warn, fail, or accept the difference. Point/cell counts
    are reported for diagnosis; the digest already covers them, so they do not
    add separate mismatch reasons.
    """
    from worker.steps.context import simulate_module

    simulate = simulate_module()
    old_surface = simulate.surface_state(old_domain, dimension)
    new_surface = simulate.surface_state(new_domain, dimension)
    old_map = simulate.material_map(old_domain)
    new_map = simulate.material_map(new_domain)

    reasons: list[str] = []
    if old_surface["digest"] != new_surface["digest"]:
        reasons.append(f"surface digest differs: {old_surface['digest']} != {new_surface['digest']}")
    if old_map != new_map:
        reasons.append(f"material map differs: {old_map} != {new_map}")

    return {
        "matched": not reasons,
        "oldDigest": old_surface["digest"],
        "newDigest": new_surface["digest"],
        "oldMaterialMap": old_map,
        "newMaterialMap": new_map,
        "oldPointCount": int(old_surface["pointCount"]),
        "newPointCount": int(new_surface["pointCount"]),
        "oldCellCount": int(old_surface["cellCount"]),
        "newCellCount": int(new_surface["cellCount"]),
        "reasons": reasons,
    }
