"""Typed /api/v1 workflow endpoints for P0-2 route plans."""

from __future__ import annotations

import uuid
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from datetime import datetime, timezone

from pydantic import Field, ValidationError, field_serializer, model_validator

from .. import snapshots, storage
from ..models import ProjectV7, StepV7, StrictModel
from .metrology import MeasurementResultV1, MetrologyRecipeV1, MetricDefinitionV1, SnapshotPayloadV1, snapshot_content_hash
from .adoption import adopt_project, first_route
from .branches import create_branch
from .equivalence import ComparisonProtocolV1, EquivalenceResultV1, compare_snapshots
from .execution import execute_segment
from .experiments import (
    create_doe_document,
    create_experiment_document,
    experiment_csv,
    experiment_table,
    start_experiment,
)
from .layouts import build_clip_gds, canonical_clip_hash, extract_clip_polygons, save_asset, structure_bounds
from .models import CompileDiffV1, CompiledWorkflowV1, GeometryBindingV1
from .provenance import ProvenanceVerificationV1, VerificationRecordV1
from .routes import AttemptV1, RoutePlanV1, SegmentRefV1, SegmentReuseV1, SegmentV1
from .templates import FlowTemplateV1, resolve_template
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
    snapshot: SnapshotPayloadV1


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
    authoredSteps: list[StepV7] = Field(default_factory=list)
    binding: GeometryBindingV1 | None = None
    compile: CompiledWorkflowV1

    @field_serializer("authoredSteps")
    def _serialize_authored_steps(self, steps: list[StepV7]) -> list[dict]:
        return [step.model_dump(mode="json", exclude_none=True) for step in steps]


class RouteFromCompileRequest(StrictModel):
    compileId: str
    name: str | None = None
    splitAfterStepId: str | None = None


class EquivalenceCheckRequest(StrictModel):
    protocol: ComparisonProtocolV1
    left: SnapshotPayloadV1
    right: SnapshotPayloadV1
    leftRunId: str | None = None
    rightRunId: str | None = None
    leftSegmentId: str | None = None
    rightSegmentId: str | None = None
    leftStepId: str | None = None
    rightStepId: str | None = None


class ExperimentVariantSpecV1(StrictModel):
    parameters: dict[str, dict] = Field(default_factory=dict)
    replicates: Annotated[int, Field(ge=1, le=8)] = 1
    seed: int = 20261029


class CreateExperimentRequest(StrictModel):
    name: str
    project: ProjectV7
    flowId: str
    recipeId: str
    recipeVersion: int
    variants: Annotated[list[ExperimentVariantSpecV1], Field(min_length=1, max_length=256)]
    maxAttempts: Annotated[int, Field(ge=1, le=4)] = 1
    costPerRunUsd: Annotated[float, Field(ge=0, le=1_000_000)] = 0.0


class DoeFactorV1(StrictModel):
    stepId: str
    parameter: str
    values: Annotated[list[float], Field(min_length=2, max_length=10)]


class DoeRequest(StrictModel):
    name: str
    project: ProjectV7
    flowId: str
    recipeId: str
    recipeVersion: int
    factors: Annotated[list[DoeFactorV1], Field(min_length=1, max_length=5)]
    replicates: Annotated[int, Field(ge=1, le=8)] = 3
    seed: int = 20261029
    maxAttempts: Annotated[int, Field(ge=1, le=4)] = 2
    costPerRunUsd: Annotated[float, Field(ge=0, le=1_000_000)] = 0.0


class LayoutBoundsV1(StrictModel):
    x0: float
    y0: float
    x1: float
    y1: float


class LayoutStructureV1(StrictModel):
    name: str
    layers: list[int]
    layerDatatypes: list[list[int]]
    maskLayerDatatypes: list[list[int]]
    references: list[str]
    bounds: LayoutBoundsV1 | None = None


