"""Typed /api/v1 workflow endpoints for P0-2 route plans."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import Field, ValidationError

from .. import storage
from ..models import StrictModel
from .metrology import MeasurementResultV1, MetrologyRecipeV1, MetricDefinitionV1
from .models import CompiledWorkflowV1
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
async def list_measurements(run_id: str | None = None, recipe_id: str | None = None) -> MeasurementListResponse:
    rows = _call(lambda: get_store().list_measurements(run_id=run_id, recipe_id=recipe_id))
    return MeasurementListResponse(measurements=rows)


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


@router.post("/segments/{segment_id}/cancel", response_model=SegmentV1)
async def cancel_segment(segment_id: str) -> SegmentV1:
    return _call(lambda: get_store().update_segment_status(segment_id, "cancelled"))
