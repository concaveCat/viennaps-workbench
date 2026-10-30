# ViennaPS 4.6.2 → 4.7.0 migration evaluation

Status: **evaluated, not adopted.** The production Workbench keeps the
ViennaPS 4.6.2 / ViennaLS 5.8.5 system runtime. A working isolated 4.7.0
runtime and the compatibility fixes below live on `task/v470-migration`
(base `1015266`). This document records what changed, what was proven, and what
still blocks a default switch.

## 1. Install method and versions

The official PyPI 4.7.0 release was used, not the local `/root/ViennaPS`
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
| FastAPI / Pydantic / Uvicorn | 0.162.0 / 2.14.2 / 0.54.0 | see `requirements-v470.lock` |
| venv size | 127 MiB | wheel is CUDA-enabled (`libViennaLS_GPU.so`, `ptx/`) |

`viennaps 4.7.0` metadata declares `Requires-Dist: ViennaLS>=5.10.0`.
`pip check` reports no broken requirements. The system packages and the main
`.venv` were not modified.

## 2. Public API differences

Full class/member dumps were compared between the installed 4.6.2 and the
PyPI 4.7.0 wheel (102 vs 125 viennaps classes, 83 vs 80 viennals classes).

New viennaps surface:

- geometry primitives: `LevelSet`, `MakeGeometry`, `Box`, `Sphere`, `Cylinder`,
  `Plane`, `VTKWriter`, `BooleanOperation`, `BooleanOperationType`
- implant/anneal: `IonImplantation`, `Anneal`, `AnnealMode`,
  `ImplantProfileModel`, `ImplantTableModel`, `DamageTableModel`,
  `ImplantPearsonIV`, `ImplantPearsonIVChanneling`, `ImplantDualPearsonIV`,
  `ImplantDamageHobler`, `PearsonIVParameters`, `ImplantDoseControl`,
  `ScreenEnergyLoss`
- analysis: `NetDoping`, `SheetResistance`
- modeldb roots: `getModelDbRoot`, `initModelDbRoot`, `setModelDbRoot`

Removed or renamed:

| 4.6.2 | 4.7.0 |
|---|---|
| `viennaps.GpuPreconditioner` | removed |
| `viennals.GpuPreconditioner`, `ILU0`, `Jacobi` | removed |
| `viennals.OxidationConstrainedAmbient`, `OxidationMaskBending` | removed |
| `OxidantType.Dry` / `.Wet` | `OxidantType.DRY` / `.WET` |
| `Oxidation.setGpuPreconditioner`, `setPressureIterations/Tolerance`, `setStokesIterations/Tolerance`, `setSimplePressureRelaxation`, `setSimpleVelocityRelaxation`, all `setMask*` mechanics setters | removed |
| `OxidationDeformationParameters.{bulkModulus,relaxation,pressure*,stokes*,tolerance,harmonicIterations}` | removed |
| `OxidationMaskParameters.{material,maxGridPoints,contact*,anchor*,creep*,temperature,...}` | removed; `viscosity`, `shearActivationVolume` added |

Additions relevant to the Workbench:

- `DenseCellSet.setScalarData(name, values)` (4.6.2 only had `addScalarData`)
- `Oxidation.setInitialTimeStep`, `setRegrid`, `setMaskBarrierOnly`,
  `setMaskPermeabilityRatio`, `setDealGroveRates`, `setShearActivationVolume`,
  `setStressDependentOxidation`
- `RayTracingParameters.minRayDistance`

Unchanged and re-verified: `GDSReader`, `GDSGeometry`, `MakeStack`,
`Planarize`, `DenseCellSet`, `Process`, all 91 `Material` names and IDs
(max built-in ID 176, so custom IDs 177+ remain valid).

### Binding pitfall found during migration

In 4.7.0 the three-positional-argument 2D constructor
`ps.Domain(gridDelta, xExtent, boundary)` now resolves to the
`(gridDelta, xExtent, yExtent)` overload, silently turning the boundary enum
into a `yExtent` value and leaving the x boundary reflective. 4.6.2 resolved it
to the `(gridDelta, xExtent, boundary)` overload. The keyword form
`ps.Domain(gd, xExtent, boundary=boundary)` is unambiguous on both versions
and is now used in `worker/simulate.py` and the test fixtures.

Unrelated pre-existing behaviour: an all-reflective 2D/3D domain makes
`MakePlane.apply()` hang on **both** versions; an `INFINITE_BOUNDARY` is needed
on the open axis. The migration probes use reflective + infinite.

## 3. Bounded probes