class LayoutUnitsV1(StrictModel):
    userUnitsPerDatabaseUnit: float
    metersPerDatabaseUnit: float
    micrometersPerNativeUserUnit: float
    nativeOutputAssumption: str


class LayoutV1(StrictModel):
    schemaVersion: Literal[1]
    layoutId: str
    assetId: str
    fileName: str
    sizeBytes: int
    units: LayoutUnitsV1
    structures: list[LayoutStructureV1]
    createdAt: str


class LayoutRegisterRequest(StrictModel):
    assetId: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    fileName: Annotated[str, Field(min_length=1, max_length=255, pattern=r"^[^/\\]+$")]


class LayoutSummaryV1(StrictModel):
    layoutId: str
    fileName: str
    structureCount: int
    createdAt: str


class LayoutListResponse(StrictModel):
    layouts: list[LayoutSummaryV1]


class ClipBoundsV1(StrictModel):
    x0: float
    y0: float
    x1: float
    y1: float

    @model_validator(mode="after")
    def ordered_bounds(self) -> "ClipBoundsV1":
        if not all(abs(value) <= 1_000_000 for value in (self.x0, self.y0, self.x1, self.y1)):
            raise ValueError("clip bounds must stay within ±1,000,000 µm")
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("clip bounds must satisfy x0 < x1 and y0 < y1")
        return self


class ClipLayerV1(StrictModel):
    layer: Annotated[int, Field(ge=0, le=65535)]
    datatype: Annotated[int, Field(ge=0, le=32767)] | None = None
    material: str = "Si"


class ClipAnnotationV1(StrictModel):
    annotationId: str | None = None
    text: Annotated[str, Field(min_length=1, max_length=200)]
    x: float
    y: float


class LayoutSelectionV1(StrictModel):
    structure: Annotated[str, Field(min_length=1, max_length=512)]
    layers: Annotated[list[ClipLayerV1], Field(min_length=1, max_length=32)]
    bbox: ClipBoundsV1


class ClipPreviewRequest(LayoutSelectionV1):
    pass


class ClipPolygonV1(StrictModel):
    layer: int
    datatype: int
    points: list[list[float]]


class ClipContentBoundsV1(StrictModel):
    x0: float
    y0: float
    x1: float
    y1: float


class ClipPreviewResponse(StrictModel):
    structure: str
    bbox: ClipBoundsV1
    width: float
    height: float
    polygons: list[ClipPolygonV1]
    polygonCount: int
    pointCount: int
    contentBounds: ClipContentBoundsV1


class ClipCreateRequest(LayoutSelectionV1):
    layoutId: str
    name: Annotated[str, Field(min_length=1, max_length=80)]
    annotations: list[ClipAnnotationV1] = Field(default_factory=list)


class ClipV1(StrictModel):
    schemaVersion: Literal[1]
    clipId: str
    name: str
    layoutId: str
    assetId: str
    structure: str
    layers: list[ClipLayerV1]
    bbox: ClipBoundsV1
    annotations: list[ClipAnnotationV1]
    polygons: list[ClipPolygonV1]
    polygonCount: int
    pointCount: int
    contentBounds: ClipContentBoundsV1
    width: float
    height: float
    clipHash: str
    gdsAssetId: str
    gdsFileName: str
    createdAt: str


class ClipListResponse(StrictModel):
    clips: list[ClipV1]


class ClipAnnotationRequest(StrictModel):
    text: Annotated[str, Field(min_length=1, max_length=200)]
    x: float
    y: float


class ClipCompileRequest(StrictModel):
    project: ProjectV7
    flowId: str | None = None
    gridDelta: Annotated[float, Field(gt=0, le=1)] | None = None


class TemplateSummaryV1(StrictModel):
    templateId: str
    name: str
    stepCount: int
    createdAt: str


class TemplateListResponse(StrictModel):
    templates: list[TemplateSummaryV1]


class BranchRequest(StrictModel):
    compileId: str
    startAfterStepId: str
    changedStepIds: list[str] = Field(default_factory=list)


