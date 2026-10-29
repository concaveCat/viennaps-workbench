"""Conservative artifact-footprint preflight for a normalized project.

The supervisor enforces a hard 512 MiB cap on one run directory
(``backend.supervisor.ARTIFACT_LIMIT`` / ``backend.storage.check_artifact_budget``).
A long run that only reaches that cap while its final snapshot is materialized
is expensive to discover: the user waits for the simulation and then gets a 413
from the download route or a failed snapshot read.

This module estimates that footprint *before* the worker starts. It is a
heuristic, not a measurement. The coefficients were fitted to completed runs in
the local workbench (read-only manifests):

===========================  ==================  ========  =============
domain/grid                  enabled steps       on disk   primary est.
===========================  ==================  ========  =============
1 x 1 um, 0.003 um, GDS      13                  599 MiB   684 MiB
1 x 1 um, 0.004 um, GDS      13                  387 MiB   385 MiB
1 x 1 um, 0.004 um, GDS      11                  244 MiB   320 MiB
1 x 1 um, 0.004 um, GDS      2                    75 MiB    91 MiB
1 x 1 um, 0.002 um, GDS      preview only        139 MiB   132 MiB
5 x 5 um, 0.05 um, 3D fin    7 + volume mesh     108 MiB   166 MiB
===========================  ==================  ========  =============

The estimate models the two contributors that dominate a ViennaPS run:

* one native ``state-<step>.vpsd`` per executed step plus the initial state.
  Its size grows with the lateral grid-cell count (``x*y/gridDelta^2`` in 3D,
  ``x/gridDelta`` in 2D) and with the step index, because every process step
  adds a level set / surface;
* the surface snapshot JSON. The initial snapshot is eager; later snapshots
  are deferred by default (``VIENNAPS_WORKBENCH_LAZY_SNAPSHOTS``) and are
  materialized on first read. The preflight includes the initial snapshot and
  the largest step snapshot, because opening the viewer or the run page reads
  them. Full-snapshot materialization is reported separately as
  ``archiveEstimateBytes`` so a download that would be refused with 413 is
  visible in advance.

Geometry factors are deliberately coarse. GDS is calibrated directly. A fin /
stack / trench / hole / plane is scaled down for native state size (the actual
surface is a small feature inside a large lateral domain) but kept near GDS for
the snapshot mesh (the extracted mesh still spans the full lateral domain).

Every result carries a safety factor and a confidence. A configuration with an
output mode that has no calibration data (``levelSets`` / ``levelSetMeshes``)
is reported as low confidence and is *not* rejected, only warned about, so an
uncertain estimate cannot block a run.
"""
from __future__ import annotations

import math
import os
from typing import Any

MIB = 1024 * 1024

#: Hard application cap enforced by supervisor/storage. Do not change here.
ARTIFACT_CAP_BYTES = 512 * MIB
#: Allow the run, but tell the user it is in the upper quarter of the cap.
WARNING_FRACTION = 0.75
#: Reject before the worker starts above this fraction of the cap.
REJECT_FRACTION = 1.0
#: Multiplier applied to the raw fit. The local calibration residuals are
#: within roughly +/-20 %; this keeps a borderline run on the safe side.
SAFETY_FACTOR = 1.20

# Bytes per lateral grid cell for the native state of step i:
#   state_i = cells * state_geometry_factor * (160 + 18 * i)
# Summed over the initial state and every enabled step this reproduces the
# 0.003 um (448 MiB of states) and 0.004 um (247 MiB) FinFET runs within 4 %.
STATE_BASE_BYTES_PER_CELL = 160
STATE_GROWTH_BYTES_PER_CELL = 18
# Snapshot JSON: initial = 300 cells, step i adds 75 cells. The final snapshot
# of the 13-step runs is 1190-1300 bytes/cell.
INITIAL_SNAPSHOT_BYTES_PER_CELL = 300
STEP_SNAPSHOT_BYTES_PER_CELL = 300
STEP_SNAPSHOT_GROWTH_BYTES_PER_CELL = 75
# Optional outputs. Volume meshes were calibrated on 5 x 5 um / 0.05 um fin
# runs (~500-600 bytes per lateral cell per step). Level-set exports have no
# local calibration and are intentionally generous.
VOLUME_MESH_BYTES_PER_CELL = 600
LEVEL_SET_BYTES_PER_CELL = 600
LEVEL_SET_MESH_BYTES_PER_CELL = 300
NATIVE_METADATA_BYTES_PER_CELL = 32

