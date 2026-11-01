"""Native isotropic etch/deposit plugin for the v2 contract.

The plugin is a thin, *parity-first* wrapper around the existing worker path
(:mod:`worker.steps.process` and the helpers in :mod:`worker.simulate`). It
does not re-derive physics: it builds the same synthetic step dictionary the
worker would see, calls the same rate/advection helpers, and constructs the
same ``ps.IsotropicProcess``. The only new part is the v2 packaging:

* parameters are validated by :class:`IsotropicParams` (Pydantic v2);
* the implementation is registered as a variant bound to a
  :class:`~process_models.v2.engine.EngineRequirement` (native ``viennaps``);
* execution returns a :class:`~process_models.v2.state.StateDelta` instead of
  a bare native domain.

The variant mutates the caller's level-set handle in place
(``mutates_input=True``), exactly like the worker path. The executor is
responsible for checkpointing before calling it.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..contract import (
    EngineVariant,
    Issue,
    ProcessContext,
    ProcessSpec,
    VariantCapabilities,
    VariantRegistration,
)
from ..engine import EngineRequirement
from ..state import LevelSetState, ProcessState, StateDelta

__all__ = [
    "IsotropicNativeVariant",
    "IsotropicParams",
    "IsotropicProcessSpec",
    "IsotropicSpec",
    "register_isotropic",
]

Operation = Literal["etch", "deposit"]
RateMode = Literal["uniform", "material"]

#: Worker-shaped project defaults used when the caller does not supply a project.
#: Schema 3 keeps ``configure_advection`` on its legacy branch (solver ratio
#: only) and makes ``protected_material_names`` derive masks from ``geometry``.
_DEFAULT_PROJECT: dict[str, Any] = {
    "schemaVersion": 3,
    "geometry": {"kind": "plane", "substrateMaterial": "Si"},
    "solver": {"timeStepRatio": 0.25},
}


class IsotropicParams(BaseModel):
    """Parameters of one isotropic etch/deposit step.

    ``rateMagnitude`` is the uniform (default) rate. In ``rateMode="material"``
    the rate table is authoritative and unlisted materials do not move, which
    mirrors the worker's material-mode convention of a zero default rate.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: Operation
    rateMagnitude: float = Field(gt=0)
    duration: float = Field(gt=0)
    depositMaterial: str = "SiO2"
    rateMode: RateMode = "uniform"
    materialRates: dict[str, float] = Field(default_factory=dict)
    maskMaterials: list[str] = Field(default_factory=list)
    isotropicTimeStepRatio: float | None = Field(default=None, ge=0.05, le=0.5)

    @field_validator("rateMagnitude", "duration")
    @classmethod
    def _finite_positive(cls, value: float, info: Any) -> float:
        if not math.isfinite(value):
            raise ValueError(f"{info.field_name} must be finite")
        return value

    @field_validator("materialRates")
    @classmethod
    def _finite_material_rates(cls, value: dict[str, float]) -> dict[str, float]:
        for name, rate in value.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("materialRates keys must be non-empty material names")
            if not math.isfinite(rate):
                raise ValueError(f"materialRates[{name!r}] must be finite")
            if rate < 0.0:
                raise ValueError(f"materialRates[{name!r}] must be non-negative")
        return value

    @field_validator("maskMaterials")
    @classmethod
    def _non_empty_mask_names(cls, value: list[str]) -> list[str]:
        for name in value:
            if not isinstance(name, str) or not name.strip():
                raise ValueError("maskMaterials entries must be non-empty material names")
        return value


