"""Shared guards for the ViennaPS 4.7.0 implant / anneal / analysis capability.

The native classes (``viennaps.IonImplantation``, ``Anneal``, ``NetDoping``,
``SheetResistance``) only exist in the 4.7.0 runtime and all of them operate on
the *cell set* stored on the Domain, not on the level-set surface. This module
holds the small, dependency-free rules that the API validator, the artifact
preflight and the worker must agree on:

* which step models belong to the cell-set capability;
* the fixed workbench length unit (um) expressed in centimetres, which the
  native implant / sheet-resistance models need for their cm-based physics;
* label provenance (which step produces which cell-set scalar label);
* the ordering rule that forbids a surface-moving step after a cell-set step;
* numeric guard rails that a hand-edited worker input can never bypass.

Nothing here imports ViennaPS, so it is safe in the API process.
"""
from __future__ import annotations

import math
import re
from typing import Any, Iterable

CELL_SET_STEP_MODELS = frozenset({"implant", "anneal", "analysis"})

#: Schema-3+ projects declare ``units.length = "um"``. The native implant and
#: sheet-resistance models take their length unit in centimetres.
LENGTH_UNIT_CM = 1e-4
LENGTH_UNIT_NAME = "um"

#: DenseCellSet field names the installed runtime always writes.
NATIVE_CELL_SET_LABELS = ("Material", "FillingFraction")

#: Upper bound for one step's native cell set. The generic cell-set output uses
#: the same hard construction limit; an implant grid above it is refused before
#: the native grid is allocated.
MAX_STEP_CELL_SET_CELLS = 2_000_000

LABEL_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,47}$")
SPECIES_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,23}$")


def step_value(step: Any, key: str, default: Any = None) -> Any:
    return step.get(key, default) if isinstance(step, dict) else getattr(step, key, default)


def is_cell_set_step(step: Any) -> bool:
    return step_value(step, "model") in CELL_SET_STEP_MODELS


def step_cell_set(step: Any) -> dict[str, Any] | None:
    settings = step_value(step, "cellSet")
    return settings if isinstance(settings, dict) else None


def valid_label(name: Any) -> bool:
    return isinstance(name, str) and bool(LABEL_PATTERN.fullmatch(name))


def produced_labels(step: Any) -> set[str]:
    """Return the cell-set scalar labels a step writes when it runs.

    Only labels proven by the installed 4.7.0 bindings are listed. A damage
    label is written only when a damage model is configured, and an active
    label only by a solid-activation anneal.
    """
    model = step_value(step, "model")
    labels: set[str] = set()
    if model == "implant":
        resolved = step_value(step, "labels")
        if isinstance(resolved, dict):
            if resolved.get("concentration"):
                labels.add(str(resolved["concentration"]))
            if step_value(step, "damage") is not None:
                if resolved.get("damage"):
                    labels.add(str(resolved["damage"]))
                if resolved.get("lastDamage"):
                    labels.add(str(resolved["lastDamage"]))
    elif model == "anneal":
        active = step_value(step, "activeLabel")
        if isinstance(active, str) and active:
            labels.add(active)
    elif model == "analysis":
        net = step_value(step, "netDoping")
        if isinstance(net, dict) and net.get("outputLabel"):
            labels.add(str(net["outputLabel"]))
    return labels


def consumed_labels(step: Any) -> set[str]:
    """Return the cell-set scalar labels a step reads."""
    model = step_value(step, "model")
    labels: set[str] = set()
    if model == "anneal":
        species = step_value(step, "speciesLabel")
        if isinstance(species, str) and species:
            labels.add(species)
    elif model == "analysis":
        net = step_value(step, "netDoping")
        if isinstance(net, dict):
            labels.update(str(name) for name in (net.get("donorLabels") or []))
            labels.update(str(name) for name in (net.get("acceptorLabels") or []))
        for row in step_value(step, "sheetResistance") or []:
            name = row.get("concentrationLabel") if isinstance(row, dict) else None
            if isinstance(name, str) and name:
                labels.add(name)
    return labels


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def numeric_guard_messages(step: Any, *, dimension: int, grid_delta: float) -> list[str]:
    """Guard rails shared by the API validator and the worker.

    The Pydantic models already reject non-finite numbers and out-of-range
    values; this function repeats the checks a hand-edited worker input could
    otherwise skip before reaching a native constructor.
    """
    messages: list[str] = []
    model = step_value(step, "model")
    if model not in CELL_SET_STEP_MODELS:
        return messages
    settings = step_cell_set(step)
    if not isinstance(settings, dict):
        messages.append("cellSet block is required")
        return messages
    height = settings.get("height")
    if not _finite(height) or float(height) <= 0.0:
        messages.append("cellSet.height must be a finite positive number")
    elif float(height) + 1e-12 < float(grid_delta):
        messages.append(
            f"cellSet.height {float(height):g} um is below one grid interval ({float(grid_delta):g} um); "
            "the installed DenseCellSet generator would crash")
    if dimension not in (2, 3):
        messages.append("cell-set steps require a 2D or 3D project")
    if settings.get("periodic") and dimension == 3:
        # The native periodic cell-set boundary is flag-per-axis and the 3D
        # Domain boundary is applied to x and y; z stays non-periodic.
        pass
    if model == "implant":
        messages.extend(_implant_guard_messages(step))
    elif model == "anneal":
        messages.extend(_anneal_guard_messages(step))
    elif model == "analysis":
        messages.extend(_analysis_guard_messages(step))
    return messages


