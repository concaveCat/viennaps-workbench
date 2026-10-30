"""Installed ViennaPS 4.6.2 specialized process allowlist and UI metadata."""
from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

from .engine import OXIDATION_ADVANCED_RESTRICTED, engine_version


def number(key: str, default: float, *, minimum: float = 0.0, maximum: float = 1_000_000.0,
           step: float = 0.1, label: str | None = None, unit: str | None = None) -> dict[str, Any]:
    return {"key": key, "label": label or key, "kind": "number", "default": default,
            "minimum": minimum, "maximum": maximum, "step": step, "nativeUnit": unit}


def enum(key: str, default: str, values: list[tuple[str, str]], *, label: str | None = None) -> dict[str, Any]:
    return {"key": key, "label": label or key, "kind": "enum", "default": default,
            "options": [{"value": value, "label": title} for value, title in values]}


def rates(key: str, default: float = 0.0, *, label: str = "材料速率表") -> dict[str, Any]:
    return {"key": key, "label": label, "kind": "materialRates", "default": default,
            "minimum": 0.0, "maximum": 1_000_000.0, "step": 0.01, "nativeUnit": None}


# ``SelectiveEpitaxy`` binds a third constructor overload that takes only a
# fixed-size three-vector of normal-vector factors (plus rate111/rate100). The
# installed header computes ``max_i(|nvFactors[i] * normal[i]|)`` and maps that
# scalar from [1/sqrt(D), 1] to [rate111, rate100]. Values outside [0, 1] push
# the interpolation outside that native range, so only the documented range is
# exposed. The native defaults are dimension-dependent and are NOT [1, 1, 1]:
# 2D -> [0.5, 1, 0], 3D -> [0.5, 0, 1]. Omitting the key keeps the existing
# materialRates overload and therefore the exact current request hash/artifacts.
NV_FACTORS_FIELD: dict[str, Any] = {
    "key": "nvFactors",
    "label": "法向方向因子 [x, y, z]",
    "kind": "nvFactors",
    "length": 3,
    "default": None,
    "minimum": 0.0,
    "maximum": 1.0,
    "step": 0.01,
    "nativeUnit": None,
    "nativeClass": "SelectiveEpitaxy",
    "nativeConstructor": "viennaps.d{D}.SelectiveEpitaxy(nvFactors, rate111, rate100)",
    "nativeDefaultByDimension": {"2": [0.5, 1.0, 0.0], "3": [0.5, 0.0, 1.0]},
    "defaultNote": ("omit to use the installed materialRates constructor and its native dimension default; "
                    "an explicit vector uses the separate nvFactors overload and applies materialRates through setMaterialRate"),
}


def coverage_tolerance_field() -> dict[str, Any]:
    """Optional native ``CoverageParameters.tolerance`` override.

    The worker has always pinned 1e-3 for the four plasma models while the
    native struct default is 0.0. Omitting the key keeps that historical
    value; an explicit value is passed to the same native object. Negative,
    non-finite or >1 values are rejected because the native convergence test
    is ``delta > tolerance``: a negative/NaN value never converges and the
    installed strategy then iterates up to the unsigned default
    (4294967295) instead of the 10-iteration fallback used for tolerance 0.
    """
    return {
        "key": "coverageTolerance",
        "label": "覆盖率收敛容差",
        "kind": "number",
        "default": 0.001,
        "minimum": 0.0,
        "maximum": 1.0,
        "step": 0.0001,
        "nativeUnit": None,
        "nativeClass": "CoverageParameters",
        "nativeMember": "tolerance",
        "nativeDefault": 0.0,
        "workbenchHistoricalDefault": 0.001,
        "defaultSource": "workbench",
        "defaultNote": ("omit to keep the Worker's historical 1e-3; the native default 0.0 leaves maxIterations "
                        "at its default and is capped to 10 coverage-initialization iterations"),
    }


FLUOROCARBON_MATERIAL_DEFAULTS = {
    "A_ie": 0.0361, "A_sp": 0.0139, "B_sp": 9.3, "E_a": 0.168,
    "Eth_ie": 4.0, "Eth_sp": 18.0, "K": 0.002789491704544977,
    "beta_e": 0.9, "beta_p": 0.26, "density": 2.2,
}