#: ``state-*.vpsd`` growth per geometry kind. GDS is the calibrated reference.
STATE_GEOMETRY_FACTOR = {
    "gds": 1.0,
    "fin": 0.28,
    "stack": 0.5,
    "trench": 0.35,
    "hole": 0.35,
    "plane": 0.25,
}
#: Snapshot mesh growth per geometry kind. Fin meshes are stored at full
#: float precision and often cover the whole lateral substrate.
SNAPSHOT_GEOMETRY_FACTOR = {
    "gds": 1.0,
    "fin": 2.0,
    "stack": 1.2,
    "trench": 1.2,
    "hole": 1.2,
    "plane": 1.0,
}
GEOMETRY_ONLY_MODELS = {"removeMaterial", "planarize", "boolean"}
MODEL_WEIGHTS = {
    "isotropic": 0.9,
    "singleParticle": 1.0,
    "directional": 1.0,
    "multiParticle": 1.0,
}

CALIBRATION_RUNS = [
    {"shape": "3D GDS 1x1 um, gridDelta 0.003, 13 steps", "onDiskMiB": 599, "estimateMiB": 684},
    {"shape": "3D GDS 1x1 um, gridDelta 0.004, 13 steps", "onDiskMiB": 387, "estimateMiB": 385},
    {"shape": "3D GDS 1x1 um, gridDelta 0.004, 11 steps", "onDiskMiB": 244, "estimateMiB": 320},
    {"shape": "3D GDS 1x1 um, gridDelta 0.004, 2 steps", "onDiskMiB": 75, "estimateMiB": 91},
    {"shape": "3D GDS 1x1 um, gridDelta 0.002, preview only", "onDiskMiB": 139, "estimateMiB": 132},
    {"shape": "3D fin 5x5 um, gridDelta 0.05, 7 steps + volume mesh", "onDiskMiB": 108, "estimateMiB": 166},
]


def lazy_snapshots_enabled() -> bool:
    """Match ``worker.simulate.LAZY_SNAPSHOTS`` without importing the worker."""
    return os.environ.get("VIENNAPS_WORKBENCH_LAZY_SNAPSHOTS") != "0"


def _enabled_steps(project: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for step in project.get("steps") or [] if step.get("enabled")]


def _step_weight(step: dict[str, Any]) -> float:
    model = step.get("model")
    if model in GEOMETRY_ONLY_MODELS:
        return 0.85
    if model == "specialized":
        # Flux-driven ALD/etch adds a surface; native-only models change less.
        return 1.0 if step.get("nativeParameters") is not None else 0.9
    return MODEL_WEIGHTS.get(str(model), 1.0)


def _lateral_cells(project: dict[str, Any]) -> float:
    domain = project.get("domain") or {}
    try:
        delta = float(domain.get("gridDelta") or 0.0)
        extent_x = float(domain.get("xExtent") or 0.0)
    except (TypeError, ValueError):
        return 1.0
    if delta <= 0.0:
        return 1.0
    if int(project.get("dimension") or 2) == 3:
        try:
            extent_y = float(domain.get("yExtent") or 0.0)
        except (TypeError, ValueError):
            extent_y = 0.0
        return max(1.0, extent_x * extent_y / (delta * delta))
    return max(1.0, extent_x / delta)


def _geometry_kind(project: dict[str, Any]) -> str:
    geometry = project.get("geometry")
    if isinstance(geometry, dict) and isinstance(geometry.get("kind"), str):
        return geometry["kind"]
    return "plane"


def _levelset_count(project: dict[str, Any]) -> int:
    """Best-effort level-set count from the initial geometry."""
    geometry = project.get("geometry")
    if not isinstance(geometry, dict):
        return 1
    if geometry.get("kind") == "gds":
        return max(1, len(geometry.get("layers") or []) + 1)
    if geometry.get("kind") == "stack":
        return max(1, len(geometry.get("layers") or []) + 1)
    return 1


