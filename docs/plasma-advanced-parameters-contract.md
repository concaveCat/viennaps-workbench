# Plasma advanced native parameters (B1)

Status: backend/API contract for the four built-in plasma models. No frontend
field is added by this change; the catalog exposes the schema so a client can
render it later.

## 1. Why this exists

`worker/specialized.py` historically called the flat float overloads:

```python
ps.SF6O2Etching(ionFlux, etchantFlux, oxygenFlux, meanIonEnergy, sigmaIonEnergy,
                ionExponent, oxySputterYield, etchStopDepth)
```

ViennaPS 4.6.2 also binds the parameter-object overloads:

| model | parameter class | constructor |
|---|---|---|
| `sf6o2` | `PlasmaEtchingParameters` | `ps.SF6O2Etching(parameters)` |
| `hbro2` | `PlasmaEtchingParameters` | `ps.HBrO2Etching(parameters)` |
| `sf6c4f8` | `PlasmaEtchingParameters` | `ps.SF6C4F8Etching(parameters)` |
| `cf4o2` | `CF4O2Parameters` | `ps.CF4O2Etching(parameters)` |

The nested families (`Ions`, `Mask`, `Passivation`, `Polymer`, `Substrate`,
`Si`, `SiGe`, `gamma_*`, `fluxIncludeSticking`, material maps) were previously
unreachable. They are now exposed through an optional, strict
`nativeParameters.advanced` object.

## 2. Payload shape

`advanced` sits beside the existing flat fields, never inside them:

```json
{
  "nativeModel": "sf6o2",
  "nativeParameters": {
    "ionFlux": 1,
    "etchantFlux": 1,
    "oxygenFlux": 0.2,
    "meanIonEnergy": 100,
    "sigmaIonEnergy": 10,
    "ionExponent": 100,
    "oxySputterYield": 3,
    "etchStopDepth": -1000000,
    "advanced": {
      "ions": {"meanEnergy": 60, "n_l": 8},
      "substrate": {"A_ie": 4},
      "beta_E": {"default": 1, "byMaterial": {"Si": 0.7}}
    }
  }
}
```

Rules:

* `advanced` is optional. Omitting it keeps the historical flat overload and
  the historical request hash (pinned by tests).
* Presence of `advanced` (even `{}`) selects the parameter-object overload and
  changes the request hash, because it is part of the submitted input.
* `advanced` is accepted only for `sf6o2`, `cf4o2`, `hbro2`, and `sf6c4f8`.
  Other specialized models reject the key.
* Unknown top-level blocks, unknown nested fields, wrong JSON types, non-finite
  numbers, and out-of-range numbers are rejected; nothing is silently dropped.
* Nested numbers use native units. Angles are **radians** (the installed
  `Ions.minAngle/inflectAngle/thetaRMin/thetaRMax` are radians), not degrees.

## 3. Exact exposed schema

### 3.1 `sf6o2`, `hbro2` — `PlasmaEtchingParameters`

Same block/field allowlist. Defaults differ only where noted in §4.