def ibe_fields() -> list[dict[str, Any]]:
    """Native IBEParameters fields shared by ionBeam and faradayCage.

    FaradayCageParameters wraps this exact native struct in ``ibeParams`` and
    adds ``cageAngle``; keeping one definition prevents the two allowlist rows
    from drifting apart.
    """
    return [number("meanEnergy", 100), number("sigmaEnergy", 10), number("thresholdEnergy", 20),
            number("thetaRMin", 70, maximum=180), number("thetaRMax", 90, maximum=180),
            number("minAngle", 85, maximum=180), number("inflectAngle", 89, maximum=180),
            number("exponent", 100), number("n_l", 10), number("tiltAngle", 0, minimum=-180, maximum=180),
            number("planeWaferRate", 0.1), number("redepositionRate", 0), number("redepositionThreshold", 0.1),
            number("cos4YieldA1", 0), number("cos4YieldA2", 0), number("cos4YieldA3", 0), number("cos4YieldA4", 0),
            rates("materialPlaneWaferRateByMaterial", 0.0, label="材料平面晶圆速率表")]


def advanced_number(key: str, default: float, *, minimum: float = 0.0, maximum: float = 1_000_000.0,
                    step: float = 0.01, unit: str | None = None) -> dict[str, Any]:
    return {"key": key, "kind": "number", "default": default, "minimum": minimum,
            "maximum": maximum, "step": step, "nativeUnit": unit}


def advanced_boolean(key: str, default: bool) -> dict[str, Any]:
    return {"key": key, "kind": "boolean", "default": default}


# Flat catalog fields are also the effective values the worker passes when
# ``advanced`` is omitted. The advanced schema below takes every shared default
# from these lists, so a client materialising the catalog cannot change
# behaviour by accident.
PLASMA_FLAT_FIELDS = [
    number("ionFlux", 1), number("etchantFlux", 1), number("oxygenFlux", 0.2),
    number("meanIonEnergy", 100), number("sigmaIonEnergy", 10), number("ionExponent", 100),
    number("oxySputterYield", 3), number("etchStopDepth", -1_000_000, minimum=-1_000_000),
]
SF6C4F8_FLAT_FIELDS = [
    number("ionFlux", 1), number("etchantFlux", 1), number("meanEnergy", 100),
    number("sigmaEnergy", 10), number("ionExponent", 300),
    number("etchStopDepth", -1_000_000, minimum=-1_000_000),
]
CF4O2_FLAT_FIELDS = [
    number("ionFlux", 1), number("etchantFlux", 1), number("oxygenFlux", 0.2),
    number("polymerFlux", 0.2), number("meanIonEnergy", 100), number("sigmaIonEnergy", 10),
    number("ionExponent", 100), number("oxySputterYield", 3), number("polySputterYield", 3),
    number("etchStopDepth", -1_000_000, minimum=-1_000_000),
]