class FromTemplateRequest(StrictModel):
    project: ProjectV7
    templateId: str
    flowId: str | None = None
    variables: dict[str, float | int | str] = Field(default_factory=dict)
    mode: Literal["replace", "append"] = "replace"


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
    ownerType: str
    ownerId: str
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
    return _call(lambda: get_store().abandon_capability(
        request.artifactId, request.ownerType, request.ownerId, request.capability, request.reason,
    ))


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


def _load_run_snapshot(run_id: str, step_id: str) -> tuple[dict | None, dict | None, str | None]:
    try:
        run_dir = storage.run_path(run_id)
        manifest = storage.read_json(run_dir / "manifest.json")
        row = next((item for item in manifest.get("snapshots", []) if item.get("stepId") == step_id), None)
        if row is None:
            return None, manifest, f"run {run_id!r} has no snapshot for step {step_id!r}"
        path = snapshots.ensure_materialized(run_dir, step_id)
        return storage.read_json(path), manifest, None
    except (FileNotFoundError, OSError, ValueError) as exc:
        return None, None, f"run {run_id!r} snapshot is unavailable: {exc}"


@router.post("/equivalence/check", response_model=EquivalenceResultV1)
async def equivalence_check(request: EquivalenceCheckRequest) -> EquivalenceResultV1:
    def operation():
        store = get_store()
        store.register_comparison_protocol(request.protocol)
        left_payload = request.left.model_dump(mode="json", exclude_none=True)
        right_payload = request.right.model_dump(mode="json", exclude_none=True)
        result = compare_snapshots(request.protocol, left_payload, right_payload).model_copy(update={
            "leftRunId": request.leftRunId,
            "rightRunId": request.rightRunId,
            "leftSegmentId": request.leftSegmentId,
            "rightSegmentId": request.rightSegmentId,
            "leftStepId": request.leftStepId,
            "rightStepId": request.rightStepId,
        })
        reasons: list[str] = []
        failed = False
        if request.protocol.checkType != "snapshot":
            required = (request.leftRunId, request.rightRunId, request.leftSegmentId,
                        request.rightSegmentId, request.leftStepId, request.rightStepId)
            if any(value is None for value in required):
                reasons.append("checkType requires both run/segment/step identities")
            else:
                left_actual, left_manifest, left_error = _load_run_snapshot(request.leftRunId, request.leftStepId)
                right_actual, right_manifest, right_error = _load_run_snapshot(request.rightRunId, request.rightStepId)
                if left_error:
                    reasons.append(left_error)
                if right_error:
                    reasons.append(right_error)
                if left_actual is not None and snapshot_content_hash(left_payload) != snapshot_content_hash(left_actual):
                    reasons.append("left request snapshot does not match the actual run snapshot")
                    failed = True
                if right_actual is not None and snapshot_content_hash(right_payload) != snapshot_content_hash(right_actual):
                    reasons.append("right request snapshot does not match the actual run snapshot")
                    failed = True
                try:
                    left_segment = store.get_segment(request.leftSegmentId)
                    right_segment = store.get_segment(request.rightSegmentId)
                except Exception as exc:
                    reasons.append(f"segment identity is unavailable: {exc}")
                    left_segment = right_segment = None
                if left_segment is not None and right_segment is not None:
                    if request.protocol.checkType == "same_segment_replay":
                        if left_segment.segmentId != right_segment.segmentId:
                            reasons.append("same_segment_replay requires the same Segment")
                        if left_segment.runId != request.leftRunId:
                            reasons.append("left run is not owned by the selected Segment")
                        if right_segment.runId != request.rightRunId:
                            if left_manifest is None or right_manifest is None or left_manifest.get("requestedInputHash") != right_manifest.get("requestedInputHash"):
                                reasons.append("right run is neither the Segment run nor a replay of the same input")
                    else:
                        if left_segment.segmentId == right_segment.segmentId:
                            reasons.append(f"{request.protocol.checkType} requires distinct Segments")
                        if request.protocol.checkType == "prefix_reuse":
                            if right_segment.startAfterStepId is None or right_segment.startCheckpointRef is None:
                                reasons.append("prefix_reuse right Segment has no checkpoint boundary")
                            elif right_segment.startCheckpointRef.runId != request.rightRunId:
                                reasons.append("prefix_reuse right Segment does not start from the right run")
                            elif left_segment.throughStepId != right_segment.startAfterStepId:
                                reasons.append("prefix_reuse left Segment does not end at the right Segment boundary")
                            if left_segment.runId != request.leftRunId:
                                reasons.append("left run is not owned by the left Segment")
                        elif left_segment.runId != request.leftRunId or right_segment.runId != request.rightRunId:
                            reasons.append("one or both runs are not owned by their Segments")
        if reasons:
            result = result.model_copy(update={"status": "failed" if failed else "inconclusive", "reasons": reasons})
        return store.record_equivalence(result)
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
        return store.measure_and_record(recipe, metric, request.snapshot.model_dump(mode="json", exclude_none=True))
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
            authoredSteps=flow.steps,
            binding=binding,
            compile=compiled,
        )
    return _call(operation)


