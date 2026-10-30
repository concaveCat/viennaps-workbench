# Engine capability audit — installed ViennaPS 4.6.2 vs. the Workbench

Machine-readable companion: [`engine-capability-matrix.json`](engine-capability-matrix.json)
(`schemaVersion: 1`, 96 native classes/enums, 8 SPX-only capability rows, 10 implementation batches).

| Item | Value |
| --- | --- |
| Audited engine | `viennaps` 4.6.2 + `viennals` 5.8.5 (`/root/.local/lib/python3.12/site-packages/viennaps`) |
| Python | 3.12.14 |
| Workbench | app 0.1.0, project schema 7, base commit `3eacc2c` |
| Audit date | 2026-10-30 |
| Method | `dir()`/`__init__.__doc__` introspection of `viennaps`, `viennaps.d2`, `viennaps.d3`; every behavioural claim comes from an isolated subprocess probe with a 40–120 s timeout (no workbench state, no service touched) |

This document answers one question: **which engine capabilities can we still expose in a verifiable
batch, and which ones must we refuse or defer?** It is docs-only — no backend, worker or frontend
behaviour changed.

## 1. Classifications

| Classification | Meaning | Count |
| --- | --- | --- |
| `exposed` | Reachable from a submitted project today, with its user-facing parameters | 59 |
| `engine-model-not-exposed` | A real native process model with no step, catalog entry or API field | 7 |
| `advanced-parameter-or-output-not-exposed` | Only a strict subset is reachable, or the object is output-only | 12 |
| `internal-or-not-user-facing` | Native plumbing, base classes, callbacks, writer/enum utilities | 18 |
| `absent-in-engine` | Not in ViennaPS at all; SPX (Sentaurus Process Explorer) reference only | 8 rows in `spxOnly` |

`dimensions` in the JSON always means *the domain dimension the class was actually exercised on*, not
what the docstring implies. Empty `dimensions` means the class never sees a domain (`Logger`,
`MetaDataLevel`).

## 2. What the Workbench exposes today

### Process models (18)

| Native class | Project surface | Dimension | Notes |
| --- | --- | --- | --- |
| `IsotropicProcess` | `model: isotropic` (schema 1+) | 2/3 | per-material rates via `rateMode: material`; no flux engine |
| `SingleParticleProcess` | `model: singleParticle` | 2/3 | CPU_DISK / GPU_DISK, explicit seed + `raysPerPoint` |
| `DirectionalProcess` | `model: directional` (schema 5+) | 2/3 | single-velocity overload only |
| `MultiParticleProcess` | `model: multiParticle` (schema 7) | 2/3 | declarative ion/neutral species + linear rate coefficients |
| `SF6O2Etching` | `specialized/sf6o2` | 2/3 | flat 8-argument form or `advanced` → `PlasmaEtchingParameters`; fixed coverage tolerance |
| `CF4O2Etching` | `specialized/cf4o2` | 2/3 | flat 10-argument form or `advanced` → `CF4O2Parameters` |
| `HBrO2Etching` | `specialized/hbro2` | 2/3 | flat 8-argument form or `advanced` → `PlasmaEtchingParameters` |
| `SF6C4F8Etching` | `specialized/sf6c4f8` | 2/3 | flat 6-argument form or `advanced` → `PlasmaEtchingParameters`; no passivation family |
| `FluorocarbonEtching` | `specialized/fluorocarbon` | 2/3 | full per-material parameter table |
| `IonBeamEtching` | `specialized/ionBeam` | 2/3 | full `IBEParameters` + cos⁴ yield + redeposition |
| `WetEtching` | `specialized/wetEtch` | 2/3 | both constructor overloads behind `useCrystallineRates` |
| `TEOSDeposition` | `specialized/teosDeposition` | 2/3 | pinned SiO₂ output |
| `TEOSPECVD` | `specialized/teospecvd` | 2/3 | pinned SiO₂ output |
| `SingleParticleALD` | `specialized/ald` | 2/3 | + `AtomicLayerProcessParameters`, selectable material |
| `Oxidation` | `specialized/oxidation` | 2/3 | 13 of ~40 setter values (see B5) |
| `OxideRegrowth` | `specialized/oxideRegrowth` | 2/3 | derives its own `DenseCellSet` |
| `SelectiveEpitaxy` | `specialized/selectiveEpitaxy` | 2/3 | native `Material` enum only, no `nvFactors` |
| `NeutralTransport` | `specialized/neutralTransport` | 2/3 | all 12 parameters |