# Payload keys are lower-case aliases; ``native`` is the exact installed member
# path. Defaults and ranges come from introspecting ViennaPS 4.6.2
# (PlasmaEtchingParameters*/CF4O2Parameters*) and the flat overloads, not from
# prose documentation. ``tests/test_plasma_advanced.py`` fails if the installed
# binding stops matching this table.
_PLASMA_ADVANCED_TEMPLATE: dict[str, Any] = {
    "parameterClass": "PlasmaEtchingParameters",
    "constructorOverload": "parameters",
    "blocks": {
        "ions": {"native": "Ions", "fields": [
            advanced_number("meanEnergy", 100.0), advanced_number("sigmaEnergy", 10.0),
            advanced_number("exponent", 500.0), advanced_number("n_l", 10.0),
            advanced_number("minAngle", 1.3962634, maximum=math.pi, unit="rad"),
            advanced_number("inflectAngle", 1.55334303, maximum=math.pi, unit="rad"),
            advanced_number("thetaRMin", 1.2217304763960306, maximum=math.pi, unit="rad"),
            advanced_number("thetaRMax", 1.5707963267948966, maximum=math.pi, unit="rad"),
        ]},
        "mask": {"native": "Mask", "fields": [
            advanced_number("A_sp", 0.0139), advanced_number("B_sp", 9.3),
            advanced_number("Eth_sp", 20.0), advanced_number("rho", 500.0, minimum=0.000001),
        ]},
        "passivation": {"native": "Passivation", "fields": [
            advanced_number("A_ie", 3.0), advanced_number("Eth_ie", 10.0),
        ]},
        "polymer": {"native": "Polymer", "fields": [
            advanced_number("A_sp", 0.02), advanced_number("B_sp", 8.5),
            advanced_number("Eth_sp", 15.0), advanced_number("rho", 5.0, minimum=0.000001),
            advanced_number("a1", -0.26, minimum=-1_000_000.0), advanced_number("a2", 2.72, minimum=-1_000_000.0),
            advanced_number("a3", -4.3, minimum=-1_000_000.0), advanced_number("a4", 1.95, minimum=-1_000_000.0),
            advanced_boolean("usePolyCosThetaYield", False),
        ]},
        # Substrate.B_ie is declared by the native struct but unused by the
        # CPU surface model (the angle-dependent branch is commented out), so
        # it is deliberately not exposed.
        "substrate": {"native": "Substrate", "fields": [
            advanced_number("A_ie", 7.0), advanced_number("A_sp", 0.0337),
            advanced_number("B_sp", 9.3),
            advanced_number("Eth_ie", 15.0), advanced_number("Eth_sp", 20.0),
            advanced_number("beta_sigma", 0.04), advanced_number("k_sigma", 300.0),
            advanced_number("rho", 5.02, minimum=0.000001),
        ]},
    },
    "scalars": [advanced_number("passivationFlux", 100.0)],
    "maps": [
        {"key": "beta_E", "native": "beta_E", "default": 1.0, "minimum": 0.0, "maximum": 1.0},
        {"key": "beta_P", "native": "beta_P", "default": 1.0, "minimum": 0.0, "maximum": 1.0},
        {"key": "rateFactors", "native": "rateFactors", "default": 1.0, "minimum": 0.0, "maximum": 1_000_000.0},
    ],
}

_CF4O2_ADVANCED: dict[str, Any] = {
    "parameterClass": "CF4O2Parameters",
    "constructorOverload": "parameters",
    "blocks": {
        "ions": {"native": "Ions", "fields": [
            advanced_number("meanEnergy", 100.0), advanced_number("sigmaEnergy", 10.0),
            advanced_number("exponent", 500.0), advanced_number("n_l", 10.0),
            advanced_number("minAngle", 1.3962634, maximum=math.pi, unit="rad"),
            advanced_number("inflectAngle", 1.55334303, maximum=math.pi, unit="rad"),
        ]},
        "mask": {"native": "Mask", "fields": [
            advanced_number("A_sp", 0.0139), advanced_number("Eth_sp", 20.0),
            advanced_number("rho", 500.0, minimum=0.000001),
        ]},
        "passivation": {"native": "Passivation", "fields": [
            advanced_number("A_C_ie", 3.0), advanced_number("A_O_ie", 3.0),
            advanced_number("Eth_C_ie", 10.0), advanced_number("Eth_O_ie", 10.0),
        ]},
        "si": {"native": "Si", "fields": [
            advanced_number("A_ie", 7.0), advanced_number("A_sp", 0.0337),
            advanced_number("Eth_ie", 15.0), advanced_number("Eth_sp", 20.0),
            advanced_number("beta_sigma", 0.04), advanced_number("k_sigma", 300.0),
            advanced_number("rho", 5.02, minimum=0.000001),
        ]},
        # The native CF4O2 surface model only consumes SiGe.beta_sigma,
        # SiGe.k_sigma and SiGe.rho. A_ie/A_sp/Eth_ie/Eth_sp are ignored in the
        # SiGe branch, and x only computes the initial k_sigma default (it does
        # not recompute it when changed), so those fields are not exposed.
        "siGe": {"native": "SiGe", "fields": [
            advanced_number("beta_sigma", 0.04), advanced_number("k_sigma", 345.168420976798),
            advanced_number("rho", 4.84, minimum=0.000001),
        ]},
    },
    "scalars": [advanced_boolean("fluxIncludeSticking", False)],
    "maps": [
        {"key": name, "native": name, "default": 1.0, "minimum": 0.0, "maximum": 1.0}
        for name in ("gamma_C", "gamma_C_oxidized", "gamma_F", "gamma_F_oxidized",
                     "gamma_O", "gamma_O_passivated")
    ],
}

