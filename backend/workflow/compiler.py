"""Deterministic P0 compiler from a workflow flow to the frozen execution schema."""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from ..models import ProjectV7, StepV7, View, View3DV3
from .hashing import compile_record_id, full_project_hash, source_map_hash
from .ids import STEP_ID_POLICY, sha256_hex, stable_step_id
from .models import (
    CompiledWorkflowV1,
    CompileRecordV1,
    FlowRevisionV1,
    GeometryBindingV1,
    ParameterProvenanceV1,
    SourceMapEntryV1,
)


COMPILER_VERSION = "0.1.0"


def _template_project(flow: FlowRevisionV1, binding: GeometryBindingV1) -> ProjectV7:
    """Validate the geometry binding by building an empty V7 execution project.

    V7 is the single execution target for both 2D and 3D. V6 remains available
    as an explicit legacy target, but this compiler never silently downgrades.
    """

    if binding.dimension == 3:
        return ProjectV7(
            schemaVersion=7,
            name=flow.name,
            dimension=3,
            units=binding.units,
            domain=binding.domain,
            geometry=binding.geometry,
            steps=[],
            solver=flow.solver,
            view3D=View3DV3(),
            materialDefinitions=binding.materialDefinitions,
        )
    return ProjectV7(
        schemaVersion=7,
        name=flow.name,
        dimension=2,
        units=binding.units,
        domain=binding.domain,
        geometry=binding.geometry,
        steps=[],
        solver=flow.solver,
        view=View(panX=0.0, panH=0.0, zoom=1.0, showGrid=True),
        materialDefinitions=binding.materialDefinitions,
    )


def _compiled_steps(
    flow: FlowRevisionV1,
    placements: dict[str, tuple[str, str]] | None = None,
) -> tuple[list[Any], list[SourceMapEntryV1]]:
    adapter = TypeAdapter(StepV7)
    compiled: list[Any] = []
    source_map: list[SourceMapEntryV1] = []
    placements = placements or {}

    for step in flow.steps:
        module_path, iteration_key = placements.get(step.id, ("root", "i0"))
        step_id = stable_step_id(
            flow_id=flow.flowId,
            module_instance_id_path=module_path,
            authored_step_id=step.id,
            iteration_key=iteration_key,
        )
        payload = step.model_dump(mode="json", exclude_none=True)
        payload["id"] = step_id
        compiled_step = adapter.validate_python(payload)
        compiled.append(compiled_step)
        source_map.append(
            SourceMapEntryV1(
                stepId=step_id,
                flowPath=f"{module_path}/{step.id}",
                authoredStepId=step.id,
                moduleInstanceIdPath=module_path,
                iterationKey=iteration_key,
                parameterProvenance={
                    key: ParameterProvenanceV1(kind="literal", resolvedValue=value)
                    for key, value in payload.items()
                    if key not in {"id", "name", "enabled"}
                },
            )
        )
    return compiled, source_map


def compile_flow(
    flow: FlowRevisionV1,
    binding: GeometryBindingV1,
    *,
    compiler_version: str = COMPILER_VERSION,
    engine_versions: dict[str, str] | None = None,
    placements: dict[str, tuple[str, str]] | None = None,
    variable_set: list[dict[str, Any]] | None = None,
    overrides: list[dict[str, Any]] | None = None,
) -> CompiledWorkflowV1:
    """Compile a literal-step flow and a geometry binding into ProjectV7.

    Inputs are deep-copied before use. A later edit to ``flow`` or ``binding``
    must not mutate an already returned compile result or invalidate its hashes.

    ``placements`` is the P4-1 extension: it maps an authored step id to the
    module instance path and iteration key produced by module/loop expansion.
    Omitting it keeps the P0 behavior (root instance, first iteration).
    ``variable_set``/``overrides`` are provenance only; they never change step
    ids.
    """

    flow = flow.model_copy(deep=True)
    binding = binding.model_copy(deep=True)

    template = _template_project(flow, binding)
    normalized_geometry = dict(template.geometry)
    steps, source_map = _compiled_steps(flow, placements)

    if binding.dimension == 3:
        project = ProjectV7(
            schemaVersion=7,
            name=flow.name,
            dimension=3,
            units=binding.units,
            domain=binding.domain,
            geometry=normalized_geometry,
            steps=steps,
            solver=flow.solver,
            view3D=View3DV3(),
            materialDefinitions=binding.materialDefinitions,
        )
    else:
        project = ProjectV7(
            schemaVersion=7,
            name=flow.name,
            dimension=2,
            units=binding.units,
            domain=binding.domain,
            geometry=normalized_geometry,
            steps=steps,
            solver=flow.solver,
            view=View(panX=0.0, panH=0.0, zoom=1.0, showGrid=True),
            materialDefinitions=binding.materialDefinitions,
        )

    flow_payload = flow.model_dump(mode="json", exclude_none=True)
    if placements:
        flow_payload["placements"] = {
            authored: {"moduleInstanceIdPath": path, "iterationKey": key}
            for authored, (path, key) in sorted(placements.items())
        }
    flow_hash = sha256_hex(flow_payload)
    binding_hash = sha256_hex(binding.model_dump(mode="json", exclude_none=True))
    material_hash = sha256_hex(
        [item.model_dump(mode="json", exclude_none=True) for item in (binding.materialDefinitions or [])]
    )
    project_hash = full_project_hash(project)
    source_map_digest = source_map_hash(source_map)
    engine_versions = dict(engine_versions or {})
    variable_set = list(variable_set or [])
    overrides = list(overrides or [])

    compile_id = compile_record_id(
        compiler_version=compiler_version,
        flow_revision_hash=flow_hash,
        geometry_binding_hash=binding_hash,
        material_definitions_hash=material_hash,
        variable_set=variable_set,
        overrides=overrides,
        seed_policy="shared-run-seed",
        step_id_policy=STEP_ID_POLICY,
        engine_versions=engine_versions,
    )
    record = CompileRecordV1(
        compileId=compile_id,
        compilerVersion=compiler_version,
        flowId=flow.flowId,
        flowRevisionId=f"{flow.flowId}:{flow.revision}",
        flowRevisionHash=flow_hash,
        geometryBindingId=binding.bindingId,
        geometryBindingHash=binding_hash,
        materialCatalogVersion=binding.materialCatalogVersion,
        materialDefinitionsHash=material_hash,
        defaultValuesVersion=binding.defaultValuesVersion,
        variableSet=variable_set,
        overrides=overrides,
        engineVersions=engine_versions,
        stepIdPolicy=STEP_ID_POLICY,
        fullProjectHash=project_hash,
        sourceMapHash=source_map_digest,
    )
    return CompiledWorkflowV1(project=project, sourceMap=source_map, compileRecord=record)