### Parameter objects with full coverage (8)

`FluorocarbonParameters`, `FluorocarbonParametersIons`, `FluorocarbonMaterialParameters`,
`IBEParameters`, `IBEParametersCos4Yield`, `NeutralTransportParameters`, `SingleParticleALDParams`,
`AtomicLayerProcessParameters`.

### Plasma sub-parameter families (B1, exposed)

The four plasma models accept optional `nativeParameters.advanced`. The
allowlist is the subset the installed CPU models actually consume:

| Class | Exposed through `advanced` | Still not exposed |
| --- | --- | --- |
| `PlasmaEtchingParameters` | `Ions`, `Mask`, `Passivation` (SF6O2/HBrO2), `Polymer`, `Substrate`, `beta_E`, `beta_P` (SF6O2/HBrO2), `rateFactors`, `passivationFlux` (SF6O2/HBrO2) | `Substrate.B_ie` (native struct field unused by the CPU surface model); SF6C4F8 `passivation`/`passivationFlux`/`beta_P` (no passivation particle; positive flux SIGSEGVs) |
| `PlasmaEtchingParametersIons/Mask/Passivation/Polymer` | all consumed fields, including `thetaRMin`/`thetaRMax` for the Plasma family | — |
| `PlasmaEtchingParametersSubstrate` | `A_ie`, `A_sp`, `B_sp`, `Eth_ie`, `Eth_sp`, `beta_sigma`, `k_sigma`, `rho` | `B_ie` (unused) |
| `CF4O2Parameters` | `Ions`, `Mask`, `Passivation`, `Si`, `SiGe`, all `gamma_*`, `fluxIncludeSticking` | `SiGe.A_ie/A_sp/Eth_ie/Eth_sp/x` (ignored by the native SiGe branch); `k_sigma_SiGe` is a method |
| `CF4O2ParametersIons/Mask/Passivation/Si` | all fields | — |
| `CF4O2ParametersSiGe` | `beta_sigma`, `k_sigma`, `rho` | the sputter/yield fields and `x`; use `k_sigma` directly |

Advanced defaults are documented in
[`plasma-advanced-parameters-contract.md`](plasma-advanced-parameters-contract.md).
Fields shared with flat inputs use the flat catalog defaults (for example
`ions.exponent=100`, `passivationFlux=0.2`); native-only fields are labelled
`nativeParameterClass`/`nativeMaterialMap`.

### Near-complete solver objects

`AdvectionParameters` (10/11 members; `integrationScheme` missing) and `RayTracingParameters`
(9/10; `useRandomSeeds` pinned `false` by design).

### Geometry, output and material surface

(59 `exposed` rows in the matrix overall, including the 18 models above.)

`MakePlane`, `MakeTrench`, `MakeHole` (`FULL` only), `MakeFin` (native + boolean construction),
multi-layer `stack` (composed from `MakePlane` + `GeometryFactory`), `GeometryFactory`,
`GDSGeometry`, `GDSReader`, `Extrude`, `Slice` (y-normal only), `Planarize` (cutoff or measured
`stopMaterial`), `Domain`, `Process`; outputs are surface mesh VTK, snapshot JSON, native state,
intermediate VTP/VTU and the OxideRegrowth cell-set JSON; `Material`/`MaterialRegistry` back the
custom material catalog.