_FLAT_MAPPING_SF6 = {
    "ionFlux": "ionFlux", "etchantFlux": "etchantFlux", "oxygenFlux": "passivationFlux",
    "meanIonEnergy": "Ions.meanEnergy", "sigmaIonEnergy": "Ions.sigmaEnergy",
    "ionExponent": "Ions.exponent", "oxySputterYield": "Passivation.A_ie",
    "etchStopDepth": "etchStopDepth",
}
_FLAT_MAPPING_SF6C4F8 = {
    "ionFlux": "ionFlux", "etchantFlux": "etchantFlux",
    "meanEnergy": "Ions.meanEnergy", "sigmaEnergy": "Ions.sigmaEnergy",
    "ionExponent": "Ions.exponent", "etchStopDepth": "etchStopDepth",
}
_FLAT_MAPPING_CF4O2 = {
    "ionFlux": "ionFlux", "etchantFlux": "etchantFlux",
    "oxygenFlux": "oxygenFlux", "polymerFlux": "polymerFlux",
    "meanIonEnergy": "Ions.meanEnergy", "sigmaIonEnergy": "Ions.sigmaEnergy",
    "ionExponent": "Ions.exponent", "oxySputterYield": "Passivation.A_O_ie",
    "polySputterYield": "Passivation.A_C_ie", "etchStopDepth": "etchStopDepth",
}


def _apply_flat_defaults(advanced: dict[str, Any], fields: list[dict[str, Any]],
                         flat_mapping: dict[str, str]) -> None:
    """Make shared advanced defaults equal the flat catalog defaults.

    ``defaultSource`` distinguishes a value the workbench already sends through
    the flat overload (``flat``) from a native parameter-class default with no
    flat equivalent (``nativeParameterClass``/``nativeMaterialMap``).
    """
    flat_defaults = {field["key"]: field["default"] for field in fields}
    targets: dict[str, dict[str, Any]] = {}
    for block in advanced["blocks"].values():
        for field in block["fields"]:
            targets[f'{block["native"]}.{field["key"]}'] = field
    for field in advanced["scalars"]:
        targets[field["key"]] = field
    for flat_key, native_path in flat_mapping.items():
        target = targets.get(native_path)
        if target is None:
            continue
        target["default"] = flat_defaults[flat_key]
        target["flatKey"] = flat_key
        target["defaultSource"] = "flat"
    for block in advanced["blocks"].values():
        for field in block["fields"]:
            field.setdefault("defaultSource", "nativeParameterClass")
    for field in advanced["scalars"]:
        field.setdefault("defaultSource", "nativeParameterClass")
    for row in advanced["maps"]:
        row.setdefault("defaultSource", "nativeMaterialMap")


def _plasma_advanced(fields: list[dict[str, Any]], flat_mapping: dict[str, str], *,
                     drop_blocks: tuple[str, ...] = (), drop_scalars: tuple[str, ...] = (),
                     drop_maps: tuple[str, ...] = (), default_patches: dict[str, float] | None = None,
                     rejected: dict[str, str] | None = None) -> dict[str, Any]:
    """Clone the shared PlasmaEtchingParameters schema for one model."""
    advanced = deepcopy(_PLASMA_ADVANCED_TEMPLATE)
    advanced["blocks"] = {name: block for name, block in advanced["blocks"].items()
                          if name not in drop_blocks}
    advanced["scalars"] = [field for field in advanced["scalars"]
                           if field["key"] not in drop_scalars]
    advanced["maps"] = [row for row in advanced["maps"] if row["key"] not in drop_maps]
    advanced["flatMapping"] = dict(flat_mapping)
    _apply_flat_defaults(advanced, fields, flat_mapping)
    by_block = {block["native"]: block for block in advanced["blocks"].values()}
    by_scalar = {field["key"]: field for field in advanced["scalars"]}
    for path, value in (default_patches or {}).items():
        if "." in path:
            block_name, field_name = path.split(".", 1)
            next(field for field in by_block[block_name]["fields"] if field["key"] == field_name)["default"] = value
        else:
            by_scalar[path]["default"] = value
    if rejected:
        advanced["rejectedKeys"] = dict(rejected)
    return advanced


def _cf4o2_advanced() -> dict[str, Any]:
    advanced = deepcopy(_CF4O2_ADVANCED)
    advanced["flatMapping"] = dict(_FLAT_MAPPING_CF4O2)
    _apply_flat_defaults(advanced, CF4O2_FLAT_FIELDS, _FLAT_MAPPING_CF4O2)
    return advanced