def _implant_guard_messages(step: Any) -> list[str]:
    messages: list[str] = []
    profile = step_value(step, "profile")
    if not isinstance(profile, dict):
        messages.append("profile block is required")
        return messages
    if profile.get("source") != "manual":
        messages.append("only modelSource=manual is supported")
        return messages
    kind = profile.get("kind")
    if kind not in {"pearsonIV", "dualPearsonIV", "pearsonIVChanneling"}:
        messages.append(f"unsupported manual profile kind {kind!r}")
        return messages
    for name, side in (("head", profile.get("head")), ("tail", profile.get("tail"))):
        if side is None:
            if name == "head" or kind == "dualPearsonIV":
                messages.append(f"profile.{name} moments are required for {kind}")
            continue
        if not isinstance(side, dict):
            messages.append(f"profile.{name} must be an object")
            continue
        sigma = side.get("sigma")
        if not _finite(sigma) or float(sigma) <= 0.0:
            messages.append(f"profile.{name}.sigma must be a finite positive number")
    for key in ("lateralSigma", "headLateralSigma", "tailLateralSigma"):
        value = profile.get(key)
        if value is not None and (not _finite(value) or float(value) <= 0.0):
            messages.append(f"profile.{key} must be a finite positive number")
    if kind == "dualPearsonIV":
        fraction = profile.get("headFraction")
        if not _finite(fraction) or not 0.0 <= float(fraction) <= 1.0:
            messages.append("profile.headFraction must be within [0, 1]")
    if kind == "pearsonIVChanneling":
        for key, minimum in (("tailFraction", 0.0), ("tailStartDepth", 0.0),
                             ("tailDecayLength", 0.0), ("tailBlendWidth", 0.0)):
            value = profile.get(key)
            if value is None:
                messages.append(f"profile.{key} is required for pearsonIVChanneling")
            elif not _finite(value) or float(value) < minimum:
                messages.append(f"profile.{key} must be a finite non-negative number")
    dose = step_value(step, "doseCm2")
    if not _finite(dose) or float(dose) <= 0.0:
        messages.append("doseCm2 must be a finite positive number")
    tilt = step_value(step, "tiltDeg")
    if not _finite(tilt) or not 0.0 <= float(tilt) <= 90.0:
        messages.append("tiltDeg must be within [0, 90]")
    return messages


def _anneal_guard_messages(step: Any) -> list[str]:
    messages: list[str] = []
    schedule = step_value(step, "schedule")
    if not isinstance(schedule, dict):
        messages.append("schedule block is required")
        return messages
    kind = schedule.get("kind")
    if kind == "isothermal":
        temperature = schedule.get("temperatureK")
        duration = schedule.get("durationSeconds")
        if not _finite(temperature) or float(temperature) <= 0.0:
            messages.append("schedule.temperatureK must be positive")
        if not _finite(duration) or float(duration) < 0.0:
            messages.append("schedule.durationSeconds must be non-negative")
    elif kind == "schedule":
        durations = schedule.get("durations") or []
        temperatures = schedule.get("temperatures") or []
        if len(temperatures) not in (len(durations), len(durations) + 1) or not durations:
            messages.append("schedule temperatures must contain N (isothermal) or N+1 (ramp) values")
        for value in durations:
            if not _finite(value) or float(value) < 0.0:
                messages.append("schedule.durations must be finite and non-negative")
                break
        for value in temperatures:
            if not _finite(value) or float(value) <= 0.0:
                messages.append("schedule.temperatures must be finite and positive")
                break
    else:
        messages.append(f"unsupported schedule kind {kind!r}")
    diffusivity = step_value(step, "diffusivity")
    if not isinstance(diffusivity, dict):
        messages.append("diffusivity block is required")
    elif diffusivity.get("kind") == "arrhenius":
        if not _finite(diffusivity.get("D0")) or float(diffusivity["D0"]) <= 0.0:
            messages.append("diffusivity.D0 must be positive")
        if not _finite(diffusivity.get("Ea_eV")) or float(diffusivity["Ea_eV"]) < 0.0:
            messages.append("diffusivity.Ea_eV must be non-negative")
    elif diffusivity.get("kind") == "constant":
        if not _finite(diffusivity.get("value")) or float(diffusivity["value"]) < 0.0:
            messages.append("diffusivity.value must be non-negative")
    else:
        messages.append("diffusivity.kind must be arrhenius or constant")
    activation = step_value(step, "solidActivation")
    if not isinstance(activation, dict):
        messages.append("solidActivation block is required")
    else:
        if not _finite(activation.get("solubilityC0")) or float(activation["solubilityC0"]) <= 0.0:
            messages.append("solidActivation.solubilityC0 must be positive")
        if not _finite(activation.get("solubilityEa_eV")) or float(activation["solubilityEa_eV"]) < 0.0:
            messages.append("solidActivation.solubilityEa_eV must be non-negative")
    return messages


