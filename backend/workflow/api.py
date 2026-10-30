"""Typed /api/v1 workflow endpoints for P0-2 route plans."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, ValidationError

from .. import storage
from ..models import ProjectV7, StrictModel
from .metrology import MeasurementResultV1, MetrologyRecipeV1, MetricDefinitionV1
from .adoption import adopt_project, first_route
from .equivalence import ComparisonProtocolV1, EquivalenceResultV1, compare_snapshots
from .execution import execute_segment
from .models import CompiledWorkflowV1
from .provenance import ProvenanceVerificationV1, VerificationRecordV1
from .routes import AttemptV1, RoutePlanV1, SegmentRefV1, SegmentReuseV1, SegmentV1
from .store import WorkflowConflict, WorkflowError, WorkflowNotFound, WorkflowStore, WorkflowValidation


router = APIRouter(prefix="/api/v1/workflow", tags=["workflow"])


class CompileSummaryV1(StrictModel):
    compileId: str
    flowRevisionId: str
    fullProjectHash: str
    geometryBindingHash: str
    stepCount: Annotated[int, Field(ge=0)]
    createdAt: str


class CompileListResponse(StrictModel):
    compiles: list[CompileSummaryV1]


class RecipeSummaryV1(StrictModel):
    recipeId: str
    version: Annotated[int, Field(ge=1)]
    metricId: str
    method: str
    name: str
    createdAt: str


class RecipeListResponse(StrictModel):
    recipes: list[RecipeSummaryV1]


class MeasureSnapshotRequest(StrictModel):
    recipeId: str
    recipeVersion: Annotated[int, Field(ge=1)]
    snapshot: dict


class MeasureRunRequest(StrictModel):
    recipeId: str
    recipeVersion: Annotated[int, Field(ge=1)]
    runId: str
    stepId: str


class MeasurementListResponse(StrictModel):
    measurements: list[MeasurementResultV1]


class BindMeasurementRequest(StrictModel):
    segmentId: str
    runId: str
    stepId: str


class MeasurementProvenanceResponse(StrictModel):
    measurementId: str
    bindingStatus: str
    bindingEvidence: ProvenanceVerificationV1 | None = None
    verifications: list[VerificationRecordV1] = Field(default_factory=list)


class RouteSummaryV1(StrictModel):
    routeId: str
    flowId: str
    name: str
    headAttemptId: str
    stepCount: Annotated[int, Field(ge=0)]
    attemptCount: Annotated[int, Field(ge=0)]
    legacyUnverified: bool = False
    updatedAt: str


class RouteListResponse(StrictModel):
    routes: list[RouteSummaryV1]


class RoutePlanResponse(StrictModel):
    plan: RoutePlanV1


class AppendAttemptRequest(StrictModel):
    attempt: AttemptV1
    segments: list[SegmentV1]
    reuse: list[SegmentReuseV1] = Field(default_factory=list)


class AttemptDetailResponse(StrictModel):
    attempt: AttemptV1
    segments: list[SegmentV1]
    reuse: list[SegmentReuseV1]


class MaterializeResponse(StrictModel):
    attemptId: str
    segments: list[SegmentRefV1]


class SegmentExecutionResponse(StrictModel):
    segment: SegmentV1
    runId: str


class AdoptProjectRequest(StrictModel):
    project: ProjectV7
    flowId: str | None = None


class AdoptProjectResponse(StrictModel):
    flowId: str
    bindingId: str
    compileId: str
    projectName: str
    dimension: int
    stepCount: int
    compile: CompiledWorkflowV1


class RouteFromCompileRequest(StrictModel):
    compileId: str
    name: str | None = None


class EquivalenceCheckRequest(StrictModel):
    protocol: ComparisonProtocolV1
    left: dict
    right: dict


class ProtocolSummaryV1(StrictModel):
    protocolId: str
    version: int
    methodVersion: str | None = None
    createdAt: str


class ProtocolListResponse(StrictModel):
    protocols: list[ProtocolSummaryV1]


class GcDryRunResponse(StrictModel):
    referenced: list[str]
    candidates: list[dict]
    bytesReclaimable: int


class ArtifactRefRequest(StrictModel):
    artifactId: str
    ownerType: str
    ownerId: str
    requiredFor: str


class ArtifactRefItemV1(StrictModel):
    artifactId: str
    ownerType: str
    ownerId: str
    requiredFor: str


class ArtifactRefResponse(StrictModel):
    refs: list[ArtifactRefItemV1]


class AbandonRequest(StrictModel):
    artifactId: str
    capability: str
    reason: str


@lru_cache(maxsize=8)
def _store_for(var: str) -> WorkflowStore:
    store = WorkflowStore(Path(var) / "workflow" / "workflow.sqlite3")
    store.initialize()
    return store


def get_store() -> WorkflowStore:
    storage.initialize()
    return _store_for(str(storage.VAR))


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, WorkflowNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, WorkflowConflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, WorkflowValidation):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, ValidationError):
        return HTTPException(status_code=422, detail=exc.errors(include_url=False, include_context=False, include_input=False))
    return HTTPException(status_code=500, detail="workflow persistence failed")


def _call(operation):
    try:
        return operation()
    except (WorkflowError, ValidationError) as exc:
        raise _http_error(exc) from exc


@router.post("/comparison-protocols", response_model=ComparisonProtocolV1, status_code=201)
async def register_protocol(protocol: ComparisonProtocolV1) -> ComparisonProtocolV1:
    return _call(lambda: get_store().register_comparison_protocol(protocol))


@router.get("/comparison-protocols", response_model=ProtocolListResponse)
async def list_protocols() -> ProtocolListResponse:
    rows = _call(get_store().list_comparison_protocols)
    return ProtocolListResponse(protocols=[ProtocolSummaryV1.model_validate(row) for row in rows])


@router.get("/comparison-protocols/{protocol_id}", response_model=ComparisonProtocolV1)
async def get_protocol(protocol_id: str, version: int | None = None) -> ComparisonProtocolV1:
    return _call(lambda: get_store().get_comparison_protocol(protocol_id, version))


@router.get("/equivalence/{check_id}", response_model=EquivalenceResultV1)
async def get_equivalence(check_id: str) -> EquivalenceResultV1:
    return _call(lambda: get_store().get_equivalence(check_id))


@router.post("/artifact-refs", response_model=dict, status_code=201)
async def register_artifact_ref(request: ArtifactRefRequest) -> dict:
    _call(lambda: get_store().register_artifact_ref(request.artifactId, request.ownerType, request.ownerId, request.requiredFor))
    return {"registered": True}


@router.get("/artifact-refs", response_model=ArtifactRefResponse)
async def list_artifact_refs(artifact_id: str | None = None) -> ArtifactRefResponse:
    rows = _call(lambda: get_store().list_artifact_refs(artifact_id))
    return ArtifactRefResponse(refs=[ArtifactRefItemV1.model_validate(row) for row in rows])


@router.post("/gc/abandon", response_model=dict)
async def abandon_capability(request: AbandonRequest) -> dict:
    return _call(lambda: get_store().abandon_capability(request.artifactId, request.capability, request.reason))


@router.post("/gc/dry-run", response_model=GcDryRunResponse)
async def gc_dry_run() -> GcDryRunResponse:
    return GcDryRunResponse.model_validate(_call(get_store().gc_dry_run))


@router.post("/artifacts/{artifact_id}/pin", response_model=dict)
async def pin_artifact(artifact_id: str) -> dict:
    _call(lambda: get_store().pin_artifact(artifact_id))
    return {"artifactId": artifact_id, "pinned": True}


@router.post("/artifacts/{artifact_id}/unpin", response_model=dict)
async def unpin_artifact(artifact_id: str) -> dict:
    _call(lambda: get_store().unpin_artifact(artifact_id))
    return {"artifactId": artifact_id, "pinned": False}


@router.post("/equivalence/check", response_model=EquivalenceResultV1)
async def equivalence_check(request: EquivalenceCheckRequest) -> EquivalenceResultV1:
    def operation():
        store = get_store()
        store.register_comparison_protocol(request.protocol)
        return store.record_equivalence(compare_snapshots(request.protocol, request.left, request.right))
    return _call(operation)


@router.post("/metrics", response_model=MetricDefinitionV1, status_code=201)
async def register_metric(metric: MetricDefinitionV1) -> MetricDefinitionV1:
    return _call(lambda: get_store().register_metric(metric))


@router.get("/metrics/{metric_id}", response_model=MetricDefinitionV1)
async def get_metric(metric_id: str) -> MetricDefinitionV1:
    return _call(lambda: get_store().get_metric(metric_id))


@router.post("/metrology-recipes", response_model=MetrologyRecipeV1, status_code=201)
async def register_recipe(recipe: MetrologyRecipeV1) -> MetrologyRecipeV1:
    return _call(lambda: get_store().register_recipe(recipe))


@router.get("/metrology-recipes", response_model=RecipeListResponse)
async def list_recipes() -> RecipeListResponse:
    rows = _call(get_store().list_recipes)
    return RecipeListResponse(recipes=[RecipeSummaryV1.model_validate(row) for row in rows])


@router.get("/metrology-recipes/{recipe_id}", response_model=MetrologyRecipeV1)
async def get_recipe(recipe_id: str, version: int | None = None) -> MetrologyRecipeV1:
    return _call(lambda: get_store().get_recipe(recipe_id, version))


@router.post("/measurements", response_model=MeasurementResultV1, status_code=201)
async def measure_snapshot(request: MeasureSnapshotRequest) -> MeasurementResultV1:
    def operation():
        store = get_store()
        recipe = store.get_recipe(request.recipeId, request.recipeVersion)
        metric = store.get_metric(recipe.metricId)
        return store.measure_and_record(recipe, metric, request.snapshot)
    return _call(operation)


@router.post("/measurements/from-run", response_model=MeasurementResultV1, status_code=201)
async def measure_run(request: MeasureRunRequest) -> MeasurementResultV1:
    def operation():
        store = get_store()
        recipe = store.get_recipe(request.recipeId, request.recipeVersion)
        metric = store.get_metric(recipe.metricId)
        return store.measure_run(recipe, metric, request.runId, request.stepId)
    return _call(operation)


@router.get("/measurements", response_model=MeasurementListResponse)
async def list_measurements(
    run_id: str | None = None,
    recipe_id: str | None = None,
    binding_status: str | None = None,
) -> MeasurementListResponse:
    rows = _call(
        lambda: get_store().list_measurements(
            run_id=run_id,
            recipe_id=recipe_id,
            binding_status=binding_status,
        )
    )
    return MeasurementListResponse(measurements=rows)


@router.post("/measurements/{measurement_id}/bind", response_model=MeasurementResultV1)
async def bind_measurement(measurement_id: str, request: BindMeasurementRequest) -> MeasurementResultV1:
    return _call(
        lambda: get_store().bind_measurement(
            measurement_id,
            request.segmentId,
            request.runId,
            request.stepId,
        )
    )


@router.get("/measurements/{measurement_id}/provenance", response_model=MeasurementProvenanceResponse)
async def measurement_provenance(measurement_id: str) -> MeasurementProvenanceResponse:
    store = get_store()
    measurement = _call(lambda: store.get_measurement(measurement_id))
    verifications = _call(lambda: store.list_verifications(measurement_id))
    return MeasurementProvenanceResponse(
        measurementId=measurement.measurementId,
        bindingStatus=measurement.bindingStatus,
        bindingEvidence=measurement.bindingEvidence,
        verifications=verifications,
    )


@router.get("/measurements/{measurement_id}", response_model=MeasurementResultV1)
async def get_measurement(measurement_id: str) -> MeasurementResultV1:
    return _call(lambda: get_store().get_measurement(measurement_id))


@router.post("/compiles", response_model=CompiledWorkflowV1, status_code=201)
async def register_compile(compiled: CompiledWorkflowV1) -> CompiledWorkflowV1:
    return _call(lambda: get_store().register_compile(compiled))


@router.get("/compiles", response_model=CompileListResponse)
async def list_compiles() -> CompileListResponse:
    rows = _call(get_store().list_compiles)
    return CompileListResponse(compiles=[CompileSummaryV1.model_validate(row) for row in rows])


@router.get("/compiles/{compile_id}", response_model=CompiledWorkflowV1)
async def get_compile(compile_id: str) -> CompiledWorkflowV1:
    return _call(lambda: get_store().get_compile(compile_id))


@router.post("/flows/from-project", response_model=AdoptProjectResponse, status_code=201)
async def adopt_project_endpoint(request: AdoptProjectRequest) -> AdoptProjectResponse:
    def operation():
        flow, binding, compiled = adopt_project(request.project.model_dump(mode="json", exclude_none=True), flow_id=request.flowId)
        get_store().register_compile(compiled)
        return AdoptProjectResponse(
            flowId=flow.flowId,
            bindingId=binding.bindingId,
            compileId=compiled.compileRecord.compileId,
            projectName=compiled.project.name,
            dimension=compiled.project.dimension,
            stepCount=len(compiled.project.steps),
            compile=compiled,
        )
    return _call(operation)


@router.post("/routes/from-compile", response_model=RoutePlanResponse, status_code=201)
async def route_from_compile(request: RouteFromCompileRequest) -> RoutePlanResponse:
    def operation():
        compiled = get_store().get_compile(request.compileId)
        plan = first_route(compiled, name=request.name)
        get_store().create_plan(plan)
        return RoutePlanResponse(plan=plan)
    return _call(operation)


@router.post("/routes", response_model=RoutePlanResponse, status_code=201)
async def create_route(plan: RoutePlanV1) -> RoutePlanResponse:
    stored = _call(lambda: get_store().create_plan(plan))
    return RoutePlanResponse(plan=stored)


@router.get("/routes", response_model=RouteListResponse)
async def list_routes() -> RouteListResponse:
    rows = _call(get_store().list_plans)
    return RouteListResponse(routes=[RouteSummaryV1.model_validate(row) for row in rows])


@router.get("/routes/{route_id}", response_model=RoutePlanResponse)
async def get_route(route_id: str) -> RoutePlanResponse:
    plan = _call(lambda: get_store().get_plan(route_id))
    return RoutePlanResponse(plan=plan)


@router.post("/routes/{route_id}/attempts", response_model=RoutePlanResponse)
async def append_attempt(route_id: str, request: AppendAttemptRequest) -> RoutePlanResponse:
    plan = _call(
        lambda: get_store().append_attempt(
            route_id,
            request.attempt,
            request.segments,
            request.reuse,
        )
    )
    return RoutePlanResponse(plan=plan)


@router.get("/routes/{route_id}/attempts/{attempt_id}/materialize", response_model=MaterializeResponse)
async def materialize_route(route_id: str, attempt_id: str) -> MaterializeResponse:
    segments = _call(lambda: get_store().materialize(route_id, attempt_id))
    return MaterializeResponse(attemptId=attempt_id, segments=segments)


@router.get("/attempts/{attempt_id}", response_model=AttemptDetailResponse)
async def get_attempt(attempt_id: str) -> AttemptDetailResponse:
    detail = _call(lambda: get_store().get_attempt_detail(attempt_id))
    return AttemptDetailResponse.model_validate(detail)


@router.post("/attempts/{attempt_id}/cancel", response_model=AttemptV1)
async def cancel_attempt(attempt_id: str) -> AttemptV1:
    return _call(lambda: get_store().update_attempt_status(attempt_id, "cancelled"))


@router.get("/segments/{segment_id}", response_model=SegmentV1)
async def get_segment(segment_id: str) -> SegmentV1:
    return _call(lambda: get_store().get_segment(segment_id))


@router.post("/segments/{segment_id}/execute", response_model=SegmentExecutionResponse)
async def execute_segment_endpoint(segment_id: str, request: Request) -> SegmentExecutionResponse:
    supervisor = getattr(request.app.state, "supervisor", None)
    if supervisor is None:
        raise HTTPException(status_code=503, detail="supervisor is unavailable")
    segment, run_id = _call(lambda: execute_segment(get_store(), supervisor, segment_id))
    return SegmentExecutionResponse(segment=segment, runId=run_id)


@router.post("/segments/{segment_id}/cancel", response_model=SegmentV1)
async def cancel_segment(segment_id: str, request: Request) -> SegmentV1:
    store = get_store()
    segment = _call(lambda: store.get_segment(segment_id))
    if segment.runId and segment.status == "running":
        supervisor = getattr(request.app.state, "supervisor", None)
        if supervisor is not None:
            supervisor.cancel(segment.runId)
            return segment
    return _call(lambda: store.update_segment_status(segment_id, "cancelled"))
