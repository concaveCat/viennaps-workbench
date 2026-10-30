# ViennaPS 4.6.2 → 4.7.0 migration

Status: **4.6.2 remains the production default; 4.7.0 is supported as an
isolated evaluation runtime with explicit version gates.** Work is on
`task/v470-migration` (base `1015266`). This document records the install, the
version-qualified policy, probe evidence, test results and the remaining
blockers for making 4.7.0 the default.

## 1. Install method and versions

The official PyPI 4.7.0 release is used, not the local `/root/ViennaPS`
development snapshot (`7a8b17f`, 2026-09-18, CMake version already 4.7.0). The
upstream release tag is `v4.7.0` = `f65c1dce102cf4c92769f3bf8a22fb1f6d933abe`.

```bash
cd /root/wb-v470
python3.12 -m venv .venv            # isolated, no system-site-packages
.venv/bin/python -m pip install -r requirements-v470.lock
```

| Component | Version | Source |
|---|---|---|
| Python | 3.12.14 | `/usr/bin/python3.12` |
| ViennaPS | 4.7.0 | PyPI wheel `viennaps-4.7.0-cp312-...whl`, 27,712,192 B |
| ViennaLS | 5.10.0 | PyPI wheel `viennals-5.10.0-cp312-...whl`, 8,295,226 B |
| FastAPI / Pydantic / Uvicorn | 0.162.0 / 2.14.2 / 0.54.0 | `requirements-v470.lock` |
| venv size | 127 MiB | CUDA-enabled (`libViennaLS_GPU.so`, `ptx/`) |

`viennaps 4.7.0` metadata requires `ViennaLS>=5.10.0`; `pip check` is clean.
System packages and the main `.venv` were not modified.

## 2. API differences

Full class/member dumps were compared between 4.6.2 and the PyPI 4.7.0 wheel
(102 vs 125 viennaps classes, 83 vs 80 viennals classes).

Added in 4.7.0: `LevelSet`, `MakeGeometry`, `Box`, `Sphere`, `Cylinder`,
`Plane`, `VTKWriter`, `BooleanOperation*`; `IonImplantation`, `Anneal`,
`AnnealMode`, the `Implant*`/`PearsonIV*` models, `ImplantDoseControl`,
`ScreenEnergyLoss`; `NetDoping`, `SheetResistance`; modeldb roots.

Removed or renamed:

| 4.6.2 | 4.7.0 |
|---|---|
| `viennaps.GpuPreconditioner` | removed |
| `viennals.GpuPreconditioner`, `ILU0`, `Jacobi` | removed |
| `viennals.OxidationConstrainedAmbient`, `OxidationMaskBending` | removed |
| `OxidantType.Dry` / `.Wet` | `OxidantType.DRY` / `.WET` |
| `Oxidation.setGpuPreconditioner`, pressure/stokes iteration+tolerance setters, `setSimplePressureRelaxation`, `setSimpleVelocityRelaxation`, all `setMask*` mechanics setters | removed |
| `OxidationDeformationParameters` pressure/stokes/relaxation fields | removed |
| `OxidationMaskParameters` material/contact/anchor/creep fields | removed; `viscosity`, `shearActivationVolume` added |

Added and used by the migration: `DenseCellSet.setScalarData`,
`Oxidation.setInitialTimeStep/setRegrid/setMaskBarrierOnly/...`,
`RayTracingParameters.minRayDistance`.

Unchanged and re-verified: `GDSReader`, `GDSGeometry`, `MakeStack`,
`Planarize`, `DenseCellSet`, `Process`, and all 91 `Material` names/IDs
(max built-in 176, so custom IDs 177–240 remain valid).

### Binding pitfall

4.7.0 binds the 2D call `ps.Domain(gd, xExtent, boundary)` to the
`(gd, xExtent, yExtent)` overload, silently dropping the boundary; 4.6.2 binds
the boundary overload. The keyword form `ps.Domain(gd, xExtent,
boundary=boundary)` is unambiguous on both and is used in `worker/simulate.py`
and all test fixtures.

