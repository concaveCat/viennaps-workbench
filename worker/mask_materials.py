"""Material names that a native process must not move.

This is the single implementation shared by the particle/directional paths and
the CSV rate-grid path. It covers the built-in trench/hole mask, stack mask
layers and the legacy stack ``gdsMask`` overlay (only when it protects etch),
plus GDS geometry layers explicitly marked as masks.
"""
from __future__ import annotations

from typing import Any


def protected_material_names(project: dict[str, Any], step: dict[str, Any] | None = None) -> list[str]:
    if project["schemaVersion"] in (4, 5, 6, 7) and step is not None and step.get("rateMode") == "material":
        return []
    geometry = project["geometry"]
    if geometry["kind"] in ("trench", "hole") and float(geometry.get("maskHeight", 0)) > 0:
        return [geometry.get("maskMaterial", "Mask")]
    if geometry["kind"] == "stack":
        values = [layer["material"] for layer in geometry["layers"] if layer["role"] == "mask"]
        if geometry.get("gdsMask", {}).get("protectsEtch"):
            values.append(geometry["gdsMask"]["material"])
        return list(dict.fromkeys(values))
    if geometry["kind"] == "gds":
        return list(dict.fromkeys(layer["material"] for layer in geometry["layers"] if layer.get("mask", False)))
    return []
