"""Deterministic P0 compiler from a workflow flow to the frozen execution schema."""

from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter

from ..models import ProjectV6, ProjectV7, Solver, StepV5, StepV7, View, View3DV3
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


def _base_solver(flow: FlowRevisionV1) -> Solver:
    """Drop V7-only solver fields when compiling a 2D ProjectV6."""

    return Solver.model_validate(
        flow.solver.model_dump(mode="python", include=set(Solver.model_fields))
    )


def _template_project(flow: FlowRevisionV1, binding: GeometryBindingV1) -> ProjectV6 | ProjectV7:
    """Validate the geometry binding by building an empty execution project."""

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
    return ProjectV6(
        schemaVersion=6,
        name=flow.name,
        dimension=2,
        units=binding.units,
        domain=binding.domain,
        geometry=binding.geometry,
        steps=[],
        solver=_base_solver(flow),
        view=View(panX=0.0, panH=0.0, zoom=1.0, showGrid=True),
        materialDefinitions=binding.materialDefinitions,
    )


def _compiled_steps(
    flow: FlowRevisionV1, dimension: int
) -> tuple[list[Any], list[SourceMapEntryV1]]:
    adapter = TypeAdapter(StepV7 if dimension == 3 else StepV5)
    compiled: list[Any] = []
    source_map: list[SourceMapEntryV1] = []

    for step in flow.steps:
        step_id = stable_step_id(
            flow_id=flow.flowId,
            module_instance_id_path="root",
            authored_step_id=step.id,
            iteration_key="i0",
        )
        payload = step.model_dump(mode="json", exclude_none=True)
        payload["id"] = step_id
        compiled_step = adapter.validate_python(payload)
        compiled.append(compiled_step)
        source_map.append(
            SourceMapEntryV1(
                stepId=step_id,
                flowPath=f"root/{step.id}",
                authoredStepId=step.id,
                moduleInstanceIdPath="root",
                iterationKey="i0",
                parameterProvenance={
                    key: ParameterProvenanceV1(kind="literal")
                    for key in payload
                    if key not in {"id", "name", "enabled"}
                },
            )
        )
    return compiled, source_map


def _execution_project_payload(project: ProjectV6 | ProjectV7) -> dict[str, Any]:
    """Presentation fields do not participate in the execution compile hash."""

    payload = project.model_dump(mode="json", exclude_none=True)
    for key in ("name", "view", "view3D"):
        payload.pop(key, None)
    return payload


def compile_flow(
    flow: FlowRevisionV1,
    binding: GeometryBindingV1,
    *,
    compiler_version: str = COMPILER_VERSION,
    engine_versions: dict[str, str] | None = None,
) -> CompiledWorkflowV1:
    """Compile a literal-step flow and a geometry binding into ProjectV6/V7.

    The returned project is the full-flow execution specification. Segment
    inputs are cut from it later; P0 does not add workflow metadata to it.
    """

    template = _template_project(flow, binding)
    normalized_geometry = dict(template.geometry)
    steps, source_map = _compiled_steps(flow, binding.dimension)

    if binding.dimension == 3:
        project: ProjectV6 | ProjectV7 = ProjectV7(
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
        project = ProjectV6(
            schemaVersion=6,
            name=flow.name,
            dimension=2,
            units=binding.units,
            domain=binding.domain,
            geometry=normalized_geometry,
            steps=steps,
            solver=_base_solver(flow),
            view=View(panX=0.0, panH=0.0, zoom=1.0, showGrid=True),
            materialDefinitions=binding.materialDefinitions,
        )

    flow_hash = sha256_hex(flow.model_dump(mode="json", exclude_none=True))
    binding_hash = sha256_hex(binding.model_dump(mode="json", exclude_none=True))
    material_hash = sha256_hex(
        [item.model_dump(mode="json", exclude_none=True) for item in (binding.materialDefinitions or [])]
    )
    project_hash = sha256_hex(_execution_project_payload(project))
    source_map_hash = sha256_hex(
        [entry.model_dump(mode="json", exclude_none=True) for entry in source_map]
    )
    engine_versions = dict(engine_versions or {})

    identity = {
        "compilerVersion": compiler_version,
        "flowRevisionHash": flow_hash,
        "geometryBindingHash": binding_hash,
        "materialDefinitionsHash": material_hash,
        "variableSet": [],
        "overrides": [],
        "seedPolicy": "shared-run-seed",
        "stepIdPolicy": STEP_ID_POLICY,
        "engineVersions": engine_versions,
    }
    compile_id = f"c_{sha256_hex(identity)[:24]}"
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
        engineVersions=engine_versions,
        stepIdPolicy=STEP_ID_POLICY,
        fullProjectHash=project_hash,
        sourceMapHash=source_map_hash,
    )
    return CompiledWorkflowV1(project=project, sourceMap=source_map, compileRecord=record)
