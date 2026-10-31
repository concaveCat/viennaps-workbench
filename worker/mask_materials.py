"""Material names that a native process must not move.

The derivation lives in ``backend.materials.geometry_mask_material_names`` so
the API validation and the worker share one implementation. This module keeps
the worker-facing name and the ``rateMode == "material"`` rule.
"""
from __future__ import annotations

from typing import Any

from backend.materials import geometry_mask_material_names


def protected_material_names(project: dict[str, Any], step: dict[str, Any] | None = None) -> list[str]:
    if project["schemaVersion"] in (4, 5, 6, 7) and step is not None and step.get("rateMode") == "material":
        return []
    return geometry_mask_material_names(project["geometry"])