Unrelated, pre-existing on both versions: an all-reflective 2D/3D domain makes
`MakePlane.apply()` hang; an `INFINITE_BOUNDARY` is required on the open axis.

## 3. Version policy and runtime gates

`backend/engine.py` is the single source of runtime identity:

- `SUPPORTED_ENGINE_VERSIONS = ("4.6.2", "4.7.0")`
- `engine_version()` / `viennals_version()` read the installed metadata
- `EVIDENCE_ENGINE_VERSION = "4.6.2"` keeps archived matrices/replays pinned to
  the run that produced them
- `ENGINE_REGISTRY_VERSION = "4.6.2"` keeps on-disk material catalogs readable
  by the unchanged 4.6.2 service; readers accept both supported versions
- `PATCHED_RUNTIME_VERSIONS = ("4.6.2",)` makes the ABI-bound patched GPU
  runtime explicit

Current-runtime metadata (`/api/materials` catalog, specialized model catalog)
reports `engine_version()`. `backend/materials.py`, `backend/native_build.py`,
`backend/validation.py` and the stage 6/8 assertions no longer hardcode the
installed version. Comments and historical evidence strings that intentionally
describe the 4.6.2 measurement were left intact.

### ALD `surfaceDiffusion` gate (4.7.0)

An **enabled** `steps[].surfaceDiffusion` on `specialized:ald` is rejected
during `validate_project()` on 4.7.0, before a worker or run is created:

```
422 steps[0].surfaceDiffusion: ViennaPS 4.7.0 has a confirmed native hang in
the ALD SurfaceDiffusionParameters stencil; the worker process would never
return. Use the ViennaPS 4.6.2 runtime, or remove or disable this block.
```

- 4.6.2: unchanged. The ALD schema + native classes run 14/14 green on the
  4.6.2 interpreter, including the two golden/knob native cases that are
  excluded on 4.7.0. (The two API persistence cases need the 4.6.2 worker venv
  and were not rerun from this 4.7.0 worktree.)
- 4.7.0: fail fast for enabled blocks; an explicit `{"enabled": false}` token
  stays accepted because it is inert and never reaches the native binding.
- The field is not silently disabled: the request is rejected, and the error
  names the runtime and the native class.
- The **worker builder** calls the same `ald_surface_diffusion_error()` helper
  before constructing `SurfaceDiffusionParameters`. A hand-edited `input.json`
  that bypasses API validation therefore fails fast with the same message
  instead of reaching the hanging native path. The direct-worker regression
  covers 4.7.0 rejection and 4.6.2 acceptance.
- Request hashes for projects without `surfaceDiffusion` are unchanged
  (`test_omitting_the_object_keeps_the_base_request_hashes`).
- Worker-side defense: the worker still refuses an inert ALD configuration and
  a radius above 8 grid intervals if a hand-edited project bypasses validation.

### `advancedOxidation` gate (4.7.0)

`probe_oxidation_setters.py` re-ran the Workbench block on both runtimes
(2D Si/SiO₂, dry, 1000 °C, 0.166 h, 0.03 h steps):

| Control | 4.6.2 | 4.7.0 |
|---|---|---|
| `cflFactor=0.05` | changes surface | **changes surface** |
| `mechanicsTolerance=0.05` | changes surface | **inert** (identical hash) |
| `couplingTolerance=0.2` (δ=0.02) | changes surface | **inert** (identical hash) |
| `simplePressureRelaxation=0.1` | changes surface | **setter removed** |

Consequently, on 4.7.0 a request that **changes** any of those three controls is
rejected with a 422/validation error naming the field, the runtime and the
reason. Materialised defaults remain accepted so normalized projects and
replays stay valid; the worker never sends the removed setter and records
`pressureRelaxationSetter: "removed by the installed viennaps (4.7.0)"`.
On 4.6.2 all four controls keep their previous behaviour.

### Replay / old-run policy

Old 4.6.2 runs are **never reinterpreted** under 4.7.0:

- `/api/runs/replay` returns 422 when `executionRequirements` versions differ,
  with `requiredVersions`, `installedVersions` and the message
  “…Old runs are never reinterpreted under a different runtime: start the
  matching ViennaPS 4.6.2 runtime to replay this run.”