> Coverage gap: `docs/capabilities.json` still lists only `isotropic` and `singleParticle` under
> `models`. The 14 specialized models live in `backend/specialized_models.py` and
> `/api/specialized-models`. That file documents *verified behaviour*, not the *engine surface*;
> this matrix is the surface map, not a replacement for it.

## 3. Workbench still not exposed

### 3.1 Structured models — safe candidates (batch A)

| Native class | Verified dims | What it actually is | Precondition / risk |
| --- | --- | --- | --- |
| `FaradayCageEtching` + `FaradayCageParameters` | 3 only | Ion-beam etch with a Faraday-cage angular filter: `cageAngle` plus a full `IBEParameters` | Probe: 3D finishes in <0.1 s; the identical call on a 2D domain did not return within 40 s (plane-only and trench, with and without `planeWaferRate`) → **engine-side 2D hang**, must be rejected at validation |
| `GeometricTrenchDeposition` | 2 only | Closed-form analytic trench fill (`trenchWidth, trenchDepth, depositionRate, bottomMed, a, b, n`), no flux engine | Probe: 3D **SIGSEGV** right after “Applying geometric model” → 2D only |
| `CSVFileProcess` + `RateGrid` | 2/3 | Rate interpolated from a CSV grid with directional + isotropic components | Needs a new uploaded asset kind and an explicit interpolation mode: the engine only logs `Interpolation mode not set.` and continues. `setCustomInterpolator` is arbitrary Python |
| `BoxDistribution` / `SphereDistribution` | 2/3 | Particle-shape distributions applied as geometric models | The mask overload requires a raw **`viennals` Domain**, not `viennaps.Domain`; without a mask the probe produced no surface change |
| `CustomSphereDistribution` | none | Multi-radius sphere distribution | Probe: aborts in 2D and 3D with `terminate called recursively` without a mask → experimental, keep out until a bounded probe passes |

### 3.2 Advanced parameters and outputs (batch B)

| Native class | Reachable subset | Missing | Why it matters |
| --- | --- | --- | --- |
| `PlasmaEtchingParameters` | B1: `Ions`, `Mask`, `Passivation` (SF6O2/HBrO2), `Polymer`, `Substrate`, `beta_E`, `beta_P` (SF6O2/HBrO2), `rateFactors`, `passivationFlux` (SF6O2/HBrO2) | `Substrate.B_ie` (unused); SF6C4F8 `passivation`/`passivationFlux`/`beta_P` (no particle; positive flux SIGSEGVs) | Remaining gaps are inert or unsafe, not missing physics controls |
| `PlasmaEtchingParametersSubstrate` | B1: all consumed fields except `B_ie` | `B_ie` (unused by the CPU surface model) | No useful substrate-yield knob remains hidden |
| `CF4O2Parameters` | B1: `Ions`, `Mask`, `Passivation`, `Si`, `SiGe` (`beta_sigma`/`k_sigma`/`rho`), `gamma_*`, `fluxIncludeSticking` | `SiGe.A_ie/A_sp/Eth_ie/Eth_sp/x` (ignored by native SiGe branch); `k_sigma_SiGe` method | SiGe chemistry is represented by the three fields the native branch reads |
| `CF4O2ParametersSiGe` | B1: `beta_sigma`, `k_sigma`, `rho` | sputter/yield fields and `x`; `k_sigma_SiGe` method | A full SiGe yield editor is not available because the native branch does not read those fields |
| `CoverageParameters` | `tolerance = 1e-3` (hard-coded) | `tolerance`, `maxIterations`, `initialized` | Convergence cannot be tightened or loosened |
| `RateSet` | 4 fields flattened into the scalar `directional` step | multi-`RateSet` sequences, per-set masks | Per-material direction/velocity impossible |
| `SurfaceDiffusionParameters` | — | 7 members | No workbench code path constructs it; it is a level-set smoothing model, so an insertion point must be evidenced first |
| `DenseCellSet` | OxideRegrowth by-product JSON only | generic per-step generation, height/cover/periodic options, filling fractions, user scalars, VTU export, continuation | No project can request a volumetric field of its own |
| `Oxidation` advanced setters | 13 of ~40 | CFL, coupling/mechanics/Stokes/pressure iterations+tolerances, solve bounds, `maxGridPoints`, simple relaxation, mask contact/traction mechanics, `GpuMode`/`GpuPreconditioner`, model-level `saveSurfaceMesh`/`saveVolumeMesh` helpers (the workbench writes the same meshes through `Domain`), `estimatePlanarOxideThickness` | Separate solver surface; unproven on this host |
| `SiliconOrientation.Si110` | 3 of 4 | `Si110` | Exists in 4.6.2, missing only because it was never evidenced |

