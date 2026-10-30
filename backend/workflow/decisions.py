"""P4-2 measurement-driven branches and replayable decision records.

The native engine still executes one bounded Segment per run. P4-2 composes a
*workflow run* out of existing pieces: a succeeded prefix Segment is measured,
the measurement selects a branch, and a new Attempt/Segment reuses the verified
prefix checkpoint with a recompiled suffix. Every step of that composition is
recorded on :class:`MeasurementDecisionV1`, so replay can follow the record
without measuring or deciding again.
"""

from __future__ import annotations

import json
import math
import operator
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter, ValidationError

from ..models import StepV7, StrictModel
from .branches import create_branch
from .compiler import compile_flow
from .experiments import authored_project
from .models import CompiledWorkflowV1, FlowRevisionV1
from .routes import RoutePlanV1, SegmentV1
from .store import (
    WorkflowConflict,
    WorkflowNotFound,
    WorkflowStore,
    WorkflowValidation,
)


DecisionOperator = Literal["lt", "lte", "gt", "gte", "eq"]
ChosenBranch = Literal["true", "false"]
MetricUnit = Literal["um", "nm"]

_OPERATORS = {
    "lt": operator.lt,
    "lte": operator.le,
    "gt": operator.gt,
    "gte": operator.ge,
    "eq": operator.eq,
}

DecisionId = Annotated[str, Field(min_length=4, max_length=64, pattern=r"^dec_[A-Za-z0-9_-]+$")]
WorkflowRunId = Annotated[str, Field(min_length=8, max_length=64, pattern=r"^wfr_[A-Za-z0-9_-]+$")]


class BranchParametersV1(StrictModel):
    """Authored step id -> field overrides applied after the reused prefix."""

    parameters: dict[str, dict[str, Any]]


class MeasurementBranchesV1(StrictModel):
    true: BranchParametersV1
    false: BranchParametersV1


class MeasurementDecisionRequestV1(StrictModel):
    baseCompileId: str | None = None
    startAfterStepId: str | None = None
    recipeId: Annotated[str, Field(min_length=1, max_length=64)]
    recipeVersion: Annotated[int, Field(ge=1)]
    operator: DecisionOperator
    threshold: float
    branches: MeasurementBranchesV1
    # Optional continuation of an existing decision sequence on the same route.
    workflowRunId: WorkflowRunId | None = None


class MeasurementDecisionV1(StrictModel):
    """Immutable record of one measurement-driven branch decision.

    ``replayOf`` turns a replay into another explicit record: it keeps the
    original measurement, chosen branch and resolved parameters, but points at
    the newly created Attempt/Segment.
    """

    schemaVersion: Literal[1] = 1
    decisionId: DecisionId
    workflowRunId: WorkflowRunId
    routeId: Annotated[str, Field(min_length=3, max_length=64)]
    sourceSegmentId: Annotated[str, Field(min_length=3, max_length=64)]
    sourceCompileId: Annotated[str, Field(min_length=3, max_length=64)] | None = None
    sourceRunId: Annotated[str, Field(min_length=3, max_length=64)]
    startAfterStepId: Annotated[str, Field(min_length=3, max_length=64)] | None = None
    measurementId: Annotated[str, Field(min_length=3, max_length=64)]
    measurementStatus: str = "measured"
    measurementBindingStatus: Literal["unbound", "verified", "failed"] = "unbound"
    recipeId: Annotated[str, Field(min_length=1, max_length=64)]
    recipeVersion: Annotated[int, Field(ge=1)]
    metricId: Annotated[str, Field(min_length=1, max_length=64)]
    method: Literal["cd_at_height", "etch_depth"] | None = None
    measuredValue: float
    unit: MetricUnit
    operator: DecisionOperator
    threshold: float
    result: bool
    chosenBranch: ChosenBranch
    resolvedParameters: dict[str, dict[str, Any]]
    targetCompileId: Annotated[str, Field(pattern=r"^c_[a-f0-9]{24}$")]
    branchAttemptId: Annotated[str, Field(min_length=3, max_length=64)]
    branchSegmentId: Annotated[str, Field(min_length=3, max_length=64)]
    replayOf: DecisionId | None = None
    createdAt: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_decision_id() -> str:
    return f"dec_{uuid.uuid4().hex[:16]}"


