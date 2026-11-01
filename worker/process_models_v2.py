"""Worker-side bridge to the v2 process-model contract.

The v2 native ``isotropic`` plugin is parity-first: it rebuilds the same
worker step dictionary and calls the same helpers in :mod:`worker.simulate`.
This module is the worker-side seam that turns the worker's live
``viennaps`` module, project dict, step dict, and ``ps.Domain`` into a v2
:class:`~process_models.v2.resolver.ExecutionRequest` and back into an
:class:`~process_models.v2.resolver.ExecutionOutcome`.

It is deliberately a *bridge*, not the dispatch switch: ``worker/steps/process.py``
still owns the worker path; a later shadow hook may call
:func:`run_isotropic_v2` next to it and compare both results with
:func:`compare_isotropic_surfaces`.

Import safety
-------------
``process_models.v2`` and the isotropic plugin are imported at module top (the
plugin itself only imports the worker lazily inside ``execute``). The worker's
``simulate`` module is resolved lazily through
:func:`worker.steps.context.simulate_module`, which returns the already-loaded
copy even when the bounded child runs ``simulate.py`` as ``__main__``. That
keeps this module importable from ``worker.steps.process`` without creating a
circular import or a second copy of the worker's module-level native state.

Scope
-----
Only ``rateMode="uniform"`` is bridged here. ``rateMode="material"`` needs the
worker's material-rate table conventions, which the shadow hook excludes for
now; :func:`build_isotropic_params` raises a clear ``ValueError`` instead of
silently running a different process.
"""

from __future__ import annotations

from typing import Any

from process_models.v2.contract import ProcessContext
from process_models.v2.engine import (
    EngineCapabilities,
    StaticEngine,
    StaticEngineProvider,
)
from process_models.v2.native.isotropic import IsotropicParams, register_isotropic
from process_models.v2.registry import ProcessRegistry
from process_models.v2.resolver import (
    ExecutionOutcome,
    ExecutionRequest,
    resolve_execution,
)
from process_models.v2.state import LevelSetState, MaterialState, ProcessState

__all__ = [
    "build_isotropic_params",
    "compare_isotropic_surfaces",
    "material_order",
    "run_isotropic_v2",
]

PROCESS_ID = "isotropic"
VARIANT_ID = "native"
DEFAULT_ENGINE_FINGERPRINT = "worker-v2-isotropic"


def material_order(domain: Any) -> tuple[int, ...]:
    """Return the native material IDs in level-set order.

    This mirrors :func:`worker.simulate.material_map` without importing the
    worker module, so it is safe to call during import-sensitive bootstrap.
    """
    mapping = domain.getMaterialMap()
    return tuple(int(mapping.getMaterialIdAtIdx(index)) for index in range(mapping.size()))


def build_isotropic_params(step: dict[str, Any]) -> IsotropicParams:
    """Build validated v2 parameters from one worker isotropic step.

    Only the uniform rate mode is supported by this bridge. The material rate
    table is a worker convention (signed rates per material, zero default);
    bridging it is a separate change, so it fails loudly here rather than
    running a subtly different process.
    """
    rate_mode = str(step.get("rateMode", "uniform"))
    if rate_mode != "uniform":
        raise ValueError(
            f"worker.process_models_v2 only bridges rateMode='uniform' isotropic steps; got rateMode={rate_mode!r}"
        )

    operation = str(step["operation"])
    values: dict[str, Any] = {
        "operation": operation,
        "rateMagnitude": float(step["rateMagnitude"]),
        "duration": float(step["duration"]),
        "rateMode": rate_mode,
        # The worker derives geometry masks through the plugin; an explicit
        # list here would diverge from the worker path.
        "maskMaterials": [],
    }
    if operation == "deposit":
        values["depositMaterial"] = str(step.get("depositMaterial", "SiO2"))
    ratio = step.get("isotropicTimeStepRatio")
    if ratio is not None:
        values["isotropicTimeStepRatio"] = float(ratio)
    return IsotropicParams(**values)


def run_isotropic_v2(
    ps: Any,
    project: dict[str, Any],
    step: dict[str, Any],
    domain: Any,
    *,
    ps_version: str,
    engine_fingerprint: str = "",
) -> ExecutionOutcome:
    """Run one isotropic step through the v2 registry and return its outcome.

    A fresh registry/provider is built per call: the bridge owns no global
    state and must not depend on another caller's registrations. The plugin
    mutates ``domain`` in place, exactly like the worker path; callers that
    need the old geometry must checkpoint (or duplicate) it first.
    """
    params = build_isotropic_params(step)

    registry = ProcessRegistry()
    register_isotropic(registry)
    provider = StaticEngineProvider(
        [
            StaticEngine(
                capabilities=EngineCapabilities(
                    engine_id="viennaps",
                    version=ps_version,
                    kind="native",
                    backends=("cpu",),
                ),
                handle=ps,
                fingerprint=engine_fingerprint or DEFAULT_ENGINE_FINGERPRINT,
            )
        ]
    )

    context = ProcessContext(
        dimension=int(project["dimension"]),
        grid_delta=float(project["domain"]["gridDelta"]),
        project=project,
        step=step,
    )
    order = material_order(domain)
    state = ProcessState(
        geometry=LevelSetState(
            handle=domain,
            dimension=context.dimension,
            grid_delta=context.grid_delta,
            material_order=order,
        ),
        materials=MaterialState(order=order),
    )
    request = ExecutionRequest(
        process_id=PROCESS_ID,
        variant_id=VARIANT_ID,
        params=params,
        context=context,
        state=state,
    )
    plan = resolve_execution(request, registry, provider)
    return plan.run()


def compare_isotropic_surfaces(old_domain: Any, new_domain: Any, dimension: int) -> dict[str, Any]:
    """Compare two domains by exposed-surface digest and material map.

    The result is a plain JSON-safe dict for run records. A mismatch is data,
    not an exception: the caller decides whether to warn, fail, or accept the
    difference. Point/cell counts are reported for diagnosis; the digest
    already covers them, so they do not add separate mismatch reasons.
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
