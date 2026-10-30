"""Installed ViennaPS 4.6.2 specialized process allowlist and UI metadata."""
from __future__ import annotations

from copy import deepcopy
from typing import Any


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


CATALOG: list[dict[str, Any]] = [
    {"id": "sf6o2", "label": "SF₆/O₂ 等离子体刻蚀", "nativeClass": "SF6O2Etching", "operation": "etch", "usesFlux": True,
     "fields": [number("ionFlux", 1), number("etchantFlux", 1), number("oxygenFlux", 0.2),
                number("meanIonEnergy", 100), number("sigmaIonEnergy", 10), number("ionExponent", 100),
                number("oxySputterYield", 3), number("etchStopDepth", -1_000_000, minimum=-1_000_000)]},
    {"id": "cf4o2", "label": "CF₄/O₂ 等离子体刻蚀", "nativeClass": "CF4O2Etching", "operation": "etch", "usesFlux": True,
     "fields": [number("ionFlux", 1), number("etchantFlux", 1), number("oxygenFlux", 0.2), number("polymerFlux", 0.2),
                number("meanIonEnergy", 100), number("sigmaIonEnergy", 10), number("ionExponent", 100),
                number("oxySputterYield", 3), number("polySputterYield", 3), number("etchStopDepth", -1_000_000, minimum=-1_000_000)]},
    {"id": "hbro2", "label": "HBr/O₂ 等离子体刻蚀", "nativeClass": "HBrO2Etching", "operation": "etch", "usesFlux": True,
     "fields": [number("ionFlux", 1), number("etchantFlux", 1), number("oxygenFlux", 0.2),
                number("meanIonEnergy", 100), number("sigmaIonEnergy", 10), number("ionExponent", 100),
                number("oxySputterYield", 3), number("etchStopDepth", -1_000_000, minimum=-1_000_000)]},
    {"id": "sf6c4f8", "label": "SF₆/C₄F₈ 等离子体刻蚀", "nativeClass": "SF6C4F8Etching", "operation": "etch", "usesFlux": True,
     "fields": [number("ionFlux", 1), number("etchantFlux", 1), number("meanEnergy", 100),
                number("sigmaEnergy", 10), number("ionExponent", 300), number("etchStopDepth", -1_000_000, minimum=-1_000_000)]},
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
     "fields": [enum("oxidant", "Dry", [("Dry", "Dry · O₂"), ("Wet", "Wet · H₂O")], label="氧化剂"),
                enum("orientation", "Si100", [("Si100", "Si (100)"), ("Si111", "Si (111)"), ("PolySi", "多晶硅")], label="晶向"),
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
     "depositMaterial": "SiGe", "fields": [rates("materialRates", 0.05, label="外延材料速率表"),
                number("rate111", 0.5), number("rate100", 1.0)]},
    # GeometricTrenchDeposition is a ViennaLS geometric-advection distribution,
    # not a velocity-field model: it deposits on the top level set (created by
    # the step's selectable depositMaterial) without a native material table.
    # 4.6.2 ignores trenchWidth, and its thickness does not scale with the
    # process duration. It evaluates the local radius from the absolute z
    # coordinate, with the trench top assumed at z=0. Validation surfaces these
    # native semantics as a warning instead of pretending they are ordinary
    # deposition parameters. The installed 3D binding SIGSEGVs in a controlled
    # probe, so only the verified 2D constructor is exposed.
    {"id": "geometricTrenchDeposition", "label": "几何沟槽沉积", "nativeClass": "GeometricTrenchDeposition",
     "operation": "deposit", "usesFlux": False, "experimental": True, "dimensions": [2],
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
    return {"schemaVersion": 1, "engineVersion": "4.6.2", "unitsNote": "安装版绑定未声明单位的数值按原样传入；只对原生文档明确标注单位的氧化字段显示单位。",
            "models": deepcopy(CATALOG)}


def model_spec(model_id: str) -> dict[str, Any] | None:
    return BY_ID.get(model_id)