# ``steps[].advancedOxidation`` is a top-level optional object on a
# specialized:oxidation step, analogous to ``steps[].surfaceDiffusion``. Every
# row below is a setter that was proven on the installed 4.6.2 binding to
# change the returned surface deterministically; see
# docs/oxidation-advanced-contract.md for the probe evidence. Defaults are the
# installed native defaults, so materialising the block is a behaviour no-op.
OXIDATION_ADVANCED_FIELDS: list[dict[str, Any]] = [
    {"key": "cflFactor", "label": "CFL 系数", "kind": "number", "default": 0.499,
     "minimum": 0.01, "maximum": 0.499, "step": 0.001, "nativeUnit": None,
     "nativeSetter": "Oxidation.setCFLFactor",
     "minimumNote": "installed native clamps to [0.001, 0.499]; the Workbench floor is 0.01 to keep bounded-child runs tractable"},
    {"key": "mechanicsTolerance", "label": "力学收敛容差", "kind": "number", "default": 0.01,
     "minimum": 0.01, "maximum": 0.2, "step": 0.001, "nativeUnit": None,
     "nativeSetter": "Oxidation.setMechanicsTolerance",
     "minimumNote": "values below the installed default can stop the mechanics coupling from converging; only looser values are accepted"},
    {"key": "couplingTolerance", "label": "扩散-变形耦合容差", "kind": "number", "default": 0.05,
     "minimum": 0.05, "maximum": 0.5, "step": 0.001, "nativeUnit": None,
     "nativeSetter": "Oxidation.setCouplingTolerance",
     "minimumNote": "must be at least mechanicsTolerance; the native validator warns when it is tighter"},
    {"key": "simplePressureRelaxation", "label": "SIMPLE 压力松弛", "kind": "number", "default": 0.5,
     "minimum": 0.01, "maximum": 1.0, "step": 0.01, "nativeUnit": None,
     "nativeSetter": "Oxidation.setSimplePressureRelaxation"},
]

OXIDATION_ADVANCED_NATIVE_CLASS = "Oxidation"
OXIDATION_ADVANCED_LOCATION = "steps[].advancedOxidation"


def oxidation_advanced_spec() -> dict[str, Any]:
    return {
        "location": OXIDATION_ADVANCED_LOCATION,
        "nativeClass": OXIDATION_ADVANCED_NATIVE_CLASS,
        "requiresNativeModel": "oxidation",
        "objectShape": "strict object; unknown keys are rejected",
        "defaultsSource": "installed viennaps 4.6.2 Oxidation member defaults; re-probed on 4.7.0",
        "runtimeGate": {key: reason for key, reason in OXIDATION_ADVANCED_RESTRICTED.items()},
        "fields": deepcopy(OXIDATION_ADVANCED_FIELDS),
        "excluded": [
            {"member": "setGpuMode", "reason": "CPU-only batch; GPU oxidation has no host evidence"},
            {"member": "setGpuPreconditioner", "reason": "GPU-only"},
            {"member": "setMaxGridPoints", "reason": "a solver cap, not a physics control; below the solve region it raises RuntimeError, above it is inert"},
            {"member": "setSolveBounds / clearSolveBounds", "reason": "raw Cartesian index bounds; a controlled probe hit 'Cartesian solve region is empty' at valid-looking bounds"},
            {"member": "setCouplingIterations", "reason": "explicit default 30 is inert; lowering it to 1 fails to converge"},
            {"member": "setMechanicsIterations", "reason": "explicit default 200 is inert; lowering it to 1 fails to converge"},
            {"member": "setPressureIterations / setStokesIterations", "reason": "explicit defaults 500 are inert; lowering pressure to 1 fails to converge"},
            {"member": "setPressureTolerance / setStokesTolerance", "reason": "changing them requires a coupled mechanicsTolerance adjustment; not exposed as independent knobs"},
            {"member": "setSimpleVelocityRelaxation", "reason": "no surface change above 1e-8 um in the controlled 2D/3D probes"},
            {"member": "setMaskParameters and setMask* mechanics setters", "reason": "no verified LOCOS output on this host; the controlled 2D probe collapsed the oxide level set, so mask mechanics stays unexposed"},
            {"member": "estimatePlanarOxideThickness", "reason": "read-only helper, not a setter; used as a native readback"},
            {"member": "OxidationParameters / psOxidation / psProcessContext", "reason": "not bound by the installed Python module"},
        ],
    }