def _analysis_guard_messages(step: Any) -> list[str]:
    messages: list[str] = []
    net = step_value(step, "netDoping")
    sheet = step_value(step, "sheetResistance") or []
    if net is None and not sheet:
        messages.append("analysis requires netDoping or at least one sheetResistance entry")
    if isinstance(net, dict):
        output = net.get("outputLabel")
        if not valid_label(output):
            messages.append("netDoping.outputLabel must be a valid cell-set label")
        if not (net.get("donorLabels") or net.get("acceptorLabels")):
            messages.append("netDoping requires at least one donor or acceptor label")
    for index, row in enumerate(sheet):
        if not isinstance(row, dict):
            messages.append(f"sheetResistance[{index}] must be an object")
            continue
        if not valid_label(row.get("concentrationLabel")):
            messages.append(f"sheetResistance[{index}].concentrationLabel must be a valid cell-set label")
        if row.get("carrier") not in {"electron", "hole"}:
            messages.append(f"sheetResistance[{index}].carrier must be electron or hole")
    return messages


def ordering_issues(steps: Iterable[Any]) -> list[tuple[int, str]]:
    """Return ``(step_index, message)`` for illegal step ordering.

    A cell-set field step operates on the cell grid generated from the level
    sets at that moment. Any later step that moves the surface would leave the
    grid (and every dopant field) stale, so the request is refused instead of
    reporting a field that no longer matches the geometry.
    """
    issues: list[tuple[int, str]] = []
    seen_cell_set = False
    for index, step in enumerate(steps):
        if not step_value(step, "enabled"):
            continue
        model = step_value(step, "model")
        if model in CELL_SET_STEP_MODELS:
            seen_cell_set = True
            continue
        if seen_cell_set:
            issues.append((
                index,
                "a surface-moving step cannot follow a cell-set field step "
                "(implant/anneal/analysis); the native DenseCellSet would no longer "
                "match the moved level sets",
            ))
    return issues


def label_provenance_issues(steps: Iterable[Any]) -> list[tuple[int, str]]:
    """Check every consumed cell-set label is produced earlier in the run."""
    issues: list[tuple[int, str]] = []
    available: set[str] = set(NATIVE_CELL_SET_LABELS)
    seen_field_step = False
    for index, step in enumerate(steps):
        if not step_value(step, "enabled"):
            continue
        model = step_value(step, "model")
        if model in CELL_SET_STEP_MODELS:
            if model == "analysis" and not seen_field_step:
                issues.append((index, "analysis requires an earlier implant or anneal step that creates the dopant field"))
            missing = sorted(consumed_labels(step) - available)
            if missing:
                issues.append((index, "cell-set label(s) not produced by an earlier step: " + ", ".join(missing)))
            available |= produced_labels(step)
            seen_field_step = True
    return issues


def cell_set_output_conflict(project: Any) -> str | None:
    """Reject the generic cell-set output next to a cell-set field step.

    ``outputSettings.cellSet`` builds a *fresh* grid from the level sets, so it
    cannot contain the implant fields. Offering both would silently produce a
    field-less cell set next to the real one; the step's own output is the only
    source of truth.
    """
    steps = project.get("steps") if isinstance(project, dict) else getattr(project, "steps", [])
    if not any(step_value(step, "enabled") and is_cell_set_step(step) for step in steps):
        return None
    settings = project.get("outputSettings") if isinstance(project, dict) else getattr(project, "outputSettings", None)
    if settings is None:
        return None
    cell_set = settings.get("cellSet") if isinstance(settings, dict) else getattr(settings, "cellSet", None)
    enabled = cell_set.get("enabled") if isinstance(cell_set, dict) else getattr(cell_set, "enabled", False)
    if enabled:
        return ("outputSettings.cellSet is not available together with implant/anneal/analysis steps; "
                "each cell-set step writes its own native DenseCellSet with the dopant fields")
    return None
