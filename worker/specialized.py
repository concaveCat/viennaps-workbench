"""Allowlisted ViennaPS 4.6.2 model constructors for schema-7 specialized steps."""
from __future__ import annotations

from typing import Any

from backend.specialized_models import model_spec


def build_specialized_model(ps: Any, domain: Any, project: dict[str, Any], step: dict[str, Any],
                            material_handle: Any, material_ids: dict[str, int]) -> tuple[Any, list[Any], dict[str, Any], float]:
    """Build an installed native model and its native process parameters.

    The model ID is an application allowlist key; submitted JSON never selects
    an arbitrary Python attribute or constructor.
    """
    model_id = step["nativeModel"]
    spec = model_spec(model_id)
    if spec is None:
        raise RuntimeError(f"unavailable ViennaPS 4.6.2 model {model_id!r}")
    p = step["nativeParameters"]
    extra: list[Any] = []
    duration = float(step["duration"]) if step.get("duration") is not None else float(p["timeHours"]) * 3600.0
    resolved: dict[str, Any] = {"nativeClass": spec["nativeClass"], "nativeModel": model_id,
                                "operation": step["operation"], "nativeParameters": p,
                                "durationSeconds": duration, "cpuThreads": int(project["solver"]["cpuThreads"]),
                                "nativeProcessEngine": "CPU_DISK pinned by the worker; no silent fallback"}

    if model_id == "sf6o2":
        model = ps.SF6O2Etching(*(float(p[key]) for key in (
            "ionFlux", "etchantFlux", "oxygenFlux", "meanIonEnergy", "sigmaIonEnergy",
            "ionExponent", "oxySputterYield", "etchStopDepth")))
        coverage = ps.CoverageParameters()
        coverage.tolerance = 1e-3
        extra.append(coverage)
    elif model_id == "cf4o2":
        model = ps.CF4O2Etching(*(float(p[key]) for key in (
            "ionFlux", "etchantFlux", "oxygenFlux", "polymerFlux", "meanIonEnergy",
            "sigmaIonEnergy", "ionExponent", "oxySputterYield", "polySputterYield", "etchStopDepth")))
        coverage = ps.CoverageParameters()
        coverage.tolerance = 1e-3
        extra.append(coverage)
    elif model_id == "hbro2":
        model = ps.HBrO2Etching(*(float(p[key]) for key in (
            "ionFlux", "etchantFlux", "oxygenFlux", "meanIonEnergy", "sigmaIonEnergy",
            "ionExponent", "oxySputterYield", "etchStopDepth")))
        coverage = ps.CoverageParameters()
        coverage.tolerance = 1e-3
        extra.append(coverage)
    elif model_id == "sf6c4f8":
        model = ps.SF6C4F8Etching(*(float(p[key]) for key in (
            "ionFlux", "etchantFlux", "meanEnergy", "sigmaEnergy", "ionExponent", "etchStopDepth")))
        coverage = ps.CoverageParameters()
        coverage.tolerance = 1e-3
        extra.append(coverage)
    elif model_id == "fluorocarbon":
        parameters = ps.FluorocarbonParameters()
        for key in ("ionFlux", "etchantFlux", "polyFlux", "k_ie", "k_ev", "delta_p", "temperature", "etchStopDepth"):
            setattr(parameters, key, float(p[key]))
        ions = parameters.Ions
        for key in ("meanEnergy", "sigmaEnergy", "exponent", "n_l", "inflectAngle", "minAngle"):
            setattr(ions, key, float(p[key]))
        for name, values in sorted(p["materialParametersByMaterial"].items()):
            material = ps.Material.Polymer if name == "Polymer" else material_handle(name)
            row = ps.FluorocarbonMaterialParameters()
            row.id = material
            for key, value in values.items():
                setattr(row, key, float(value))
            parameters.addMaterial(row)
        model = ps.FluorocarbonEtching(parameters)
    elif model_id == "ionBeam":
        parameters = ps.IBEParameters()
        for key in ("meanEnergy", "sigmaEnergy", "thresholdEnergy", "thetaRMin", "thetaRMax",
                    "minAngle", "inflectAngle", "exponent", "n_l", "tiltAngle", "planeWaferRate",
                    "redepositionRate", "redepositionThreshold"):
            setattr(parameters, key, float(p[key]))
        for index, key in enumerate(("cos4YieldA1", "cos4YieldA2", "cos4YieldA3", "cos4YieldA4"), start=1):
            setattr(parameters.cos4Yield, f"a{index}", float(p[key]))
        for name, value in sorted(p["materialPlaneWaferRateByMaterial"].items()):
            parameters.materialPlaneWaferRate.set(material_handle(name), float(value))
        masks = [material_handle(name) for name in sorted(_mask_material_names(project))]
        model = ps.IonBeamEtching(parameters, masks)
    elif model_id == "wetEtch":
        material_rates = [(material_handle(name), float(value)) for name, value in sorted(p["materialRates"].items())]
        if p["useCrystallineRates"]:
            model = ps.WetEtching([float(v) for v in p["direction100"]], [float(v) for v in p["direction010"]],
                                  float(p["rate100"]), float(p["rate110"]), float(p["rate111"]),
                                  float(p["rate311"]), material_rates)
        else:
            model = ps.WetEtching(material_rates)
    elif model_id == "teosDeposition":
        model = ps.TEOSDeposition(*(float(p[key]) for key in (
            "stickingProbabilityP1", "rateP1", "orderP1", "stickingProbabilityP2", "rateP2", "orderP2")))
    elif model_id == "teospecvd":
        model = ps.TEOSPECVD(*(float(p[key]) for key in (
            "stickingProbabilityRadical", "depositionRateRadical", "depositionRateIon", "exponentIon",
            "stickingProbabilityIon", "reactionOrderRadical", "reactionOrderIon", "minAngleIon")))
    elif model_id == "ald":
        parameters = ps.SingleParticleALDParams()
        for key in ("stickingProbability", "gasMeanFreePath", "growthPerCycle", "incomingFlux",
                    "evaporationFlux", "s0", "coverageDiffusionCoefficient"):
            setattr(parameters, key, float(p[key]))
        model = ps.SingleParticleALD(parameters)
        atomic = ps.AtomicLayerProcessParameters()
        for key in ("pulseTime", "purgePulseTime", "coverageTimeStep", "purgeTimeStep"):
            setattr(atomic, key, float(p[key]))
        atomic.numCycles = int(p["numCycles"])
        extra.append(atomic)
    elif model_id == "oxidation":
        model = ps.Oxidation()
        model.setOxidant(getattr(ps.OxidantType, p["oxidant"]))
        model.setOrientation(getattr(ps.SiliconOrientation, p["orientation"]))
        model.setTemperature(float(p["temperatureC"]))
        model.setPressure(float(p["pressureAtm"]))
        model.setTime(float(p["timeHours"]))
        model.setTimeStep(float(p["timeStepHours"]))
        model.setTransferCoefficient(float(p["transferCoefficientUmHr"]))
        model.setInitialOxideThickness(float(p["initialOxideThicknessUm"]))
        model.setReactionActivationVolume(float(p["reactionActivationVolumeM3"]))
        model.setDiffusionActivationVolume(float(p["diffusionActivationVolumeM3"]))
        model.setSiliconMaterial(material_handle(p["siliconMaterial"]))
        model.setOxideMaterial(material_handle(p["oxideMaterial"]))
        if p["maskMaterial"]:
            model.setMaskMaterial(material_handle(p["maskMaterial"]))
        resolved["nativeParameters"] = {**p, "processDurationDerivation": "timeHours * 3600 seconds"}
    elif model_id == "oxideRegrowth":
        surface = domain.getSurfaceMesh(addInterfaces=True, sharpCorners=False, minNodeDistanceFactor=0.01)
        vertical_axis = 1 if int(project["dimension"]) == 2 else 2
        top = max(float(point[vertical_axis]) for point in surface.getNodes())
        padding = max(1.0, 5.0 * float(project["domain"]["gridDelta"]))
        cellset_height = top + padding
        domain.generateCellSet(cellset_height, ps.Material.GAS, True)
        cell_set = domain.getCellSet()
        if cell_set is None:
            raise RuntimeError("ViennaPS Domain.generateCellSet returned no DenseCellSet")
        cell_set.addScalarData("byproductSum", 0.0)
        cell_set.buildNeighborhood()
        model = ps.OxideRegrowth(*(float(p[key]) for key in (
            "nitrideEtchRate", "oxideEtchRate", "redepositionRate", "redepositionThreshold",
            "redepositionTimeInt", "diffusionCoefficient", "sinkStrength", "scallopVelocity",
            "centerVelocity", "topHeight", "centerWidth", "stabilityFactor")))
        resolved["derivedCellSet"] = {"nativeType": "DenseCellSet", "material": "GAS", "height": cellset_height,
                                      "verticalAxis": vertical_axis, "initialField": "byproductSum=0",
                                      "neighborhoodBuilt": True}
    elif model_id == "selectiveEpitaxy":
        # ViennaPS 4.6.2 requires the native epitaxy enum identity in this
        # overload; registry handles do not satisfy its top-material check.
        rates = [(getattr(ps.Material, name), float(value)) for name, value in sorted(p["materialRates"].items())]
        model = ps.SelectiveEpitaxy(rates, float(p["rate111"]), float(p["rate100"]))
    elif model_id == "neutralTransport":
        parameters = ps.NeutralTransportParameters()
        for key in ("incomingFlux", "sourceDistributionPower", "etchFrontSticking", "zeroCoverageSticking",
                    "coverageTimeStep", "desorptionRate", "surfaceDiffusionCoefficient", "surfaceSiteDensity"):
            setattr(parameters, key, float(p[key]))
        parameters.useSteadyStateCoverage = bool(p["useSteadyStateCoverage"])
        parameters.etchFrontMaterial = material_handle(p["etchFrontMaterial"])
        parameters.fluxLabel = str(p["fluxLabel"])
        parameters.coverageLabel = str(p["coverageLabel"])
        model = ps.NeutralTransport(parameters)
    else:
        raise RuntimeError(f"no native constructor is implemented for {model_id!r}")

    resolved["materialIdsByName"] = {name: material_ids.get(name) for name in _referenced_materials(p)}
    resolved["nativeProcessParameters"] = [type(item).__name__ for item in extra]
    return model, extra, resolved, duration


def _referenced_materials(parameters: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for key in ("siliconMaterial", "oxideMaterial", "maskMaterial", "etchFrontMaterial"):
        value = parameters.get(key)
        if isinstance(value, str) and value:
            result.add(value)
    for key in ("materialRates", "materialPlaneWaferRateByMaterial", "materialParametersByMaterial"):
        value = parameters.get(key)
        if isinstance(value, dict):
            result.update(str(name) for name in value if name != "Polymer")
    return result


def _mask_material_names(project: dict[str, Any]) -> set[str]:
    geometry = project.get("geometry", {})
    if geometry.get("kind") == "gds":
        return {str(row["material"]) for row in geometry.get("layers", []) if row.get("mask")}
    names: set[str] = set()
    if geometry.get("kind") == "stack":
        names.update(str(row["material"]) for row in geometry.get("layers", []) if row.get("role") == "mask")
    elif geometry.get("kind") in {"trench", "hole", "fin"} and float(geometry.get("maskHeight", 0.0)) > 0:
        names.add(str(geometry.get("maskMaterial", "Mask")))
    return names