### 3.3 Deliberately never exposed (batch C)

| Native symbol | Reason |
| --- | --- |
| `ProcessModel`, `ProcessModelBase`, `ProcessParams` | Extension hook: a project could recompose native surface/geometric/velocity models in ways no evidence covers |
| `AdvectionCallback` | Arbitrary Python executed inside the worker → breaks request hashing, replay and the bounded-child resource model |
| `RateGrid.setCustomInterpolator`, `MultiParticleProcess.setRateFunction` (user-supplied) | Same. The worker already installs its own *fixed* linear rate callback for `multiParticle`; the legitimate use case is covered declaratively by `defaultBySpecies` / `byMaterial` |
| `Writer`, `Reader`, `ToDiskMesh`, `VTKRenderWindow`, `Logger`, `MetaDataLevel`, `DomainSetup`, `StencilLocalLaxFriedrichsScalar`, `LengthUnit`, `TimeUnit`, `MaterialMap`, `MaterialKind`, `MaterialCategory` | Plumbing, output writers the workbench replaces for hashing/budget reasons, or units/metadata pinned by the schema (`um`, `s`) |

## 4. SPX-only / not in the engine

SPX = the Synopsys Sentaurus Process Explorer reference flows in `/root/spx_examples` (`.tbson`
routes, `.tdr` output, `litho_patterning` / `implant_*` steps). **None of the rows below exist in
ViennaPS 4.6.2**, so no batch in this document can add them:

| Capability | Status after “ViennaPS 完全体” |
| --- | --- |
| Lithography patterning (resist coat/expose/develop) | absent — the engine only consumes already-patterned GDS/geometry masks |
| Ion implantation (dose/energy/tilt) | absent |
| Anneal / dopant diffusion / activation | absent |
| Silicide formation | absent (`MaterialCategory.Silicide` is a label, not a process) |
| Stress / mechanics | absent (only `Oxidation` has an internal mask-mechanics solver) |
| True CMP (pattern density, pad/slurry, dishing/erosion) | only a geometric surrogate: `Planarize` + measured `stopMaterial` |
| Doping profiles, device/electrical simulation | absent — ViennaPS is a topography engine |
| Contact/M0 routing as a process step | project-model limitation, partially reachable via `gdsBoolean` + continuation |

So the honest statement is: **even after every batch below lands, “ViennaPS 完全体” still does not add
litho, implant, anneal, silicide, stress, true CMP, doping or electrical simulation.** Those stay
SPX-only, and any UI that implies otherwise would be wrong.

## 5. Proposed implementation batches

Each batch is independent, ships behind its own schema field, and must not change existing request
hashes when unused.

### Batch A1 — `FaradayCageEtching` (3D)