@router.post("/routes/from-compile", response_model=RoutePlanResponse, status_code=201)
async def route_from_compile(request: RouteFromCompileRequest) -> RoutePlanResponse:
    def operation():
        compiled = get_store().get_compile(request.compileId)
        plan = first_route(compiled, name=request.name, split_after_step_id=request.splitAfterStepId)
        get_store().create_plan(plan)
        return RoutePlanResponse(plan=plan)
    return _call(operation)


@router.post("/experiments", response_model=dict, status_code=201)
async def create_experiment(request: CreateExperimentRequest) -> dict:
    def operation():
        store = get_store()
        recipe = store.get_recipe(request.recipeId, request.recipeVersion)
        metric = store.get_metric(recipe.metricId)
        document = create_experiment_document(
            name=request.name,
            project=request.project.model_dump(mode="json", exclude_none=True),
            flow_id=request.flowId,
            recipe_id=request.recipeId,
            recipe_version=request.recipeVersion,
            variants=[item.model_dump(mode="json") for item in request.variants],
            metric_id=metric.metricId, unit=metric.unit,
            max_attempts=request.maxAttempts, cost_per_run_usd=request.costPerRunUsd,
        )
        return store.save_experiment(document["experimentId"], document)
    return _call(operation)


@router.post("/experiments/doe", response_model=dict, status_code=201)
async def create_doe(request: DoeRequest) -> dict:
    def operation():
        store = get_store()
        recipe = store.get_recipe(request.recipeId, request.recipeVersion)
        metric = store.get_metric(recipe.metricId)
        try:
            document = create_doe_document(
                name=request.name,
                project=request.project.model_dump(mode="json", exclude_none=True),
                flow_id=request.flowId,
                recipe_id=request.recipeId,
                recipe_version=request.recipeVersion,
                factors=[item.model_dump(mode="json") for item in request.factors],
                replicates=request.replicates,
                seed=request.seed,
                metric_id=metric.metricId,
                unit=metric.unit,
                max_attempts=request.maxAttempts,
                cost_per_run_usd=request.costPerRunUsd,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return store.save_experiment(document["experimentId"], document)
    return _call(operation)


@router.get("/experiments/{experiment_id}/aggregate", response_model=dict)
async def get_experiment_aggregate(experiment_id: str) -> dict:
    document = _call(lambda: get_store().get_experiment(experiment_id))
    return document.get("aggregation") or {"rows": []}


@router.get("/experiments/{experiment_id}/table", response_model=dict)
async def get_experiment_table(
    experiment_id: str,
    min_value: float | None = None,
    max_value: float | None = None,
    sort: str = "ordinal",
    order: str = "asc",
) -> dict:
    document = _call(lambda: get_store().get_experiment(experiment_id))
    return experiment_table(document, min_value=min_value, max_value=max_value, sort=sort, order=order)


@router.get("/experiments/{experiment_id}/export.csv", response_class=Response)
async def export_experiment_csv(experiment_id: str) -> Response:
    document = _call(lambda: get_store().get_experiment(experiment_id))
    return Response(
        content=experiment_csv(document), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{experiment_id}.csv"'},
    )


@router.get("/experiments", response_model=dict)
async def list_experiments() -> dict:
    return {"experiments": _call(get_store().list_experiments)}


@router.get("/experiments/{experiment_id}", response_model=dict)
async def get_experiment(experiment_id: str) -> dict:
    return _call(lambda: get_store().get_experiment(experiment_id))


@router.post("/experiments/{experiment_id}/run", response_model=dict)
async def run_experiment(experiment_id: str, request: Request) -> dict:
    supervisor = getattr(request.app.state, "supervisor", None)
    if supervisor is None:
        raise HTTPException(status_code=503, detail="supervisor is unavailable")
    return _call(lambda: start_experiment(get_store(), supervisor, experiment_id))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@router.post("/layouts", response_model=LayoutV1, status_code=201)
async def register_layout(request: LayoutRegisterRequest) -> LayoutV1:
    def operation():
        path = storage.resolve_gds_asset(request.assetId)
        from ..gds import inspect_gds
        data = path.read_bytes()
        info = inspect_gds(data)
        structures = []
        for structure in info["structures"]:
            try:
                bounds = structure_bounds(data, structure["name"])
            except Exception:
                bounds = None
            structures.append({**structure, "bounds": bounds})
        layout_id = f"layout_{request.assetId[:16]}"
        document = LayoutV1(
            schemaVersion=1, layoutId=layout_id, assetId=request.assetId,
            fileName=request.fileName, sizeBytes=info["fileSizeBytes"],
            units=info["units"], structures=structures, createdAt=_now_iso(),
        ).model_dump(mode="json", exclude_none=True)
        get_store().register_layout(layout_id, request.assetId, request.fileName, document)
        return LayoutV1.model_validate(document)
    return _call(operation)


@router.get("/layouts", response_model=LayoutListResponse)
async def list_layouts() -> LayoutListResponse:
    rows = _call(get_store().list_layouts)
    return LayoutListResponse(layouts=[LayoutSummaryV1.model_validate(row) for row in rows])


@router.get("/layouts/{layout_id}", response_model=LayoutV1)
async def get_layout(layout_id: str) -> LayoutV1:
    return LayoutV1.model_validate(_call(lambda: get_store().get_layout(layout_id)))


@router.post("/layouts/{layout_id}/preview", response_model=ClipPreviewResponse)
async def preview_layout(layout_id: str, request: ClipPreviewRequest) -> ClipPreviewResponse:
    def operation():
        layout = get_store().get_layout(layout_id)
        data = storage.resolve_gds_asset(layout["assetId"]).read_bytes()
        extracted = extract_clip_polygons(
            data,
            structure=request.structure,
            layers=[item.model_dump(mode="json") for item in request.layers],
            bbox=request.bbox.model_dump(mode="json"),
        )
        return ClipPreviewResponse.model_validate(extracted)
    return _call(operation)


@router.post("/clips", response_model=ClipV1, status_code=201)
async def create_clip(request: ClipCreateRequest) -> ClipV1:
    def operation():
        layout = get_store().get_layout(request.layoutId)
        data = storage.resolve_gds_asset(layout["assetId"]).read_bytes()
        layer_payload = [item.model_dump(mode="json") for item in request.layers]
        extracted = extract_clip_polygons(
            data, structure=request.structure, layers=layer_payload,
            bbox=request.bbox.model_dump(mode="json"),
        )
        clip_id = f"clip_{uuid.uuid4().hex[:16]}"
        gds = build_clip_gds(extracted["polygons"], extracted["width"], extracted["height"])
        gds_asset_id = save_asset(gds)
        annotations = [
            {
                "annotationId": item.annotationId or f"ann_{uuid.uuid4().hex[:12]}",
                "text": item.text, "x": item.x, "y": item.y,
            }
            for item in request.annotations
        ]
        document = {
            "schemaVersion": 1, "clipId": clip_id, "name": request.name,
            "layoutId": request.layoutId, "assetId": layout["assetId"],
            "structure": request.structure, "layers": layer_payload,
            "bbox": request.bbox.model_dump(mode="json"), "annotations": annotations,
            "polygons": extracted["polygons"], "polygonCount": extracted["polygonCount"],
            "pointCount": extracted["pointCount"], "contentBounds": extracted["contentBounds"],
            "width": extracted["width"], "height": extracted["height"],
            "gdsAssetId": gds_asset_id, "gdsFileName": f"{clip_id}.gds",
            "createdAt": _now_iso(),
        }
        document["clipHash"] = canonical_clip_hash(document)
        get_store().save_clip(document)
        return ClipV1.model_validate(document)
    return _call(operation)


@router.get("/clips", response_model=ClipListResponse)
async def list_clips(layout_id: str | None = None) -> ClipListResponse:
    rows = _call(lambda: get_store().list_clips(layout_id))
    return ClipListResponse(clips=[ClipV1.model_validate(row) for row in rows])


@router.get("/clips/{clip_id}", response_model=ClipV1)
async def get_clip(clip_id: str) -> ClipV1:
    return ClipV1.model_validate(_call(lambda: get_store().get_clip(clip_id)))


@router.post("/clips/{clip_id}/annotations", response_model=ClipV1, status_code=201)
async def add_clip_annotation(clip_id: str, request: ClipAnnotationRequest) -> ClipV1:
    def operation():
        document = get_store().get_clip(clip_id)
        document["annotations"].append({
            "annotationId": f"ann_{uuid.uuid4().hex[:12]}",
            "text": request.text, "x": request.x, "y": request.y,
        })
        document["clipHash"] = canonical_clip_hash(document)
        get_store().save_clip(document)
        return ClipV1.model_validate(document)
    return _call(operation)


@router.post("/clips/{clip_id}/compile", response_model=AdoptProjectResponse, status_code=201)
async def compile_clip(clip_id: str, request: ClipCompileRequest) -> AdoptProjectResponse:
    def operation():
        clip = get_store().get_clip(clip_id)
        if request.project.dimension != 2:
            raise HTTPException(status_code=422, detail="layout clips currently compile 2D projects only")
        grid = request.gridDelta or request.project.domain.gridDelta
        payload = request.project.model_dump(mode="json", exclude_none=True)
        payload["geometry"] = {
            "kind": "gds", "assetId": clip["gdsAssetId"], "fileName": clip["gdsFileName"],
            "structure": "CLIP", "unitScale": 1, "position": [0, 0, 0],
            "layers": [{"layer": item["layer"], "material": item["material"]} for item in clip["layers"]],
        }
        payload["domain"] = {
            "gridDelta": grid,
            "xExtent": max(float(clip["width"]) + 2 * grid, 4 * grid),
            "xBoundary": "reflective",
        }
        source = {
            "kind": "layout", "layoutId": clip["layoutId"], "clipId": clip["clipId"],
            "clipHash": clip["clipHash"], "structure": clip["structure"], "bbox": clip["bbox"],
            "polygonCount": clip["polygonCount"], "pointCount": clip["pointCount"],
            "annotations": clip["annotations"],
        }
        flow, binding, compiled = adopt_project(
            payload, flow_id=request.flowId, geometry_source=source,
        )
        get_store().register_compile(compiled)
        return AdoptProjectResponse(
            flowId=flow.flowId, bindingId=binding.bindingId,
            compileId=compiled.compileRecord.compileId, projectName=compiled.project.name,
            dimension=compiled.project.dimension, stepCount=len(compiled.project.steps),
            authoredSteps=flow.steps, binding=binding, compile=compiled,
        )
    return _call(operation)


@router.post("/templates", response_model=FlowTemplateV1, status_code=201)
async def register_template(template: FlowTemplateV1) -> FlowTemplateV1:
    return _call(lambda: get_store().register_template(template))


@router.get("/templates", response_model=TemplateListResponse)
async def list_templates() -> TemplateListResponse:
    rows = _call(get_store().list_templates)
    return TemplateListResponse(templates=[TemplateSummaryV1.model_validate(row) for row in rows])


@router.get("/templates/{template_id}", response_model=FlowTemplateV1)
async def get_template(template_id: str) -> FlowTemplateV1:
    return _call(lambda: get_store().get_template(template_id))


@router.post("/flows/from-template", response_model=AdoptProjectResponse, status_code=201)
async def adopt_template(request: FromTemplateRequest) -> AdoptProjectResponse:
    def operation():
        template = get_store().get_template(request.templateId)
        template_steps = resolve_template(template, request.variables)
        if request.mode == "append":
            nonce = uuid.uuid4().hex[:8]
            inserted = [
                step.model_copy(update={"id": f"tpl_{nonce}_{index:02d}"})
                for index, step in enumerate(template_steps, start=1)
            ]
            steps = [*request.project.steps, *inserted]
        else:
            steps = template_steps
        flow, binding, compiled = adopt_project(
            request.project.model_dump(mode="json", exclude_none=True),
            flow_id=request.flowId,
            steps=steps,
        )
        get_store().register_compile(compiled)
        return AdoptProjectResponse(
            flowId=flow.flowId, bindingId=binding.bindingId,
            compileId=compiled.compileRecord.compileId, projectName=compiled.project.name,
            dimension=compiled.project.dimension, stepCount=len(compiled.project.steps),
            authoredSteps=steps, binding=binding, compile=compiled,
        )
    return _call(operation)


@router.get("/compiles/{left_id}/diff/{right_id}", response_model=CompileDiffV1)
async def compile_diff(left_id: str, right_id: str) -> CompileDiffV1:
    def operation():
        store = get_store()
        left = store.get_compile(left_id)
        right = store.get_compile(right_id)
        left_steps = {step.id: step.model_dump(mode="json", exclude_none=True) for step in left.project.steps}
        right_steps = {step.id: step.model_dump(mode="json", exclude_none=True) for step in right.project.steps}
        added = sorted(set(right_steps) - set(left_steps))
        removed = sorted(set(left_steps) - set(right_steps))
        changed = sorted(key for key in set(left_steps) & set(right_steps) if left_steps[key] != right_steps[key])
        left_order = [step.id for step in left.project.steps]
        right_order = [step.id for step in right.project.steps]
        common = set(left_order) & set(right_order)
        moved = [step_id for step_id in right_order if step_id in common and left_order.index(step_id) != right_order.index(step_id)]
        affected = sorted(set(changed) | set(moved) | set(added))
        return CompileDiffV1(
            addedStepIds=added, removedStepIds=removed, movedStepIds=moved,
            changedStepIds=changed, affectedStepIds=affected, changedVariables=[],
            details=[{"stepId": key, "before": left_steps[key], "after": right_steps[key]} for key in changed],
        )
    return _call(operation)


@router.post("/routes/{route_id}/branches", response_model=RoutePlanResponse, status_code=201)
async def branch_route(route_id: str, request: BranchRequest) -> RoutePlanResponse:
    plan = _call(lambda: create_branch(
        get_store(), route_id, request.compileId, request.startAfterStepId, request.changedStepIds,
    ))
    return RoutePlanResponse(plan=plan)


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