```text
advanced.ions
  meanEnergy        0 .. 1e6        default 100
  sigmaEnergy       0 .. 1e6        default 10
  exponent          0 .. 1e6        default 500
  n_l               0 .. 1e6        default 10
  minAngle          0 .. pi rad     default 1.3962634
  inflectAngle      0 .. pi rad     default 1.55334303
  thetaRMin         0 .. pi rad     default 1.2217304763960306
  thetaRMax         0 .. pi rad     default 1.5707963267948966

advanced.mask
  A_sp              0 .. 1e6        default 0.0139
  B_sp              0 .. 1e6        default 9.3
  Eth_sp            0 .. 1e6        default 20
  rho               1e-6 .. 1e6     default 500

advanced.passivation
  A_ie              0 .. 1e6        default 3
  Eth_ie            0 .. 1e6        default 10

advanced.polymer
  A_sp              0 .. 1e6        default 0.02
  B_sp              0 .. 1e6        default 8.5
  Eth_sp            0 .. 1e6        default 15
  rho               1e-6 .. 1e6     default 5
  a1                -1e6 .. 1e6     default -0.26
  a2                -1e6 .. 1e6     default 2.72
  a3                -1e6 .. 1e6     default -4.3
  a4                -1e6 .. 1e6     default 1.95
  usePolyCosThetaYield  boolean    default false

advanced.substrate
  A_ie              0 .. 1e6        default 7
  A_sp              0 .. 1e6        default 0.0337
  B_ie              0 .. 1e6        default 0.8
  B_sp              0 .. 1e6        default 9.3
  Eth_ie            0 .. 1e6        default 15
  Eth_sp            0 .. 1e6        default 20
  beta_sigma        0 .. 1e6        default 0.04
  k_sigma           0 .. 1e6        default 300
  rho               1e-6 .. 1e6     default 5.02

advanced.passivationFlux   0 .. 1e6   default 100

advanced.beta_E      map {default 1, byMaterial {material: value}}  0 .. 1e6
advanced.beta_P      map {default 1, byMaterial {material: value}}  0 .. 1e6
advanced.rateFactors map {default 1, byMaterial {material: value}}  0 .. 1e6
```

The flat fields already expose the top-level `ionFlux`, `etchantFlux`,
`etchStopDepth` and (for `sf6o2`/`hbro2`) `oxygenFlux`; they are deliberately
not duplicated in `advanced`. `advanced.passivationFlux` is the native top-level
name and, when present, is applied after `oxygenFlux`.

### 3.2 `cf4o2` — `CF4O2Parameters`

```text
advanced.ions
  meanEnergy        0 .. 1e6        default 100
  sigmaEnergy       0 .. 1e6        default 10
  exponent          0 .. 1e6        default 500
  n_l               0 .. 1e6        default 10
  minAngle          0 .. pi rad     default 1.3962634
  inflectAngle      0 .. pi rad     default 1.55334303
  # thetaRMin/thetaRMax do not exist on CF4O2ParametersIons

advanced.mask
  A_sp              0 .. 1e6        default 0.0139
  Eth_sp            0 .. 1e6        default 20
  rho               1e-6 .. 1e6     default 500
  # B_sp does not exist on CF4O2ParametersMask

advanced.passivation
  A_C_ie            0 .. 1e6        default 3
  A_O_ie            0 .. 1e6        default 3
  Eth_C_ie          0 .. 1e6        default 10
  Eth_O_ie          0 .. 1e6        default 10

advanced.si
  A_ie              0 .. 1e6        default 7
  A_sp              0 .. 1e6        default 0.0337
  Eth_ie            0 .. 1e6        default 15
  Eth_sp            0 .. 1e6        default 20
  beta_sigma        0 .. 1e6        default 0.04
  k_sigma           0 .. 1e6        default 300
  rho               1e-6 .. 1e6     default 5.02

advanced.siGe
  A_ie              0 .. 1e6        default 6.5
  A_sp              0 .. 1e6        default 0.03
  Eth_ie            0 .. 1e6        default 14
  Eth_sp            0 .. 1e6        default 18
  beta_sigma        0 .. 1e6        default 0.04
  k_sigma           0 .. 1e6        default 345.168420976798
  rho               1e-6 .. 1e6     default 4.84
  x                 0 .. 1            default 0.3

advanced.fluxIncludeSticking   boolean   default false

advanced.gamma_C             map {default 1, byMaterial {material: value}}  0 .. 1
advanced.gamma_C_oxidized    map {default 1, byMaterial {material: value}}  0 .. 1
advanced.gamma_F             map {default 1, byMaterial {material: value}}  0 .. 1
advanced.gamma_F_oxidized    map {default 1, byMaterial {material: value}}  0 .. 1
advanced.gamma_O             map {default 1, byMaterial {material: value}}  0 .. 1
advanced.gamma_O_passivated  map {default 1, byMaterial {material: value}}  0 .. 1
```