All probes ran in a separate process through `scripts/bounded_child.py`
(60 s timeout, 4 GiB RSS polling limit, 1 MiB log). Scripts are in
`scripts/migration-probes/`, outputs in `var/probes/`.

| Probe | 4.7.0 result | Notes |
|---|---|---|
| masked Box/Sphere (`probe_masked_distributions.py`) | **pass** | official `examples/emulation/FinFET.py` pattern: `ps.LevelSet` + `MakeGeometry(Box)` as hard mask, `SphereDistribution(-1.5)` and `BoxDistribution([-gd,-gd,-2])` with `addMaskMaterial(Mask)`, `Planarize`. Si etched to z=8.5 then 6.5 while the mask top stayed at z=20. |
| same pattern on 4.6.2 | **fails** | `AttributeError: module 'viennaps' has no attribute 'LevelSet'`; 4.6.2 only has `ps.ls.Domain`/`ps.ls.Box`/`ps.ls.MakeGeometry`. |
| 4.6.2-compatible masked variant | pass on 4.6.2 | masking itself was never the gap; the convenience primitives and implant/anneal are the new capability. |
| manual implant + anneal (`probe_implant_anneal.py`) | **pass** | 2D Pearson IV + Hobler damage, 1e14 cm⁻², 7°, 2-step 600→1050 °C schedule. `P_total` 78.313341 → 78.313352 (0.00001 % drift), `P_active` 69.865 (89.2 % of dose), interstitial field present, VTU written. |
| GDS + stack + cell set + oxidation (`probe_geometry_oxidation.py`) | **pass** | `myTest.gds`: 6 structures / 7 layers, mask inserted, `Planarize`; 2-layer `MakeStack`; `setScalarData` round-trip over 500 cells; dry oxidation 1000 °C / 0.5 min grew 0.01337 µm oxide (+0.00895 µm Si consumed) vs 0.02063 µm planar estimate. |
| GPU (`probe_gpu.py`) | **pass (execution only)** | `gpuAvailable() = true`; 2D `SingleParticleProcess` + `GPU_DISK`, 64 rays, 1 thread, 265 MiB peak, 0.8 s. CPU/GPU surfaces are not bit-identical (max 0.035 µm deviation, statistical RNG), so this proves execution, not equivalence. |
| GPU multi-ion, table-driven implant/modeldb, `pnJunction` end-to-end, 3D GDS | **not probed** | see risks. |

## 4. Test results

`318` tests are collected in this worktree; `5` are skipped.

With the 4.7.0 venv (`VIENNAPS_WORKBENCH_DATA_DIR=$PWD/var/test-v470`,
`WORKBENCH_PORT=8767`):

```
Ran 316 tests in 289.7s
FAILED (failures=11, errors=58, skipped=5)
```

Two tests were excluded by the runner because they never return on 4.7.0
(see §5): the ALD "golden" and "knob changes the surface" cases. So:
242 passed, 11 failed, 58 errored, 5 skipped, 2 hung.

The 69 reported failure/error ids split as:

| Class | Count | Examples |
|---|---|---|
| Missing untracked evidence in this worktree | 61 | `docs/materials-evidence/...`, `docs/stage5-evidence/two-structure-two-layer.gds`, `docs/stage9-evidence/...`; every path exists in the main worktree at the same relative path. **Not regressions.** |
| 4.6.2-pinned expectations | 4 | `test_api_integration` replay rejects 4.6.2 replay bundles before reaching the engine-preference check; `test_domain_continuation` asserts `"4.6.2" == version`. |
| Native behaviour changes | 4 | 3 oxidation tests (below) and 1 ALD API run that times out. |
| ALD native hang (excluded) | 2 | run is killed by timeout. |

The stated main baseline (318 OK / 5 skipped) cannot be reproduced from this
worktree with the system venv: workers are launched as `ROOT/.venv/bin/python`,
which is now 4.7.0, so a 4.6.2 test process and a 4.7.0 worker reject each
other via the expected-version check. Baseline comparisons must run in a
worktree whose `.venv` is the 4.6.2 runtime.

## 5. Compatibility fixes on `task/v470-migration`

Applied (all version-adaptive, 4.6.2 behaviour preserved):

1. `worker/simulate.py` — 2D domain now uses
   `ps.Domain(gd, xExtent, boundary=boundary)`, fixing the 4.7.0 overload
   misbinding. This alone turned `test_engine_cellset` from failure to pass.
2. `worker/specialized.py` — `OxidantType` lookup accepts `Dry`/`Wet` and
   `DRY`/`WET`; `setSimplePressureRelaxation` is sent only when the binding
   exposes it, and the resolved record states when it was removed.
