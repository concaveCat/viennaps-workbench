"""Worker step adapter for the native isotropic plugin.

A raw worker step is untyped JSON: it carries ``model``, ``operation``,
``rateMode``, and the dispersion fields next to the rate parameters.
:func:`parse_step` is the plugin's declared
:class:`~process_models.v2.contract.ProcessSpec` hook: it returns
:class:`~plugins.isotropic.params.IsotropicParams` when a step belongs to this
process and ``None`` when it does not. Malformed fields raise ``ValueError``.

Only uniform level-set isotropic steps are covered. ``rateMode="material"``
uses the worker's material-rate table conventions, and ``isotropicMethod``
``auto``/``geometric`` dispatches to a different native process; mapping them
here would silently run a different simulation, so they return ``None`` and
the caller keeps its own path.
"""

from __future__ import annotations

from typing import Any, Mapping

from .params import IsotropicParams

__all__ = ["parse_step"]

_OPERATIONS = ("etch", "deposit")
_SUPPORTED_METHODS = (None, "levelSet")


def parse_step(project: Mapping[str, Any], step: Mapping[str, Any]) -> IsotropicParams | None:
    """Return validated params when ``step`` is a uniform level-set step.

    ``project`` is accepted for the common ``(project, step)`` hook signature;
    nothing in the project switches dispatch today. ``None`` means the step is
    not this process (unsupported ``model``/``operation``/``rateMode``/
    ``isotropicMethod``). A step that claims to be isotropic but lacks required
    fields raises ``ValueError`` instead of being silently skipped.
    """
    del project
    if step.get("model") != "isotropic":
        return None
    if step.get("operation") not in _OPERATIONS:
        return None
    if str(step.get("rateMode", "uniform")) != "uniform":
        return None
    if step.get("isotropicMethod") not in _SUPPORTED_METHODS:
        return None

    rate_magnitude = step.get("rateMagnitude")
    duration = step.get("duration")
    if rate_magnitude is None or duration is None:
        raise ValueError("isotropic step requires rateMagnitude and duration")

    values: dict[str, Any] = {
        "operation": step["operation"],
        "rateMagnitude": float(rate_magnitude),
        "duration": float(duration),
        "depositMaterial": str(step.get("depositMaterial") or "SiO2"),
        "rateMode": "uniform",
        "materialRates": dict(step.get("materialRates") or {}),
        # The plugin derives geometry masks from the project; an explicit
        # worker list here would diverge from the native path.
        "maskMaterials": [],
    }
    ratio = step.get("isotropicTimeStepRatio")
    if ratio is not None:
        values["isotropicTimeStepRatio"] = float(ratio)
    return IsotropicParams(**values)