def _new_workflow_run_id() -> str:
    return f"wfr_{uuid.uuid4().hex[:16]}"


def evaluate(operator_name: str, measured: float, threshold: float) -> bool:
    try:
        compare = _OPERATORS[operator_name]
    except KeyError as exc:
        raise WorkflowValidation(f"unsupported decision operator {operator_name!r}") from exc
    return bool(compare(measured, threshold))


def _workflow_run_id(
    store: WorkflowStore, route_id: str, requested: str | None
) -> str:
    """Continue the route's decision sequence unless a new run is requested.

    The first decision creates ``wfr_...``. Later decisions on the same route
    share it so one workflow run can contain a chain of measurement-driven
    branches; passing ``workflowRunId`` explicitly continues that sequence.
    """

    decisions = store.list_measurement_decisions(route_id)
    if requested is not None:
        if not any(decision.workflowRunId == requested for decision in decisions):
            raise WorkflowNotFound(
                f"workflow run {requested!r} has no decision on route {route_id!r}"
            )
        return requested
    if decisions:
        return decisions[-1].workflowRunId
    return _new_workflow_run_id()


def _source_segment(plan: RoutePlanV1, start_after_step_id: str | None) -> SegmentV1:
    succeeded = [
        segment
        for segment in plan.segments
        if segment.status == "succeeded"
        and segment.endCheckpointRef is not None
        and not segment.runId.startswith("pending_")
    ]
    if start_after_step_id is None:
        if not succeeded:
            raise WorkflowNotFound(
                "route has no succeeded prefix segment with a checkpoint to measure"
            )
        source = succeeded[-1]
        if source.throughStepId == plan.stepOrder[-1]:
            with_suffix = [
                segment for segment in succeeded
                if segment.throughStepId != plan.stepOrder[-1]
            ]
            if with_suffix:
                source = with_suffix[-1]
        return source
    source = next(
        (
            segment for segment in reversed(succeeded)
            if segment.throughStepId == start_after_step_id
        ),
        None,
    )
    if source is None:
        raise WorkflowNotFound(
            f"route has no succeeded segment through {start_after_step_id!r}"
        )
    return source