class IsotropicNativeVariant(EngineVariant):
    """Run a native ``ps.IsotropicProcess`` step through the worker helpers.

    The variant reads and writes only native geometry and declares
    ``mutates_input=True`` because the process advects the caller's domain in
    place.
    """

    variant_id = "native"
    version = "1.0.0"
    capabilities = VariantCapabilities(
        dimensions=(2, 3),
        reads=frozenset({"geometry"}),
        writes=frozenset({"geometry"}),
        supports_masks=True,
        mutates_input=True,
        determinism="strict",
    )
    engine = EngineRequirement(kind="native", engine_id="viennaps", min_version="4.6.2")
    implementation_hash: str | None = None

    def validate(self, ctx: ProcessContext, params: BaseModel) -> list[Issue]:
        """Return cheap, deterministic issues for ``params`` in ``ctx``."""
        issues: list[Issue] = []
        if ctx.dimension not in self.capabilities.dimensions:
            issues.append(
                Issue(
                    path="dimension",
                    message=(
                        f"isotropic supports dimensions {list(self.capabilities.dimensions)}, not {ctx.dimension}"
                    ),
                )
            )

        rate_mode = str(getattr(params, "rateMode", "uniform"))
        operation = str(getattr(params, "operation", "etch"))
        material_rates = dict(getattr(params, "materialRates", {}) or {})
        mask_materials = list(getattr(params, "maskMaterials", []) or [])

        if rate_mode == "material":
            if not material_rates:
                issues.append(
                    Issue(
                        path="materialRates",
                        message="rateMode 'material' requires a non-empty material rate table",
                    )
                )
            if mask_materials:
                # The worker uses no geometry masks in material mode: the table
                # alone decides which materials move.
                issues.append(
                    Issue(
                        path="maskMaterials",
                        message=(
                            "rateMode 'material' ignores maskMaterials; "
                            "leave unlisted materials out of materialRates instead"
                        ),
                        severity="warning",
                    )
                )
        elif material_rates:
            issues.append(
                Issue(
                    path="materialRates",
                    message="rateMode 'uniform' forbids materialRates",
                )
            )

        if operation == "deposit":
            deposit_material = str(getattr(params, "depositMaterial", "") or "")
            if not deposit_material.strip():
                issues.append(
                    Issue(
                        path="depositMaterial",
                        message="deposit steps require a non-empty depositMaterial",
                    )
                )

        rate_magnitude = float(getattr(params, "rateMagnitude", 0.0))
        if not math.isfinite(rate_magnitude) or rate_magnitude <= 0.0:
            issues.append(
                Issue(
                    path="rateMagnitude",
                    message=f"rateMagnitude must be a finite positive number, got {rate_magnitude!r}",
                )
            )
        duration = float(getattr(params, "duration", 0.0))
        if not math.isfinite(duration) or duration <= 0.0:
            issues.append(
                Issue(
                    path="duration",
                    message=f"duration must be a finite positive number, got {duration!r}",
                )
            )

        for name, rate in material_rates.items():
            if not math.isfinite(rate) or rate < 0.0:
                issues.append(
                    Issue(
                        path=f"materialRates.{name}",
                        message=f"materialRates[{name!r}] must be finite and non-negative, got {rate!r}",
                    )
                )

        for name in mask_materials:
            if not str(name).strip():
                issues.append(
                    Issue(path="maskMaterials", message="maskMaterials entries must be non-empty material names")
                )

        return issues

    def execute(self, state: ProcessState, params: BaseModel, ctx: ProcessContext) -> StateDelta:
        """Apply one isotropic step and return the mutated native geometry.

        The worker module is imported lazily so that registering the plugin
        never imports the worker or native libraries.
        """
        import worker.simulate as simulate

        geometry = state.require("geometry")
        lease = state.engine
        if lease is None:
            raise RuntimeError("isotropic native variant requires an engine lease on the process state")
        ps = lease.handle
        domain = geometry.handle

        operation = str(getattr(params, "operation", "etch"))
        rate_mode = str(getattr(params, "rateMode", "uniform"))
        material_rates = dict(getattr(params, "materialRates", {}) or {})
        duration = float(getattr(params, "duration", 0.0))
        deposit_material = str(getattr(params, "depositMaterial", "SiO2"))
        time_step_ratio = getattr(params, "isotropicTimeStepRatio", None)

        project = _worker_project(ctx)
        step = {
            "model": "isotropic",
            "operation": operation,
            "rateMagnitude": float(getattr(params, "rateMagnitude", 0.0)),
            "duration": duration,
            "depositMaterial": deposit_material,
            "rateMode": rate_mode,
            "materialRates": material_rates,
            "isotropicTimeStepRatio": time_step_ratio,
        }

        if operation == "deposit":
            domain.duplicateTopLevelSet(simulate.material_handle(ps, deposit_material))

        rate, signed_material_rates = simulate.signed_rates(step)
        warnings: list[str] = []

        if rate_mode == "material":
            # The rate table is authoritative. The worker's validated material
            # mode uses rateMagnitude=0, so unlisted materials (for example a
            # mask) must not inherit the uniform fallback rate.
            native_default_rate = 0.0
            mask_names: list[str] = []
            if getattr(params, "maskMaterials", None):
                warnings.append("rateMode 'material' ignores maskMaterials; the material rate table is authoritative")
            model = ps.IsotropicProcess(rate=native_default_rate, maskMaterials=[])
            for name, signed_rate in signed_material_rates.items():
                model.setMaterialRate(simulate.material_handle(ps, name), signed_rate)
        else:
            native_default_rate = rate
            explicit_masks = list(getattr(params, "maskMaterials", None) or [])
            mask_names = explicit_masks or simulate.protected_material_names(project, step)
            model = ps.IsotropicProcess(
                rate=rate,
                maskMaterials=[simulate.material_handle(ps, name) for name in mask_names],
            )

        process = ps.Process(domain, model, duration)
        process.setFluxEngineType(ps.FluxEngineType.CPU_DISK)
        advection, advection_actual = simulate.configure_advection(
            ps,
            project,
            time_step_ratio=time_step_ratio,
        )
        process.setParameters(advection)
        process.apply()

        material_order = tuple(int(material_id) for material_id in simulate.material_map(domain))
        delta_geometry = LevelSetState(
            handle=domain,
            dimension=geometry.dimension,
            grid_delta=geometry.grid_delta,
            material_order=material_order,
        )

        metadata: dict[str, Any] = {
            "operation": operation,
            "rateMode": rate_mode,
            "requestedRate": float(getattr(params, "rateMagnitude", 0.0)),
            "actualRate": abs(rate),
            "signedRate": rate,
            "nativeDefaultRate": native_default_rate,
            "duration": duration,
            "depositMaterial": deposit_material if operation == "deposit" else None,
            "materialRates": material_rates,
            "nativeMaterialRates": signed_material_rates,
            "maskMaterials": mask_names,
            "timeStepRatio": float(advection.timeStepRatio),
            "timeStepRatioSource": advection_actual.get(
                "timeStepRatioSource",
                "solver.timeStepRatio",
            ),
            "nativeClass": "ps.IsotropicProcess",
            "fluxEngineType": "CPU_DISK",
            "dimension": geometry.dimension,
            "gridDelta": geometry.grid_delta,
            "levelSetCount": int(domain.getNumberOfLevelSets()),
            "materialMap": list(material_order),
        }

        return StateDelta(geometry=delta_geometry, metadata=metadata, warnings=warnings)