3. Test fixtures/tests — the same keyword-boundary form in 21 files;
   `OxidantType` fallback in `tests/test_oxidation_advanced.py`,
   `tests/fixtures/stage8_model_smoke.py`, and the oxidation probe.
4. `scripts/migration-probes/` — reproducible bounded probes.
5. `requirements-v470.lock` and this document.

Focused reruns after the fixes: `test_engine_cellset`, `test_plasma_advanced`,
`test_engine_batch1_specialized`, `test_cmp_measured_thickness`, `test_stage6`
all pass. `test_oxidation_advanced` went from 14 failures/errors to 3.

## 6. Remaining blockers and required Workbench changes

1. **ALD surface diffusion hangs natively (highest risk).** With a non-default
   `SurfaceDiffusionParameters` (`radius=0.1`, `sigmaNormal=0.2`,
   `kNeighbors=8`, `stabilityFactor=0.5`) on `specialized:ald`,
   `Process.apply()` never returns on 4.7.0; the same request finishes in
   ~0.03 s on 4.6.2. Default-parameter and zero-coefficient runs pass, and
   single-parameter variations usually pass, so the failure is a
   parameter-combination instability inside the new native stencil. Upstream
   report/repro is required; until then the Workbench must reject or disable
   `surfaceDiffusion` when the installed engine is 4.7.0.
2. **Oxidation solver knobs are inert.** `setMechanicsTolerance` and
   `setCouplingTolerance` are still bound but no longer change the 2D surface
   in the probe configuration, and `setSimplePressureRelaxation` is gone.
   Either re-verify which 4.7.0 controls are physical or shrink
   `advancedOxidation` to the controls that still have an effect.
3. **Version identity is hardcoded to 4.6.2.** `backend/materials.py`,
   `backend/specialized_models.py`, `backend/validation.py`,
   `backend/native_build.py` (rejects anything but 4.6.2), and
   `tests/test_stage6.py` / `tests/test_stage8.py` all pin `"4.6.2"`.
   Introduce one engine-version constant and make capability-matrix scopes
   version-aware before a default switch.
4. **Replay/continuation version lock.** 4.6.2 snapshots, replays and native
   Domain states are rejected by 4.7.0 (`ReplayMismatch`). A migration policy
   (re-run vs. archive-only) and user-facing message are needed.
5. **GPU patched runtime.** The 4.6.2 `runtime-patched` build is ABI-bound to
   ViennaLS 5.8.5 and is rejected by `native_build`; it cannot be reused with
   4.7.0/5.10.0. Stock 4.7.0 GPU works for single-particle, but multi-ion GPU
   behaviour is unverified and the 4.6.2 source patches have no 4.7.0
   counterpart yet.

## 7. Recommendation

**Stay on 4.6.2 for the production service; keep 4.7.0 as an isolated
evaluation runtime.** 4.7.0 adds genuinely useful capability (official
geometry primitives, masked distributions as shipped, implant/anneal, doping
analysis) and fixes the old `DenseCellSet` scalar API. But the ALD native hang
and the oxidation-control changes are behavioural regressions that the current
UI advertises, and the version/catalog/replay layer is still 4.6.2-specific.
Switching main now would ship a hanging ALD configuration and break every
existing replay.

Recommended next step: file the ALD `SurfaceDiffusionParameters` hang upstream
with the minimal 2D repro in `scripts/migration-probes/` (or a trimmed variant)
and, in parallel, implement the version constant + capability-matrix
versioning. Re-evaluate a default switch only after the ALD hang is fixed or
gated off and the oxidation advanced contract is re-probed on 4.7.0.

## 8. Risks if 4.7.0 is adopted later

- **GPU:** stock wheel is CUDA-enabled and runs, but the 4.6.2 multi-ion patch
  is not portable; GPU multi-ion must be re-validated (or remain CPU-only).
- **Snapshots/replay:** all 4.6.2 evidence and saved runs become read-only.
- **Request hashes:** schema and normalized defaults were not changed in this
  branch, and the hash tests that do not depend on untracked evidence pass;
  changing `advancedOxidation` fields would change those hashes.
- **Artifacts:** the isolated venv is 127 MiB; wheels are 36 MiB total.
  Probe VTUs are small (5,640 cells), but GPU PTX/CUDA payload inflates images.
- **Evidence:** 4.6.2 capability/evidence matrices remain the only accepted
  proof set; 4.7.0 would need a fresh bounded evidence pass per capability.