- replay package import returns 409 with the same policy text.
- native Domain state continuation and the supervisor raise
  `ReplayMismatch` naming both version pairs.
- To replay an old run, use the retained 4.6.2 environment. Full dual-runtime
  execution is intentionally not implemented; the rejection path is explicit
  and tested.

Deferred snapshots follow the same rule. `worker/materialize_snapshot.py`
regenerates `<step>.json` from the saved native `.vpsd` on first read; both
`backend/snapshots.ensure_materialized` and the worker compare
`input.json.expectedVersions` against the installed runtime before staging or
reading native state. A mismatch returns **409** on snapshot GET and run
download with `message`, `requiredVersions` and `installedVersions`; no
snapshot is cached. Runs produced by the matching runtime still materialize
normally, and an already-cached JSON is served as stored data without a native
read.

## 4. Bounded probes

All probes run through `scripts/bounded_child.py` (60 s timeout, 4 GiB RSS
polling limit, 1 MiB log) or a direct `timeout` for the deliberate hang repro.
Scripts are in `scripts/migration-probes/`; outputs in `var/probes/`.

| Probe | 4.7.0 | 4.6.2 |
|---|---|---|
| masked Box/Sphere (`probe_masked_distributions.py`) | pass | official pattern fails (`ps.LevelSet` absent); 4.6.2-compatible variant passes |
| manual implant + anneal (`probe_implant_anneal.py`) | pass: dose conserved 78.313341→78.313352, 89.2 % active | n/a (`IonImplantation`/`Anneal` absent) |
| GDS + `MakeStack` + cell set + oxidation (`probe_geometry_oxidation.py`) | pass: 6 structures/7 layers, 500-cell scalar round-trip, 0.01337 µm oxide | pass for the same calls without the 4.7.0-only classes |
| GPU `GPU_DISK` (`probe_gpu.py`) | pass (execution): 64 rays, 265 MiB; CPU/GPU not bit-identical | pass |
| oxidation setters (`probe_oxidation_setters.py`) | cfl works; mechanics/coupling inert; pressure unbound | all four change the surface |
| ALD hang (`probe_ald_hang.py`) | **hangs reliably** (3/3, 10 s timeout) | returns in 0.01 s |

The ALD repro needs both the non-default `SurfaceDiffusionParameters`
(`radius=0.1`, `sigmaNormal=0.2`, `kNeighbors=8`, `stabilityFactor=0.5`) and
the pinned `RayTracingParameters`; without the ray block the same case returns.
It reproduces on 4.7.0/5.10.0 only.

## 5. Test results

`330` tests are collected in this worktree (5 skipped). With the 4.7.0 venv,
`VIENNAPS_WORKBENCH_DATA_DIR=$PWD/var/test-v470`, `WORKBENCH_PORT=8767`:

```
Ran 328 tests in 163.1s
FAILED (failures=3, errors=58, skipped=5)
```

That is 262 passed, 3 failed, 58 errored, 5 skipped, plus the 2 excluded native
ALD cases.

Two native ALD tests (`test_golden_2d_and_3d_...` and
`test_the_knob_changes_the_surface_...`) are excluded by the runner on 4.7.0
because they intentionally exercise the hanging native path; the request is
now rejected before the worker, and the native hang is covered by
`probe_ald_hang.py`. Excluding them is not a green suite: the reported numbers
above are the actual 4.7.0 result for the remaining tests.

Breakdown of the 61 failing/erroring ids:

| Class | Count | Detail |
|---|---|---|
| Missing untracked evidence in this worktree | **61** | `docs/*-evidence/...`; every path exists in the main worktree. Not regressions. |
| Real code/API regressions | **0** | 3 failures + 58 errors are all missing evidence. |

Focused results after the hardening changes:

- `tests.test_oxidation_advanced`: 25/25 pass (both runtime branches tested via
  version patching).
- `tests.test_ald_surface_diffusion` schema class: 8/8 pass, including the
  explicit 4.6.2-acceptance and 4.7.0-rejection cases.