- Model fields: `cageAngle` + the existing `ionBeam` parameter group (reuse the `IBEParameters` block).
- Mask materials come from the geometry, exactly like `ionBeam`.
- Acceptance:
  1. validator rejects `dimension: 2` with a clear 422 before the worker starts;
  2. 3D trench + mask surface equals a direct native reference within `1e-6 µm`;
  3. an independent native Faraday reference (cage source directions, not ordinary normal-incidence IBE) matches within `1e-6 µm`;
  4. replay reproduces artifact hashes exactly;
  5. `specialized-models` catalog row states `engineVersion 4.6.2`, dimension 3.

### Batch A2 — `GeometricTrenchDeposition` (2D)

- Acceptance:
  1. validator rejects `dimension: 3` (engine SIGSEGV);
  2. 2D trench profile equals a direct native reference within `1e-6 µm`;
  3. run record reports `engine: not-applicable` (no flux engine);
  4. degenerate parameters (zero rate/width) fail validation instead of hanging.

### Batch A3 — CSV rate tables (`CSVFileProcess`, `RateGrid`)

- New uploaded asset kind like GDS: size cap, `sha256`, schema `x,z,rate`, finite floats; the project
  stores `assetId` + hash, never a path.
- Acceptance:
  1. upload is capped, hashed, schema-validated and stored under the data directory;
  2. a missing interpolation mode is a 422, not a warning;
  3. a `LINEAR` grid matches a direct native reference within `1e-6 µm`;
  4. the request hash changes with CSV content and an old hash fails replay explicitly;
  5. no API field can reach `setCustomInterpolator`.

### Batch A4 — particle shape distributions

- These are geometry sources, not process steps (the native mask is a `viennals` Domain).
- Acceptance:
  1. design note proves the `viennals` mask is derived from the existing project domain, with no second
     geometry source of truth;
  2. `Box`/`Sphere` surfaces equal direct native references;
  3. count and magnitude of radii/half-axes are bounded;
  4. `CustomSphereDistribution` is catalogued only after a passing 2D **and** 3D probe is archived.

### Batch B1 — full plasma sub-parameter families (implemented backend/API)

- Status: implemented by the optional `nativeParameters.advanced` object and the
  parameter-object overloads. The allowlist is the native-consumed subset, not
  every struct member:
  - `Substrate.B_ie`, `SiGe.A_ie/A_sp/Eth_ie/Eth_sp/x`, and the SF6C4F8
    passivation family are rejected as inert/unsafe;
  - SF6C4F8 `passivationFlux > 0` would dereference a missing native flux
    scalar (SIGSEGV) and is rejected by validation and the worker.
- Advanced defaults for fields shared with flat inputs equal the flat catalog
  defaults; native-only defaults are labelled and recorded at run time.
- Acceptance:
  1. omitting it leaves every existing project's request hash unchanged;
  2. defaults read back equal to the effective flat/native defaults and are
     recorded in run evidence;
  3. 2D and 3D golden runs for sf6o2, hbro2, sf6c4f8 and a cf4o2-SiGe case
     match direct native references within `1e-6 µm`; archived `docs/*-evidence`
     is still pending;
  4. no UI field is added by this backend/API change.

### Batch B2 — multi-`RateSet` directional model

- Acceptance: bounded `rateSets` list, mutually exclusive with the scalar fields; existing directional
  hashes unchanged; a two-material run matches a native reference; per-set `maskMaterials` are either
  supported or rejected, never silently ignored.

### Batch B3 — `SurfaceDiffusionParameters`

- Acceptance: a bounded probe first establishes a legal insertion point; if none exists in 4.6.2 the
  class is documented as not applicable and stays unexposed; otherwise defaults must be a no-op and
  `radius`/`sigmaNormal`/`stabilityFactor` carry documented units and bounds.

### Batch B4 — generic `DenseCellSet` output

- Acceptance: per-step opt-in validated before the run; the existing OxideRegrowth artifact keeps its
  schema and hash; oversized cell sets fail preflight before writing; cell counts and field arrays
  replay byte-identically; VTU export stays off until its artifact budget is proven.

### Batch B5 — Oxidation advanced setters and `Si110`