def _flow_revision(compiled: CompiledWorkflowV1) -> int:
    try:
        return int(compiled.compileRecord.flowRevisionId.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return 1


def _compile_provenance(
    resolved: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Derive stable provenance lists from the recorded resolved parameters.

    The source is a constant so replay reproduces the same compile id instead
    of folding a decision id into the compile identity.
    """

    overrides: list[dict[str, Any]] = []
    variable_set: list[dict[str, Any]] = []
    for authored_step_id in sorted(resolved):
        fields = resolved[authored_step_id]
        for field in sorted(fields):
            value = fields[field]
            overrides.append({
                "stepId": authored_step_id,
                "parameter": field,
                "value": value,
                "source": "measurement-decision",
            })
            variable_set.append({
                "name": f"{authored_step_id}.{field}",
                "value": value,
                "source": "measurement-decision",
            })
    return overrides, variable_set


def _build_target_compile(
    store: WorkflowStore,
    source_compiled: CompiledWorkflowV1,
    resolved: dict[str, dict[str, Any]],
) -> CompiledWorkflowV1:
    """Rebuild the authored flow, apply resolved overrides and compile again."""

    record = source_compiled.compileRecord
    binding = store.get_binding(record.geometryBindingId)
    payload = authored_project(source_compiled)
    steps_by_id = {str(step.get("id")): step for step in payload.get("steps", [])}
    adapter = TypeAdapter(StepV7)
    for authored_step_id, fields in resolved.items():
        step = steps_by_id.get(authored_step_id)
        if step is None:
            raise WorkflowValidation(
                f"resolved parameter step {authored_step_id!r} is not an authored step "
                "of the source compile"
            )
        if "id" in fields:
            raise WorkflowValidation(
                "resolved parameters must not change an authored step id"
            )
        try:
            merged = json.loads(json.dumps(step))
            merged.update(fields)
            merged["id"] = authored_step_id
            adapter.validate_python(merged)
        except (ValueError, TypeError, ValidationError) as exc:
            raise WorkflowValidation(
                f"resolved parameters for authored step {authored_step_id!r} are invalid: {exc}"
            ) from exc
        step.update(fields)

    try:
        steps = [adapter.validate_python(step) for step in payload["steps"]]
        flow = FlowRevisionV1(
            schemaVersion=1,
            flowId=record.flowId,
            revision=_flow_revision(source_compiled),
            name=source_compiled.project.name,
            steps=steps,
            solver=source_compiled.project.solver.model_copy(deep=True),
        )
    except (ValueError, ValidationError) as exc:
        raise WorkflowValidation(f"decision target flow is invalid: {exc}") from exc

    placements: dict[str, tuple[str, str]] | None = None
    flow_paths: dict[str, str] | None = None
    if record.moduleRevisions or any(
        (entry.moduleInstanceIdPath, entry.iterationKey) != ("root", "i0")
        for entry in source_compiled.sourceMap
    ):
        placements = {
            entry.authoredStepId: (entry.moduleInstanceIdPath, entry.iterationKey)
            for entry in source_compiled.sourceMap
        }
    if any(entry.flowPath != f"root/{entry.authoredStepId}" for entry in source_compiled.sourceMap):
        flow_paths = {entry.authoredStepId: entry.flowPath for entry in source_compiled.sourceMap}

    overrides, variable_set = _compile_provenance(resolved)
    module_revisions = (
        [item.model_dump(mode="json", exclude_none=True) for item in record.moduleRevisions]
        if record.moduleRevisions
        else None
    )
    return compile_flow(
        flow,
        binding,
        placements=placements,
        flow_paths=flow_paths,
        variable_set=variable_set,
        overrides=overrides,
        module_revisions=module_revisions,
    )


def _register_target(store: WorkflowStore, compiled: CompiledWorkflowV1) -> None:
    store.register_binding(store.get_binding(compiled.compileRecord.geometryBindingId))
    store.register_compile(compiled)


def _compile_step_ids(
    source_compiled: CompiledWorkflowV1,
) -> dict[str, str]:
    return {entry.authoredStepId: entry.stepId for entry in source_compiled.sourceMap}


def _resolved_scope(
    source_compiled: CompiledWorkflowV1,
    start_after_step_id: str,
    resolved: dict[str, dict[str, Any]],
) -> list[str]:
    """Validate authored overrides and return the changed compiled step ids.

    A decision may only change the suffix after the reused prefix; changing a
    prefix step would silently invalidate the checkpoint reuse contract.
    """

    compiled_ids = [step.id for step in source_compiled.project.steps]
    if start_after_step_id not in compiled_ids:
        raise WorkflowConflict(
            f"startAfterStepId {start_after_step_id!r} is not a compiled step of the route"
        )
    start_index = compiled_ids.index(start_after_step_id)
    if start_index >= len(compiled_ids) - 1:
        raise WorkflowValidation(
            f"startAfterStepId {start_after_step_id!r} leaves no suffix to branch"
        )
    authored_to_compiled = _compile_step_ids(source_compiled)
    changed: list[str] = []
    for authored_step_id in resolved:
        if authored_step_id not in authored_to_compiled:
            raise WorkflowValidation(
                f"resolved parameter step {authored_step_id!r} is not an authored step "
                "of the source compile"
            )
        compiled_step_id = authored_to_compiled[authored_step_id]
        if compiled_ids.index(compiled_step_id) <= start_index:
            raise WorkflowValidation(
                f"resolved parameter step {authored_step_id!r} is inside the reused prefix"
            )
        changed.append(compiled_step_id)
    return changed


def _branch_ids(plan: RoutePlanV1, submission_key: str) -> tuple[str, str]:
    attempt = next(
        (item for item in plan.attempts if item.submissionKey == submission_key),
        None,
    )
    if attempt is None:
        raise WorkflowConflict("measurement decision branch attempt was not persisted")
    segment = next(
        (item for item in plan.segments if item.attemptId == attempt.attemptId),
        None,
    )
    if segment is None:
        raise WorkflowConflict("measurement decision branch segment was not persisted")
    return attempt.attemptId, segment.segmentId


def _measure_source(
    store: WorkflowStore,
    source: SegmentV1,
    recipe_id: str,
    recipe_version: int,
):
    recipe = store.get_recipe(recipe_id, recipe_version)
    metric = store.get_metric(recipe.metricId)
    measurement = store.measure_run(
        recipe, metric, source.runId, source.throughStepId
    )
    if (
        measurement.status != "measured"
        or not isinstance(measurement.value, (int, float))
        or not math.isfinite(float(measurement.value))
    ):
        raise WorkflowValidation(
            f"measurement {measurement.measurementId} is not usable for a decision: "
            f"{measurement.status} ({measurement.reason or 'no reason'})"
        )
    # Binding evidence is recorded on the measurement; a decision keeps using
    # the real measured value even when binding is explicitly failed.
    try:
        measurement = store.bind_measurement(
            measurement.measurementId, source.segmentId, source.runId, source.throughStepId
        )
    except (WorkflowConflict, WorkflowNotFound, ValueError):
        measurement = store.get_measurement(measurement.measurementId)
    return recipe, metric, measurement


def create_measurement_decision(
    store: WorkflowStore,
    route_id: str,
    request: MeasurementDecisionRequestV1 | dict[str, Any],
) -> dict[str, Any]:
    """Measure the succeeded prefix, pick a branch and append it to the route."""

    if not isinstance(request, MeasurementDecisionRequestV1):
        request = MeasurementDecisionRequestV1.model_validate(request)
    plan = store.get_plan(route_id)
    start_after = request.startAfterStepId
    source = _source_segment(plan, start_after)
    start_after = source.throughStepId
    if source.runId.startswith("pending_") or source.endCheckpointRef is None:
        raise WorkflowConflict("source segment has no completed run checkpoint")

    source_compiled = store.get_compile(source.sourceCompileId)
    if source_compiled.compileRecord.flowId != plan.route.flowId:
        raise WorkflowConflict("source compile belongs to a different flow than the route")
    if request.baseCompileId is not None and request.baseCompileId != source.sourceCompileId:
        raise WorkflowValidation(
            "baseCompileId does not match the source segment's compile"
        )

    recipe, metric, measurement = _measure_source(
        store, source, request.recipeId, request.recipeVersion
    )
    measured = float(measurement.value)  # type: ignore[arg-type]
    if not math.isfinite(request.threshold):
        raise WorkflowValidation("decision threshold must be finite")
    result = evaluate(request.operator, measured, request.threshold)
    chosen: ChosenBranch = "true" if result else "false"
    resolved = getattr(request.branches, chosen).parameters

    changed_step_ids = _resolved_scope(source_compiled, start_after, resolved)
    target = _build_target_compile(store, source_compiled, resolved)
    _register_target(store, target)

    suffix = uuid.uuid4().hex[:10]
    submission_key = f"decision_{suffix}"
    branch_plan = create_branch(
        store,
        route_id,
        target.compileRecord.compileId,
        start_after,
        changed_step_ids,
        source_segment_id=source.segmentId,
        submission_key=submission_key,
    )
    attempt_id, segment_id = _branch_ids(branch_plan, submission_key)

    decision = MeasurementDecisionV1(
        decisionId=_new_decision_id(),
        workflowRunId=_workflow_run_id(store, route_id, request.workflowRunId),
        routeId=route_id,
        sourceSegmentId=source.segmentId,
        sourceCompileId=source.sourceCompileId,
        sourceRunId=source.runId,
        startAfterStepId=start_after,
        measurementId=measurement.measurementId,
        measurementStatus=measurement.status,
        measurementBindingStatus=measurement.bindingStatus,
        recipeId=recipe.recipeId,
        recipeVersion=recipe.version,
        metricId=metric.metricId,
        method=recipe.method,
        measuredValue=measured,
        unit=measurement.unit,
        operator=request.operator,
        threshold=request.threshold,
        result=result,
        chosenBranch=chosen,
        resolvedParameters=resolved,
        targetCompileId=target.compileRecord.compileId,
        branchAttemptId=attempt_id,
        branchSegmentId=segment_id,
        createdAt=_now(),
    )
    stored = store.save_measurement_decision(decision)
    return {
        "decision": stored,
        "plan": branch_plan,
        "workflowRunId": stored.workflowRunId,
        "measurement": measurement,
        "resolvedParameters": stored.resolvedParameters,
    }


def replay_measurement_decision(
    store: WorkflowStore,
    route_id: str,
    decision_id: str,
) -> dict[str, Any]:
    """Replay a recorded branch without measuring or re-deciding."""

    original = store.get_measurement_decision(decision_id, route_id=route_id)
    if original.routeId != route_id:
        raise WorkflowNotFound(
            f"decision {decision_id!r} does not belong to route {route_id!r}"
        )
    plan = store.get_plan(route_id)
    source = next(
        (segment for segment in plan.segments if segment.segmentId == original.sourceSegmentId),
        None,
    )
    if source is None:
        raise WorkflowNotFound(
            f"decision {decision_id!r} source segment is no longer in the route"
        )
    if source.status != "succeeded" or source.endCheckpointRef is None:
        raise WorkflowConflict(
            f"decision {decision_id!r} source segment is no longer a succeeded prefix"
        )
    if (
        (original.sourceCompileId is not None and source.sourceCompileId != original.sourceCompileId)
        or source.runId != original.sourceRunId
        or (original.startAfterStepId is not None and source.throughStepId != original.startAfterStepId)
    ):
        raise WorkflowConflict(
            f"decision {decision_id!r} source prefix identity changed"
        )
    source_compile_id = original.sourceCompileId or source.sourceCompileId
    start_after_step_id = original.startAfterStepId or source.throughStepId
    source_compiled = store.get_compile(source_compile_id)
    target = _build_target_compile(store, source_compiled, original.resolvedParameters)
    if target.compileRecord.compileId != original.targetCompileId:
        raise WorkflowConflict(
            "decision target compile cannot be reproduced from its resolved parameters"
        )
    _register_target(store, target)
    changed_step_ids = _resolved_scope(
        source_compiled, start_after_step_id, original.resolvedParameters
    )

    suffix = uuid.uuid4().hex[:10]
    submission_key = f"decision_{suffix}"
    branch_plan = create_branch(
        store,
        route_id,
        target.compileRecord.compileId,
        start_after_step_id,
        changed_step_ids,
        source_segment_id=source.segmentId,
        submission_key=submission_key,
    )
    attempt_id, segment_id = _branch_ids(branch_plan, submission_key)

    measurement = store.get_measurement(original.measurementId)
    if (
        measurement.sourceRunId != original.sourceRunId
        or measurement.sourceStepId != start_after_step_id
        or measurement.measurementId != original.measurementId
    ):
        raise WorkflowConflict(
            "decision measurement identity no longer matches its source prefix"
        )
    decision = MeasurementDecisionV1(
        decisionId=_new_decision_id(),
        workflowRunId=original.workflowRunId,
        routeId=route_id,
        sourceSegmentId=original.sourceSegmentId,
        sourceCompileId=source_compile_id,
        sourceRunId=original.sourceRunId,
        startAfterStepId=start_after_step_id,
        measurementId=original.measurementId,
        measurementStatus=original.measurementStatus,
        measurementBindingStatus=original.measurementBindingStatus,
        recipeId=original.recipeId,
        recipeVersion=original.recipeVersion,
        metricId=original.metricId,
        method=original.method,
        measuredValue=original.measuredValue,
        unit=original.unit,
        operator=original.operator,
        threshold=original.threshold,
        result=original.result,
        chosenBranch=original.chosenBranch,
        resolvedParameters=original.resolvedParameters,
        targetCompileId=original.targetCompileId,
        branchAttemptId=attempt_id,
        branchSegmentId=segment_id,
        replayOf=original.decisionId,
        createdAt=_now(),
    )
    stored = store.save_measurement_decision(decision)
    return {
        "decision": stored,
        "plan": branch_plan,
        "workflowRunId": stored.workflowRunId,
        "measurement": measurement,
        "resolvedParameters": stored.resolvedParameters,
    }