IsotropicProcessSpec = ProcessSpec(
    id="isotropic",
    version="1.0.0",
    label="Isotropic etch/deposit (native)",
    params_model=IsotropicParams,
    dimensions=(2, 3),
    operations=("etch", "deposit"),
    description=(
        "Native ViennaPS IsotropicProcess driven through the worker rate and "
        "advection helpers; parity with worker/steps/process.py."
    ),
)

#: Alias matching the short name used in the v2 spec.
IsotropicSpec = IsotropicProcessSpec


def register_isotropic(registry: Any) -> VariantRegistration:
    """Register the isotropic spec and its single native variant.

    Returns the registration so callers can inspect the resolved identity
    without resolving again.
    """
    from ..registry import compute_implementation_hash

    variant = IsotropicNativeVariant()
    registration = VariantRegistration(
        process_id=IsotropicProcessSpec.id,
        variant_id=variant.variant_id,
        version=variant.version,
        variant=variant,
        capabilities=variant.capabilities,
        engine=variant.engine,
        implementation_hash=variant.implementation_hash or compute_implementation_hash(variant),
    )
    registry.register_process(IsotropicProcessSpec)
    registry.register_variant(registration)
    return registration


def _worker_project(ctx: ProcessContext) -> dict[str, Any]:
    """Return a worker-shaped project mapping for the shared helpers.

    ``ProcessContext.project`` may be empty when a caller resolves a step
    outside a saved project. The helpers only need ``schemaVersion``,
    ``geometry`` (mask derivation), and ``solver.timeStepRatio``, so missing
    values fall back to :data:`_DEFAULT_PROJECT` without mutating the context.
    """
    project = dict(ctx.project or {})
    project.setdefault("schemaVersion", _DEFAULT_PROJECT["schemaVersion"])
    project.setdefault("geometry", dict(_DEFAULT_PROJECT["geometry"]))
    solver = dict(project.get("solver") or {})
    solver.setdefault("timeStepRatio", _DEFAULT_PROJECT["solver"]["timeStepRatio"])
    project["solver"] = solver
    return project