def _layer_growth(project: dict[str, Any]) -> float:
    """Mild growth for many mapped GDS / stack layers.

    The calibrated FinFET projects have four mapped layers and eleven level
    sets. More layers mean more native level sets, but the relationship is not
    linear in this model, so this is capped at 1.5x.
    """
    geometry = project.get("geometry")
    if not isinstance(geometry, dict):
        return 1.0
    if geometry.get("kind") not in {"gds", "stack"}:
        return 1.0
    layers = len(geometry.get("layers") or [])
    return min(1.5, 1.0 + max(0, layers - 8) * 0.05)


def _all_snapshot_bytes(cells: float, snapshot_factor: float, layer_factor: float,
                        step_count: int) -> int:
    total = 0.0
    for index in range(max(1, step_count)):
        total += STEP_SNAPSHOT_BYTES_PER_CELL + STEP_SNAPSHOT_GROWTH_BYTES_PER_CELL * index
    return int(round(cells * snapshot_factor * layer_factor * total))


def _optional_outputs(project: dict[str, Any], *, cells: float, step_count: int,
                      all_snapshot_bytes: int) -> tuple[int, list[str]]:
    settings = project.get("outputSettings") or {}
    dimension = int(project.get("dimension") or 2)
    levelsets = _levelset_count(project)
    total = 0.0
    contributors: list[str] = []

    if settings.get("surfaceMeshVtk"):
        # One VTK file per step. The legacy VTK payload is close to the JSON
        # snapshot in size (calibrated on the 5x5 um fin runs).
        total += all_snapshot_bytes
        contributors.append("surfaceMeshVtk")
    if settings.get("volumeMesh") and dimension == 3:
        total += VOLUME_MESH_BYTES_PER_CELL * cells * step_count
        contributors.append("volumeMesh")
    if settings.get("levelSets"):
        total += LEVEL_SET_BYTES_PER_CELL * cells * step_count * levelsets
        contributors.append("levelSets")
    if settings.get("levelSetMeshes"):
        total += LEVEL_SET_MESH_BYTES_PER_CELL * cells * step_count * levelsets
        contributors.append("levelSetMeshes")
    if settings.get("nativeMetadata"):
        total += NATIVE_METADATA_BYTES_PER_CELL * cells * step_count
        contributors.append("nativeMetadata")
    return int(round(total)), contributors


def _confidence(project: dict[str, Any]) -> str:
    settings = project.get("outputSettings") or {}
    # No calibration data for full-grid level-set exports.
    if settings.get("levelSets") or settings.get("levelSetMeshes"):
        return "low"
    geometry = _geometry_kind(project)
    if int(project.get("dimension") or 2) != 3 or geometry not in {"gds", "fin"}:
        return "medium"
    if settings.get("volumeMesh") or settings.get("surfaceMeshVtk") or settings.get("nativeMetadata"):
        return "medium"
    return "high"


def _suggestions(project: dict[str, Any], estimate: dict[str, Any]) -> list[str]:
    suggestions: list[str] = []
    cap = estimate["capBytes"]
    util = estimate["utilization"]
    dimension = int(project.get("dimension") or 2)
    domain = project.get("domain") or {}
    try:
        delta = float(domain.get("gridDelta") or 0.0)
    except (TypeError, ValueError):
        delta = 0.0
    enabled = _enabled_steps(project)
    settings = project.get("outputSettings") or {}

    if delta > 0 and util > WARNING_FRACTION:
        # Footprint scales as 1/delta^2 in 3D and 1/delta in 2D. Target 90 %
        # of the cap so a warning does not immediately return.
        factor = math.sqrt(util / 0.9) if dimension == 3 else util / 0.9
        if factor > 1.01:
            suggestions.append(
                f"increase gridDelta from {delta:g} to at least {delta * factor:.4g} um"
            )
    if len(enabled) > 1 and util > WARNING_FRACTION:
        # State and snapshot totals grow roughly quadratically with step count.
        keep = int(len(enabled) * math.sqrt(0.9 / max(util, 1e-9)))
        disable = max(1, len(enabled) - max(1, keep))
        suggestions.append(
            f"disable or combine at least {disable} of the {len(enabled)} enabled steps"
        )
    optional = sorted(key for key, value in settings.items() if value)
    if optional:
        suggestions.append("disable optional outputs: " + ", ".join(optional))
    if dimension == 3 and util > WARNING_FRACTION:
        suggestions.append("shrink xExtent/yExtent (currently "
                           f"{domain.get('xExtent')} x {domain.get('yExtent')} um)")
    if estimate["archiveEstimateBytes"] > cap and estimate["estimatedBytes"] <= cap:
        suggestions.append("avoid downloading every step snapshot at once; the combined ZIP is over the cap")
    if not suggestions:
        suggestions.append("no change required; the estimate is below the warning threshold")
    return suggestions