`CF4O2ParametersSiGe.k_sigma_SiGe(x)` is a method, not a parameter field, so it
is not exposed. Set `advanced.siGe.k_sigma` directly.

Cross-model blocks are rejected (for example `polymer`, `substrate`, `beta_E`,
`passivationFlux`, or `ions.thetaRMin` under `cf4o2`, and `si`, `siGe`,
`gamma_*`, or `fluxIncludeSticking` under the `PlasmaEtchingParameters` models).

## 4. Native defaults are per model, not per parameter class

The installed binding does not use one set of defaults for a shared class. The
catalog and the run metadata record the effective model defaults:

| model | deviation from `PlasmaEtchingParameters()` / `CF4O2Parameters()` |
|---|---|
| `sf6o2` | none |
| `hbro2` | `Substrate.A_ie=5` (not 7), `Substrate.A_sp=0.5` (not 0.0337), `Substrate.Eth_sp=30` (not 20) |
| `sf6c4f8` | `Ions.exponent=300` (not 500), `passivationFlux=0` (not 100) |
| `cf4o2` | none in the exposed fields |

The per-material defaults of `beta_E`, `beta_P`, `rateFactors` and the
`gamma_*` maps also depend on the model (for example `beta_E[Si]` is 0.7 for
`sf6o2`, 0.1 for `hbro2`, and `gamma_F[Si]` is 0.7 for `cf4o2`). They are not
guessed from the generic class default; the runtime metadata records the
actual per-material readback.

## 5. Native construction path

Without `advanced`:

```text
nativeParameters.advanced absent
  -> ps.<Model>(*flat_values)          # unchanged historical path
  -> no nativeAdvancedParameters metadata
```

With `advanced`:

```text
nativeParameters.advanced present
  -> flat_model = ps.<Model>(*flat_values)      # same flat overload
  -> params = flat_model.getParameters()        # model-owned parameter object
  -> re-apply every flat field through the documented flatMapping
  -> snapshot defaults                         # before advanced overrides
  -> apply validated advanced blocks/scalars/maps
  -> model = ps.<Model>(params)                 # parameter-object overload
  -> snapshot effective
  -> discard flat_model (kept alive until the final constructor copied params)
```

The flat model is used as the base instead of constructing a fresh
`PlasmaEtchingParameters()`/`CF4O2Parameters()` because the flat overloads
install model-specific values (see §4) and per-material map values. Using a
fresh parameter class would silently change physics for HBrO2 and SF6C4F8.

Flat-to-native mapping (also recorded in metadata as `flatMapping`):

| model | flat field | native path |
|---|---|---|
| `sf6o2`, `hbro2` | `ionFlux` | `ionFlux` |
| | `etchantFlux` | `etchantFlux` |
| | `oxygenFlux` | `passivationFlux` |
| | `meanIonEnergy` | `Ions.meanEnergy` |
| | `sigmaIonEnergy` | `Ions.sigmaEnergy` |
| | `ionExponent` | `Ions.exponent` |
| | `oxySputterYield` | `Passivation.A_ie` |
| | `etchStopDepth` | `etchStopDepth` |
| `sf6c4f8` | `ionFlux` | `ionFlux` |
| | `etchantFlux` | `etchantFlux` |
| | `meanEnergy` | `Ions.meanEnergy` |
| | `sigmaEnergy` | `Ions.sigmaEnergy` |
| | `ionExponent` | `Ions.exponent` |
| | `etchStopDepth` | `etchStopDepth` |
| `cf4o2` | `ionFlux` | `ionFlux` |
| | `etchantFlux` | `etchantFlux` |
| | `oxygenFlux` | `oxygenFlux` |
| | `polymerFlux` | `polymerFlux` |
| | `meanIonEnergy` | `Ions.meanEnergy` |
| | `sigmaIonEnergy` | `Ions.sigmaEnergy` |
| | `ionExponent` | `Ions.exponent` |
| | `oxySputterYield` | `Passivation.A_O_ie` |
| | `polySputterYield` | `Passivation.A_C_ie` |
| | `etchStopDepth` | `etchStopDepth` |

