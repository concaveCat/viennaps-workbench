"""Independent ViennaPS reference for schema-7 ``particleShape`` steps.

Always run this fixture inside scripts/bounded_child.py. It never imports
workbench worker code: it builds the domain from the submitted project, applies
the native ``BoxDistribution`` / ``SphereDistribution`` to every enabled
``particleShape`` step in order and records one surface snapshot per step.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import viennaps as ps


def material(name: str):
    registry = ps.MaterialRegistry.instance()
    if registry.findMaterial(name) is None:
        raise RuntimeError(f"material {name!r} is not registered")
    return registry.getMaterial(name)


def build_domain(project):
    ps.setDimension(project["dimension"])
    ps.setNumThreads(project["solver"]["cpuThreads"])
    delta = float(project["domain"]["gridDelta"])
    boundary = ps.BoundaryType.REFLECTIVE_BOUNDARY if project["domain"].get(
        "xBoundary", project["domain"].get("boundaryXY")) == "reflective" else ps.BoundaryType.PERIODIC_BOUNDARY
    if project["dimension"] == 2:
        domain = ps.Domain(delta, float(project["domain"]["xExtent"]), boundary)
    else:
        domain = ps.Domain(delta, float(project["domain"]["xExtent"]), float(project["domain"]["yExtent"]), boundary)
    geometry = project["geometry"]
    if geometry["kind"] == "plane":
        ps.MakePlane(domain, height=float(geometry["height"]), material=material(geometry["substrateMaterial"]),
                     addToExisting=False).apply()
    elif geometry["kind"] == "trench":
        ps.MakeTrench(domain=domain, trenchWidth=float(geometry["width"]), trenchDepth=float(geometry["depth"]),
                      trenchTaperAngle=float(geometry["taperAngle"]), maskHeight=float(geometry["maskHeight"]),
                      maskTaperAngle=float(geometry["maskTaperAngle"]), halfTrench=False,
                      material=material(geometry["substrateMaterial"]),
                      maskMaterial=material(geometry.get("maskMaterial", "Mask"))).apply()
    else:
        raise ValueError("reference fixture supports plane and trench inputs")
    return domain


def material_map(domain):
    mapping = domain.getMaterialMap()
    return [int(mapping.getMaterialIdAtIdx(index)) for index in range(mapping.size())]


def snapshot(domain, step_id, dimension):
    mesh = domain.getSurfaceMesh(addInterfaces=True, sharpCorners=False, minNodeDistanceFactor=0.01)
    points = [[float(value) for value in point] for point in mesh.getNodes()]
    cells_raw = mesh.getLines() if dimension == 2 else mesh.getTriangles()
    cells = [[int(value) for value in cell] for cell in cells_raw]
    data = mesh.getCellData()
    material_ids = []
    for index in range(data.getScalarDataSize()):
        if str(data.getScalarDataLabel(index)) == "MaterialIds":
            material_ids = [int(value) for value in data.getScalarData(index)]
    return {"stepId": step_id, "points": points, "cells": cells,
            "materialIds": material_ids, "materialMap": material_map(domain)}


def apply_step(domain, project, step):
    dimension = int(project["dimension"])
    sign = -1.0 if step["operation"] == "etch" else 1.0
    if step["shape"] == "box":
        axes = [sign * float(value) for value in step["halfAxes"]]
        model = ps.BoxDistribution(axes + [0.0] if dimension == 2 else axes)
    else:
        model = ps.SphereDistribution(sign * float(step["radius"]))
    ps.Process(domain, model, 0.0).apply()


def main() -> None:
    project = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    target = Path(sys.argv[2])
    dimension = int(project["dimension"])
    domain = build_domain(project)
    reference = {"initial": snapshot(domain, "initial", dimension)}
    for step in project["steps"]:
        if not step.get("enabled"):
            continue
        if step.get("model") != "particleShape":
            raise ValueError(f"reference fixture only supports particleShape steps, got {step.get('model')!r}")
        apply_step(domain, project, step)
        reference[step["id"]] = snapshot(domain, step["id"], dimension)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(reference, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"steps": sorted(key for key in reference if key != "initial")}))


if __name__ == "__main__":
    main()