def estimate_artifacts(project: dict[str, Any], *, kind: str = "simulate",
                       lazy_snapshots: bool | None = None) -> dict[str, Any]:
    """Return a structured artifact estimate for a normalized project.

    ``kind`` follows the run API: ``preview`` and ``slice`` execute no process
    steps, so only the initial state/snapshot are counted. ``simulate`` counts
    every enabled step. Unknown kinds are treated conservatively as simulate.
    """
    if kind in {"preview", "slice"}:
        process_steps = 0
    else:
        process_steps = len(_enabled_steps(project))
    if lazy_snapshots is None:
        lazy_snapshots = lazy_snapshots_enabled()

    dimension = int(project.get("dimension") or 2)
    geometry = _geometry_kind(project)
    cells = _lateral_cells(project)
    dimension_factor = 0.4 if dimension == 2 else 1.0
    state_factor = STATE_GEOMETRY_FACTOR.get(geometry, 0.5) * dimension_factor
    snapshot_factor = SNAPSHOT_GEOMETRY_FACTOR.get(geometry, 1.2) * dimension_factor
    layer_factor = _layer_growth(project)
    step_count = process_steps + 1  # initial state/snapshot + enabled steps
    growth_units = sum(_step_weight(step) for step in _enabled_steps(project))

    # Native states: one per executed step, growing with the step index.
    state_bytes = int(round(
        cells * state_factor * layer_factor
        * step_count * (STATE_BASE_BYTES_PER_CELL
                        + STATE_GROWTH_BYTES_PER_CELL * (step_count - 1) / 2.0)
    ))
    initial_snapshot_bytes = int(round(
        cells * snapshot_factor * layer_factor * INITIAL_SNAPSHOT_BYTES_PER_CELL
    ))
    largest_step_snapshot_bytes = 0
    if process_steps:
        largest_step_snapshot_bytes = int(round(
            cells * snapshot_factor * layer_factor
            * (STEP_SNAPSHOT_BYTES_PER_CELL + STEP_SNAPSHOT_GROWTH_BYTES_PER_CELL * growth_units)
        ))
    all_snapshots_bytes = _all_snapshot_bytes(cells, snapshot_factor, layer_factor, step_count)
    optional_bytes, optional_kinds = _optional_outputs(
        project, cells=cells, step_count=step_count, all_snapshot_bytes=all_snapshots_bytes
    )

    if lazy_snapshots:
        snapshot_bytes = initial_snapshot_bytes + largest_step_snapshot_bytes
    else:
        # Every step snapshot is written eagerly, matching a worker started
        # with VIENNAPS_WORKBENCH_LAZY_SNAPSHOTS=0.
        snapshot_bytes = all_snapshots_bytes

    raw_primary = state_bytes + snapshot_bytes + optional_bytes
    raw_archive = state_bytes + all_snapshots_bytes + optional_bytes
    estimated_bytes = int(round(raw_primary * SAFETY_FACTOR))
    archive_bytes = int(round(raw_archive * SAFETY_FACTOR))
    confidence = _confidence(project)
    utilization = estimated_bytes / ARTIFACT_CAP_BYTES

    risk = "ok"
    downgraded = False
    if utilization > REJECT_FRACTION:
        if confidence == "low":
            # No calibration for this output mode: warn instead of blocking.
            risk = "warning"
            downgraded = True
        else:
            risk = "reject"
    elif utilization >= WARNING_FRACTION:
        risk = "warning"
    # The archive is informational: the run cap applies to the run directory,
    # not to the ZIP. A ZIP of every materialized snapshot may still be refused
    # with 413 even for an allowed run, so surface it separately instead of
    # turning an otherwise normal run into a warning.
    archive_risk = "warning" if archive_bytes > ARTIFACT_CAP_BYTES else "ok"

    contributors = [
        {"kind": "nativeStates", "bytes": int(round(state_bytes * SAFETY_FACTOR)),
         "detail": f"{step_count} native .vpsd state(s) at {cells:,.0f} lateral cells"},
        {"kind": "initialSnapshot", "bytes": int(round(initial_snapshot_bytes * SAFETY_FACTOR)),
         "detail": "eager initial surface snapshot"},
        {"kind": "stepSnapshots", "bytes": int(round((snapshot_bytes - initial_snapshot_bytes) * SAFETY_FACTOR)),
         "detail": ("largest deferred snapshot (materialized on first read)" if lazy_snapshots
                    else "all eager step snapshots")},
    ]
    if optional_bytes:
        contributors.append({
            "kind": "optionalOutputs", "bytes": int(round(optional_bytes * SAFETY_FACTOR)),
            "detail": ", ".join(optional_kinds),
        })
    if archive_risk == "warning":
        contributors.append({
            "kind": "archiveAllSnapshots", "bytes": archive_bytes,
            "detail": "downloading the ZIP materializes every deferred snapshot",
        })

    estimate = {
        "schemaVersion": 1,
        "estimatedBytes": estimated_bytes,
        "estimatedMiB": round(estimated_bytes / MIB, 1),
        "archiveEstimateBytes": archive_bytes,
        "archiveEstimateMiB": round(archive_bytes / MIB, 1),
        "capBytes": ARTIFACT_CAP_BYTES,
        "capMiB": ARTIFACT_CAP_BYTES // MIB,
        "utilization": round(utilization, 3),
        "risk": risk,
        "archiveRisk": archive_risk,
        "confidence": confidence,
        "rejectionDowngraded": downgraded,
        "lazySnapshots": lazy_snapshots,
        "kind": kind,
        "safetyFactor": SAFETY_FACTOR,
        "warningFraction": WARNING_FRACTION,
        "rejectFraction": REJECT_FRACTION,
        "contributors": contributors,
        "suggestions": [],
    }
    estimate["suggestions"] = _suggestions(project, estimate)
    return estimate