The installed binding returns `getParameters()` as a reference into the model;
the flat model must stay alive until the final constructor has consumed it.
This is why the worker keeps `flat_model` in scope until after
`ps.<Model>(params)`. A controlled probe also showed that passing
`ps.<Model>.defaultParameters()` directly into the parameter-object constructor
can leave `Ions.thetaRMax` uninitialized, so that overload is not used.

## 6. Run metadata

For every run that contains `advanced`, `execution.nativeModel` (and the
per-step `actualParameters.nativeModel`) contains:

```json
"nativeAdvancedParameters": {
  "parameterClass": "PlasmaEtchingParameters",
  "constructor": "SF6O2Etching",
  "constructorOverload": "parameter-object; flat values applied first, then advanced overrides",
  "flatMapping": {"ionFlux": "ionFlux", "...": "..."},
  "requested": {"ions": {"meanEnergy": 60}},
  "defaults": {"blocks": {"...": {"...": 0}}, "scalars": {"...": 0}, "maps": {"...": {"default": 1, "byMaterial": {"Si": 0.7}}}},
  "effective": {"blocks": {"...": {"...": 0}}, "scalars": {"...": 0}, "maps": {"...": {"default": 1, "byMaterial": {"Si": 0.6}}}},
  "defaultsSource": "installed flat overload parameters before advanced overrides"
}
```

`defaults` is the flat-path native value before any advanced override;
`effective` is what the constructed model received. Maps record `default` and
the per-material values for the catalog materials supplied to the worker; if a
name cannot be resolved by the native `MaterialRegistry`, it is listed in
`unresolvedMaterialNames` instead of being silently omitted.

## 7. Validation

`backend/validation.py` enforces the same schema used by the worker:

* `advanced` must be a JSON object for one of the four plasma models;
* unknown top-level keys, unknown block keys, and unknown map keys are errors;
* numbers must be finite JSON numbers inside the published range; booleans must
  be JSON booleans (no `0`/`1` coercion);
* `thetaRMin <= thetaRMax` and `minAngle <= inflectAngle` are enforced for the
  `PlasmaEtchingParameters` ion block;
* `byMaterial` keys must name installed catalog materials (custom materials and
  the `Polymer` pseudo-material are not accepted here);
* existing flat-field validation is untouched and still runs first;
* the worker re-checks the allowlist and raises if it is asked to set a member
  that the schema does not describe (no silent fallback).

## 8. Evidence and limitations

`tests/test_plasma_advanced.py` pins:

* the eight pre-B1 request hashes are unchanged when `advanced` is omitted;
* the catalog schema equals the introspected installed members (including the
  absence of `thetaR*`/`B_sp` under CF4O2 and the presence of every family);
* catalog defaults equal the flat overload's native defaults for each model;
* empty `advanced` reconstructs the flat path with identical values;
* 2D **and** 3D golden runs with `ions.meanEnergy=60` and `substrate.A_ie=4`
  match an independently constructed native reference within `1e-6 um`
  (observed difference: 0.0), while differing from the default run;
* unknown key, cross-model block, type, range, and material-name negatives;
* save/reopen, run metadata, replay-project round-trip, and replay execution.

Limitations:

* No custom materials: the built-in plasma models have no verified parameter
  rows for custom materials, so existing validation still rejects them.
* The gamma/beta map *per-material* native defaults are not statically listed in
  the catalog; they are model-specific and are recorded at run time.
* `docs/*-evidence/` is not modified by this task, so the B1 acceptance "archived
  evidence" step remains open even though the automated golden comparison is in
  the test suite.
* This is a backend/API contract only; no frontend field is exposed.
