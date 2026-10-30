"""Adopt an existing ProjectV7 into the workflow layer and build a first Route."""

from __future__ import annotations

import uuid
from typing import Any

from ..models import ProjectV7, SolverV7, parse_project
from ..validation import ProjectInputError, validate_project
from .compiler import compile_flow
from .hashing import full_project_hash
from .models import FlowRevisionV1, GeometryBindingV1
from .routes import AttemptV1, RoutePlanV1, RouteV1, SegmentV1


def adopt_project(
    project_payload: dict[str, Any],
    *,
    flow_id: str | None = None,
    steps: list[Any] | None = None,
) -> tuple[FlowRevisionV1, GeometryBindingV1, Any]:
    try:
        project = parse_project(project_payload)
        validated, _warnings = validate_project(project_payload, kind="simulate")
    except (ValueError, ProjectInputError) as exc:
        raise ValueError(f"project cannot be adopted: {exc}") from exc
    if not isinstance(validated, ProjectV7):
        raise ValueError("workflow adoption currently requires a schemaVersion 7 project")
    suffix = uuid.uuid4().hex[:12]
    flow_id = flow_id or f"flow_{suffix}"
    binding = GeometryBindingV1(
        schemaVersion=1,
        bindingId=f"bind_{suffix}",
        name=f"{validated.name} · geometry",
        dimension=validated.dimension,
        units=validated.units,
        domain=validated.domain,
        geometry=validated.geometry,
        materialDefinitions=validated.materialDefinitions,
    )
    flow = FlowRevisionV1(
        schemaVersion=1,
        flowId=flow_id,
        revision=1,
        name=validated.name,
        steps=steps if steps is not None else validated.steps,
        solver=validated.solver,
    )
    compiled = compile_flow(flow, binding)
    return flow, binding, compiled


def first_route(compiled: Any, *, name: str | None = None) -> RoutePlanV1:
    suffix = uuid.uuid4().hex[:10]
    step_ids = [step.id for step in compiled.project.steps if step.enabled]
    if not step_ids:
        raise ValueError("workflow route requires at least one enabled step")
    attempt = AttemptV1(
        attemptId=f"attempt_{suffix}",
        routeId=f"route_{suffix}",
        compileId=compiled.compileRecord.compileId,
        submissionKey=f"submit_{suffix}",
        startAfterStepId=None,
        throughStepId=step_ids[-1],
        seedPolicy="shared-run-seed",
        runSeed=compiled.project.solver.random.seed if compiled.project.solver.random.mode == "fixed" else 0,
        flowDigest=compiled.compileRecord.fullProjectHash,
        status="planned",
    )
    segment = SegmentV1(
        segmentId=f"segment_{suffix}",
        attemptId=attempt.attemptId,
        sourceCompileId=compiled.compileRecord.compileId,
        segmentProjectHash=full_project_hash(compiled.project),
        startAfterStepId=None,
        throughStepId=step_ids[-1],
        startCheckpointRef=None,
        endCheckpointRef=None,
        runId=f"pending_{suffix}",
        provenanceInputHash="0" * 64,
        flowDigest=compiled.compileRecord.fullProjectHash,
        executionHash="0" * 64,
        resultDigest=None,
        status="planned",
    )
    return RoutePlanV1(
        route=RouteV1(
            routeId=attempt.routeId,
            flowId=compiled.compileRecord.flowId,
            name=name or f"{compiled.project.name} · route",
            headAttemptId=attempt.attemptId,
        ),
        stepOrder=step_ids,
        attempts=[attempt],
        segments=[segment],
        reuse=[],
    )