def should_reject(estimate: dict[str, Any]) -> bool:
    return estimate.get("risk") == "reject"


def warning_message(estimate: dict[str, Any]) -> str | None:
    if estimate.get("risk") == "ok":
        return None
    utilization = estimate.get("utilization", 0.0)
    percent = int(round(utilization * 100))
    if estimate.get("risk") == "reject":
        prefix = (f"Artifact preflight estimates {estimate['estimatedMiB']:.0f} MiB "
                  f"({percent}% of the {estimate['capMiB']} MiB run cap), above the cap. "
                  "POST /api/runs will reject this project before the worker starts.")
    elif estimate.get("rejectionDowngraded"):
        prefix = (f"Artifact preflight estimates {estimate['estimatedMiB']:.0f} MiB "
                  f"({percent}% of the {estimate['capMiB']} MiB run cap), above the cap. "
                  "This configuration uses an uncalibrated output mode; the run is allowed "
                  "with a warning instead of being rejected.")
    else:
        prefix = (f"Artifact preflight estimates {estimate['estimatedMiB']:.0f} MiB "
                  f"({percent}% of the {estimate['capMiB']} MiB run cap).")
    details = []
    if estimate.get("archiveEstimateBytes", 0) > estimate.get("capBytes", 0):
        details.append(
            f"Downloading every step snapshot is estimated at "
            f"{estimate['archiveEstimateMiB']:.0f} MiB and may be refused with HTTP 413."
        )
    suggestions = estimate.get("suggestions") or []
    if suggestions:
        details.append("Suggestions: " + "; ".join(suggestions) + ".")
    return " ".join([prefix, *details])


def rejection_detail(estimate: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": "artifact-budget-preflight",
        "message": warning_message(estimate),
        "estimatedBytes": estimate["estimatedBytes"],
        "capBytes": estimate["capBytes"],
        "suggestions": estimate["suggestions"],
        "artifactEstimate": estimate,
    }