CATALOG: list[dict[str, Any]] = [
    {"id": "sf6o2", "label": "SF₆/O₂ 等离子体刻蚀", "nativeClass": "SF6O2Etching", "operation": "etch", "usesFlux": True,
     "optionalKeys": ["advanced", "coverageTolerance"],
     "optionalFields": [coverage_tolerance_field()],
     "advanced": _plasma_advanced(PLASMA_FLAT_FIELDS, _FLAT_MAPPING_SF6),
     "fields": list(PLASMA_FLAT_FIELDS)},
    {"id": "cf4o2", "label": "CF₄/O₂ 等离子体刻蚀", "nativeClass": "CF4O2Etching", "operation": "etch", "usesFlux": True,
     "optionalKeys": ["advanced", "coverageTolerance"],
     "optionalFields": [coverage_tolerance_field()],
     "advanced": _cf4o2_advanced(),
     "fields": list(CF4O2_FLAT_FIELDS)},
    {"id": "hbro2", "label": "HBr/O₂ 等离子体刻蚀", "nativeClass": "HBrO2Etching", "operation": "etch", "usesFlux": True,
     "optionalKeys": ["advanced", "coverageTolerance"],
     "optionalFields": [coverage_tolerance_field()],
     # HBrO2Etching's native defaults differ from PlasmaEtchingParameters():
     # Substrate.A_ie/A_sp/Eth_sp are 5.0/0.5/30.0 instead of 7.0/0.0337/20.0.
     "advanced": _plasma_advanced(PLASMA_FLAT_FIELDS, _FLAT_MAPPING_SF6, default_patches={
         "Substrate.A_ie": 5.0, "Substrate.A_sp": 0.5, "Substrate.Eth_sp": 30.0}),
     "fields": list(PLASMA_FLAT_FIELDS)},
    {"id": "sf6c4f8", "label": "SF₆/C₄F₈ 等离子体刻蚀", "nativeClass": "SF6C4F8Etching", "operation": "etch", "usesFlux": True,
     "optionalKeys": ["advanced", "coverageTolerance"],
     "optionalFields": [coverage_tolerance_field()],
     # SF6C4F8Etching has no passivation particle: its surface model
     # dereferences a missing "passivationFlux" flux scalar when the value is
     # positive (SIGSEGV). The model also does not read the Passivation block or
     # beta_P, so those are absent from this model's allowlist.
     "advanced": _plasma_advanced(
         SF6C4F8_FLAT_FIELDS, _FLAT_MAPPING_SF6C4F8,
         drop_blocks=("passivation",), drop_scalars=("passivationFlux",), drop_maps=("beta_P",),
         rejected={"passivationFlux": "SF6C4F8Etching has no passivation particle; a positive value "
                                     "dereferences a missing native flux scalar and can SIGSEGV"}),
     "fields": list(SF6C4F8_FLAT_FIELDS)},
    {"id": "fluorocarbon", "label": "Fluorocarbon 等离子体刻蚀", "nativeClass": "FluorocarbonEtching", "operation": "etch", "usesFlux": True,
     "fields": [number("ionFlux", 1), number("etchantFlux", 1), number("polyFlux", 0.2),
                number("k_ie", 2), number("k_ev", 2), number("delta_p", 1), number("temperature", 300),
                number("meanEnergy", 100), number("sigmaEnergy", 10), number("exponent", 500), number("n_l", 10),
                number("inflectAngle", 1.55334303), number("minAngle", 1.3962634),
                number("etchStopDepth", -1_000_000, minimum=-1_000_000),
                {"key": "materialParametersByMaterial", "label": "原生材料参数表", "kind": "fluorocarbonMaterials",
                 "defaults": FLUOROCARBON_MATERIAL_DEFAULTS, "minimum": -1_000_000.0, "maximum": 1_000_000.0,
                 "step": 0.01, "nativeUnit": None}]},
    {"id": "ionBeam", "label": "离子束刻蚀（IBE）", "nativeClass": "IonBeamEtching", "operation": "etch", "usesFlux": True,
     "fields": ibe_fields()},
    # Experimental: native construction and CPU runs are covered by
    # tests/test_engine_batch1_specialized.py, but no Stage 8/9 direct-reference
    # acceptance row has been archived for this model yet. Validation warns
    # instead of claiming verified CPU coverage; GPU coverage stays unclaimed.
    # ViennaPS 4.6.2 binds FaradayCageEtching in both dimensions, but the 2D
    # native model does not return for the controlled probe, so only the 3D
    # constructor path is exposed.
    {"id": "faradayCage", "label": "法拉第笼离子束刻蚀", "nativeClass": "FaradayCageEtching", "operation": "etch",
     "usesFlux": True, "experimental": True, "dimensions": [3],
     "fields": [number("cageAngle", 0, maximum=180)] + ibe_fields()},
    {"id": "wetEtch", "label": "晶向湿法刻蚀", "nativeClass": "WetEtching", "operation": "etch", "usesFlux": False,
     "fields": [rates("materialRates", 0.0),
                {"key": "useCrystallineRates", "label": "使用晶向速率", "kind": "boolean", "default": False},
                {"key": "direction100", "label": "direction100 [x,y,z]", "kind": "vector3", "default": [1, 0, 0]},
                {"key": "direction010", "label": "direction010 [x,y,z]", "kind": "vector3", "default": [0, 1, 0]},
                number("rate100", 0.1), number("rate110", 0.1), number("rate111", 0.1), number("rate311", 0.1)]},
    {"id": "teosDeposition", "label": "TEOS 热沉积", "nativeClass": "TEOSDeposition", "operation": "deposit", "usesFlux": True,
     "depositMaterial": "SiO2", "fields": [number("stickingProbabilityP1", 0.5, maximum=1), number("rateP1", 0.05), number("orderP1", 1),
                number("stickingProbabilityP2", 0, maximum=1), number("rateP2", 0), number("orderP2", 0)]},
    {"id": "teospecvd", "label": "TEOS PECVD", "nativeClass": "TEOSPECVD", "operation": "deposit", "usesFlux": True,
     "depositMaterial": "SiO2", "fields": [number("stickingProbabilityRadical", 0.5, maximum=1), number("depositionRateRadical", 0.05),
                number("depositionRateIon", 0.02), number("exponentIon", 1), number("stickingProbabilityIon", 1, maximum=1),
                number("reactionOrderRadical", 1), number("reactionOrderIon", 1), number("minAngleIon", 0, maximum=180)]},
    {"id": "ald", "label": "单粒子 ALD", "nativeClass": "SingleParticleALD", "operation": "deposit", "usesFlux": True,
     "depositMaterialSelectable": True, "fields": [number("stickingProbability", 0.5, maximum=1), number("gasMeanFreePath", 1, minimum=-1),
                number("growthPerCycle", 0.02), number("incomingFlux", 1), number("evaporationFlux", 0.1),
                number("s0", 1), number("coverageDiffusionCoefficient", 0), number("pulseTime", 0.02),
                number("purgePulseTime", 0), number("coverageTimeStep", 0.01), number("purgeTimeStep", 1),
                number("numCycles", 1, minimum=1, maximum=10000, step=1)]},
    {"id": "oxidation", "label": "硅热氧化", "nativeClass": "Oxidation", "operation": "native", "usesFlux": False,
     "advancedOxidation": oxidation_advanced_spec(),
     "fields": [enum("oxidant", "Dry", [("Dry", "Dry · O₂"), ("Wet", "Wet · H₂O")], label="氧化剂"),
                enum("orientation", "Si100", [("Si100", "Si (100)"), ("Si110", "Si (110)"),
                                             ("Si111", "Si (111)"), ("PolySi", "多晶硅")], label="晶向"),
                number("temperatureC", 1000, minimum=800, maximum=1200, step=1, unit="°C"),
                number("pressureAtm", 1, minimum=0.000001, unit="atm"), number("timeHours", 0.001, minimum=0.000003, maximum=0.1666667, unit="h"),
                number("timeStepHours", 0.0001, minimum=0.000001, unit="h"), number("transferCoefficientUmHr", 0.1, unit="µm/hr"),
                number("initialOxideThicknessUm", 0, unit="µm"), number("reactionActivationVolumeM3", 0, unit="m³"),
                number("diffusionActivationVolumeM3", 0, unit="m³"),
                {"key": "maskMaterial", "label": "掩膜材料", "kind": "materialOptional", "default": ""},
                {"key": "siliconMaterial", "label": "硅材料", "kind": "material", "default": "Si"},
                {"key": "oxideMaterial", "label": "氧化层材料", "kind": "material", "default": "SiO2"}]},
    {"id": "oxideRegrowth", "label": "氧化层再生与再沉积", "nativeClass": "OxideRegrowth", "operation": "native", "usesFlux": False,
     "requiresCellSet": True, "fields": [number("nitrideEtchRate", 0.01), number("oxideEtchRate", 0.005),
                number("redepositionRate", 0.001), number("redepositionThreshold", 0.01), number("redepositionTimeInt", 0.1),
                number("diffusionCoefficient", 0.02), number("sinkStrength", 0.1), number("scallopVelocity", 0.05),
                number("centerVelocity", 0.05), number("topHeight", 0.6), number("centerWidth", 1.2),
                number("stabilityFactor", 0.15)]},
    {"id": "selectiveEpitaxy", "label": "选择性外延", "nativeClass": "SelectiveEpitaxy", "operation": "deposit", "usesFlux": False,
     "depositMaterial": "SiGe", "optionalKeys": ["nvFactors"], "optionalFields": [NV_FACTORS_FIELD],
     "fields": [rates("materialRates", 0.05, label="外延材料速率表"),
                number("rate111", 0.5), number("rate100", 1.0)]},
    # GeometricTrenchDeposition is a ViennaLS geometric-advection distribution,
    # not a velocity-field model: it deposits on the top level set (created by
    # the step's selectable depositMaterial) without a native material table.
    # 4.6.2 ignores trenchWidth, and its thickness does not scale with the
    # process duration. It evaluates the local radius from the absolute z
    # coordinate, with the trench top assumed at z=0. Validation surfaces these
    # native semantics as a warning instead of pretending they are ordinary
    # deposition parameters. The audit's 3D SIGSEGV was reproduced as a
    # parameter-reach crash (depositionRate + 3*gridDelta < local radius) that
    # also occurs in 2D; with that guard enforced, 2D and 3D use the same native
    # class and all parametric geometry kinds are exercised. GDS remains gated
    # because its vertical span is not described by the project.
    {"id": "geometricTrenchDeposition", "label": "几何沟槽沉积", "nativeClass": "GeometricTrenchDeposition",
     "operation": "deposit", "usesFlux": False, "experimental": True, "dimensions": [2, 3],
     "depositMaterialSelectable": True,
     "fields": [number("trenchWidth", 1.0, minimum=0.000001),
                number("trenchDepth", 4.0, minimum=0.000001),
                number("depositionRate", 0.5, minimum=0.000001),
                number("bottomMed", 0.2, minimum=0),
                number("a", 0.3), number("b", 0.1),
                number("n", 1.0, minimum=1, maximum=100, step=1)]},
    {"id": "neutralTransport", "label": "中性粒子输运", "nativeClass": "NeutralTransport", "operation": "native", "usesFlux": True,
     "fields": [number("incomingFlux", 0.2), number("sourceDistributionPower", 1), number("etchFrontSticking", 0.5, maximum=1),
                number("zeroCoverageSticking", 0.1, maximum=1), number("coverageTimeStep", 0.05),
                number("desorptionRate", 0), number("surfaceDiffusionCoefficient", 0), number("surfaceSiteDensity", 1.66e-5),
                {"key": "useSteadyStateCoverage", "label": "稳态覆盖率", "kind": "boolean", "default": True},
                {"key": "etchFrontMaterial", "label": "刻蚀前沿材料", "kind": "material", "default": "Si"},
                {"key": "fluxLabel", "label": "fluxLabel", "kind": "text", "default": "neutralFlux"},
                {"key": "coverageLabel", "label": "coverageLabel", "kind": "text", "default": "neutralCoverage"}]},
]

BY_ID = {row["id"]: row for row in CATALOG}


def specialized_catalog() -> dict[str, Any]:
    return {"schemaVersion": 1, "engineVersion": engine_version(), "unitsNote": "安装版绑定未声明单位的数值按原样传入；只对原生文档明确标注单位的氧化字段显示单位。",
            "models": deepcopy(CATALOG)}


def model_spec(model_id: str) -> dict[str, Any] | None:
    return BY_ID.get(model_id)


def advanced_spec(model_id: str) -> dict[str, Any] | None:
    """Return the strict ``nativeParameters.advanced`` schema for a model."""
    spec = BY_ID.get(model_id)
    return spec.get("advanced") if spec else None