- Acceptance: `Si110` added only after a dry/wet run reproduces a native reference; advanced setters
  grouped and opt-in with unchanged defaults; mask-mechanics fields require a mask level set (422
  otherwise); GPU oxidation stays out until host GPU evidence exists.

### Batch C1 — guard rails for the never-exposed surface

- Acceptance: no project/replay JSON field can name a callable or module path; a schema test asserts
  the forbidden names never appear; new modelling needs are met with declarative fields plus a native
  reference run.

### Suggested order

`A2` → `A1` → `A4` → `B4` → `A3` → `B1` → `B2` → `B5` → `B3`.

Rationale: two self-contained models with the smallest validation surface first (`A2`, `A1`), then the
geometry-source and volumetric-output work that adds new artifact kinds (`A4`, `B4`), then the asset
pipeline (`A3`) which is the riskiest of the A group, then the deep-physics parameter batches, and last
the two items (`B5` GPU oxidation, `B3` surface diffusion) whose native insertion point is not yet
evidenced.

`C1` is not a feature batch: its schema test should land **together with `A3`**, which is the batch most
likely to invite a custom-interpolator callback.

## 6. Reproducing the audit

```bash
cd /root/wb-engine-audit
.venv/bin/python - <<'PY'
import viennaps, viennaps.d2, viennaps.d3
print(viennaps.__version__)
print([n for n in dir(viennaps) if not n.startswith('_')])
for name in ("FaradayCageEtching", "GeometricTrenchDeposition", "CSVFileProcess", "RateGrid"):
    print(name, getattr(viennaps, name).__init__.__doc__)
PY
```

The two load-bearing negative results:

```bash
# FaradayCageEtching: 3D returns in <0.1 s, 2D never returns (rc=124 from timeout)
for DIM in 3 2; do
  timeout 40 .venv/bin/python - "$DIM" <<'PY'
import sys, viennaps as ps
ps.setDimension(int(sys.argv[1])); ps.Length.setUnit("um"); ps.Time.setUnit("s")
d = ps.Domain(0.1, 1.0, 1.0) if sys.argv[1] == "3" else ps.Domain(0.1, 2.0)
ps.MakePlane(d, 0.0, ps.Material.Si, False).apply()
p = ps.FaradayCageParameters(); p.cageAngle = 0.2
p.ibeParams.meanEnergy, p.ibeParams.sigmaEnergy, p.ibeParams.exponent = 100.0, 10.0, 100.0
ps.Process(d, ps.FaradayCageEtching(p, [ps.Material.Mask]), 0.005).apply()
print("ok", sys.argv[1])
PY
  echo "dim=$DIM rc=$?"
done

# GeometricTrenchDeposition: 2D finishes, 3D aborts with SIGSEGV (rc=139)
timeout 40 .venv/bin/python - <<'PY'
import viennaps as ps
ps.setDimension(3); ps.Length.setUnit("um"); ps.Time.setUnit("s")
d = ps.Domain(0.1, 1.0, 1.0)
ps.MakePlane(d, 0.0, ps.Material.Si, False).apply()
ps.Process(d, ps.GeometricTrenchDeposition(0.4, 0.5, 0.05, 0.1, 0.1, 2.0, 1.0), 0.05).apply()
print("unexpected ok")
PY
```

The `CSVFileProcess` / distribution results come from the same pattern (one subprocess per case,
`timeout 40`, tiny domain, `0.01 s` process time).

Behavioural claims (2D hang, 3D SIGSEGV, `terminate called recursively`, `Interpolation mode not set.`)
were each produced by such a subprocess; none of them mutated the workbench data directory or contacted
the running service.

Caveats: this audit covers the **installed 4.6.2 bindings only**. It does not prove that an exposed
model is physically calibrated, and it does not claim any model is correct — only that it is reachable
and, where noted, how it failed in a bounded probe.
