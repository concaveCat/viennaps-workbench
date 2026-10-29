# Artifact preflight contract

`backend/preflight.py` estimates whether a normalized project is likely to fill
the 512 MiB per-run artifact cap. The cap itself is unchanged:
`backend.supervisor.ARTIFACT_LIMIT` and `backend.storage.check_artifact_budget`.

## Thresholds

| Constant | Value | Meaning |
|---|---|---|
| `ARTIFACT_CAP_BYTES` | 536870912 (512 MiB) | hard run-directory cap |
| `WARNING_FRACTION` | 0.75 | allowed, returned with `artifactWarning` |
| `REJECT_FRACTION` | 1.0 | `POST /api/runs` returns 422 before the worker starts |
| `SAFETY_FACTOR` | 1.20 | margin applied to the raw fit |

The same values are published under `run.artifactPreflight` in
`docs/resource-policy.json`. A unit test keeps the two copies in sync.

## API fields

`POST /api/projects/validate` (unchanged success contract) adds:

```json
{
  "valid": true,
  "warnings": ["... existing warnings ...", "运行产物预检估算本次运行约 ..."],
  "normalizedProject": {},
  "artifactEstimate": { "...": "..." }
}
```

`POST /api/runs` adds the same `artifactEstimate` object to its 202 response and
an `artifactWarning` string in the warning band. Above the cap it returns 422:

```json
{
  "detail": {
    "code": "artifact-budget-preflight",
    "message": "运行产物预检估算本次运行约 684 MiB（占 512 MiB 上限的 134%），已超过上限。...",
    "estimatedBytes": 717053952,
    "capBytes": 536870912,
    "suggestions": ["..."],
    "artifactEstimate": { "...": "..." }
  }
}
```

`artifactEstimate` contains `estimatedBytes`, `estimatedMiB`, `utilization`,
`risk` (`ok` / `warning` / `reject`), `confidence` (`high` / `medium` / `low`),
`archiveEstimateBytes` (all deferred snapshots materialized, i.e. the run ZIP),
`lazySnapshots`, `contributors` and `suggestions`.

## Formula and calibration

```
cells            = xExtent*yExtent/gridDelta^2   (3D)
                   xExtent/gridDelta             (2D)
stepCount        = 1 (initial) + enabled steps    (0 process steps for preview/slice)
stateBytes       = cells * stateGeometryFactor * layerFactor
                   * sum(160 + 18*i for i in 0..stepCount-1)
initialSnapshot  = 300 * cells * meshGeometryFactor * layerFactor
largestSnapshot  = (300 + 75*weightedSteps) * cells * meshGeometryFactor * layerFactor
optionalOutputs  = outputSettings contribution (volume mesh: 600 bytes/cell/step;
                   level-set exports: intentionally generous, low confidence)
estimatedBytes   = 1.20 * (stateBytes + initialSnapshot + largestSnapshot + optionalOutputs)
archiveBytes     = 1.20 * (stateBytes + allStepSnapshots + optionalOutputs)
```

Geometry factors are deliberately coarse: GDS is the calibrated reference
(`state=1.0`, `mesh=1.0`); a fin uses `state=0.28` (small real feature in a
large lateral domain) and `mesh=2.0` (full-precision mesh over the whole
domain); stack/trench/hole/plane are between those. 2D runs are scaled by 0.4.

Calibration shapes (read-only manifests from completed local runs):

| Shape | On disk | Estimate |
|---|---|---|
| 3D GDS 1x1 um, 0.003 um, 13 steps | 599 MiB | 684 MiB |
| 3D GDS 1x1 um, 0.004 um, 13 steps | 387 MiB | 385 MiB |
| 3D GDS 1x1 um, 0.004 um, 11 steps | 244 MiB | 320 MiB |
| 3D GDS 1x1 um, 0.004 um, 2 steps | 75 MiB | 91 MiB |
| 3D GDS 1x1 um, 0.002 um, preview | 139 MiB | 132 MiB |
| 3D fin 5x5 um, 0.05 um, 7 steps + volume mesh | 108 MiB | 166 MiB |

The first row is the incident that motivated this work: the run itself reached
462 MiB, and materializing its final snapshot took the directory to ~600 MiB.
The preflight rejects that shape. The 0.004 um variant stays under the cap but
is in the warning band. A 0.003 um run on the real 0.48 x 0.50 um cell is well
below the warning threshold.

## Failure policy

The estimator is not a measurement. If a configuration uses an output mode
without local calibration (`levelSets` / `levelSetMeshes`), `confidence` is
`low` and an over-cap estimate is downgraded to a warning (`risk="warning"`,
`rejectionDowngraded=true`) instead of a false rejection. The user is told that
the estimate is uncertain and over the cap.