- `tests.test_api_integration`: 16/16 pass (4 skipped).
- `tests.test_domain_continuation`: 2/2 pass; state metadata records the
  installed version instead of a literal.
- `tests.test_stage6`: pass; catalog `engineVersion` follows the runtime.
- `tests.test_engine_cellset`, `test_plasma_advanced`,
  `test_engine_batch1_specialized`, `test_cmp_measured_thickness`: pass.
- `tests.test_snapshot_runtime_policy`: 5/5 pass (foreign-runtime GET/download,
  typed direct error, worker pre-read rejection, matching-runtime success).
- Direct ALD worker gate: 4.7.0 rejection and 4.6.2 acceptance pass.

## 6. Fixes and files

- `backend/engine.py` — runtime identity, supported-version sets, version
  predicates, restricted oxidation controls.
- `backend/validation.py` — ALD/oxidation gates, version-qualified warnings,
  historical evidence constant.
- `backend/materials.py`, `backend/specialized_models.py` — current-runtime
  metadata, registry compatibility, `runtimeGate` catalog metadata.
- `backend/native_build.py` — explicit patched-runtime version set.
- `backend/main.py`, `backend/supervisor.py` — explicit replay policy/errors.
- `backend/snapshots.py`, `worker/materialize_snapshot.py` — deferred-snapshot
  runtime gate before native read; 409 policy detail; matching-runtime read
  unchanged.
- `worker/simulate.py` — keyword 2D boundary.
- `worker/specialized.py` — oxidant enum fallback, guarded/version-checked
  oxidation setters, removed-setter record.
- Tests and fixtures — version-aware assertions, gated cases, 2D boundary
  keyword, no-op/disabled acceptance, direct-worker ALD gate, snapshot policy.
- `scripts/migration-probes/probe_ald_hang.py`,
  `probe_oxidation_setters.py`, plus the earlier capability probes.
- `requirements-v470.lock`.

## 7. Remaining blockers to a 4.7.0 default

1. **Native ALD hang** (upstream bug). Until fixed, `surfaceDiffusion` is
   unusable on 4.7.0; the gate prevents a hung worker but the feature is
   unavailable. Issue draft: `docs/upstream-ald-surface-diffusion-hang.md`.
2. **Oxidation control parity.** Only `cflFactor` remains effective. The
   catalog still advertises mechanics/coupling/pressure fields; the UI should
   hide or mark them version-qualified before a default switch.
3. **Evidence coverage.** All capability/evidence matrices are 4.6.2
   measurements. A default switch needs a fresh bounded 4.7.0 evidence pass or
   an explicit “verified on 4.6.2 only” policy in the UI.
4. **Replay/continuation.** Old runs require the 4.6.2 runtime; a default
   switch without a retained 4.6.2 deployment would make history read-only
   (rejected, not broken).
5. **GPU multi-ion.** The patched 4.6.2 runtime is ABI-bound to ViennaLS 5.8.5
   and is rejected on 4.7.0; stock GPU single-particle works, multi-ion is
   unverified.

## 8. Recommendation

Keep 4.6.2 as the production default. 4.7.0 is now safe to run as an isolated
evaluation runtime: the known hang fails fast with a clear 422, the removed
oxidation setters are gated, and old runs are explicitly refused rather than
reinterpreted. Revisit the default after the upstream ALD fix (or an accepted
“no surfaceDiffusion on 4.7.0” product decision), a re-scoped oxidation UI, and
a 4.7.0 evidence pass.

## 9. Risks

- **GPU:** stock 4.7.0 runs `GPU_DISK`, but the multi-ion source patch does not
  carry over; GPU multi-ion must stay on 4.6.2 or be re-validated.
- **Snapshots/replay:** 4.6.2 runs become read-only once 4.6.2 is retired.
- **Request hashes:** unchanged for projects without the gated fields; adding
  or removing `advancedOxidation` fields changes hashes for projects that use
  the block. No schema change was made in this pass.
- **Artifacts:** isolated venv 127 MiB, wheels 36 MiB; GPU PTX payload inflates
  container images.
- **Evidence:** 4.6.2 evidence remains the only accepted proof set.
