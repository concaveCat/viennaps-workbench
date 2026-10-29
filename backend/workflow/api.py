"""Typed /api/v1 workflow endpoints for P0-2 route plans."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import Field, ValidationError

from .. import storage
from ..models import StrictModel
from .routes import AttemptV1, RoutePlanV1, SegmentRefV1, SegmentReuseV1, SegmentV1
from .store import WorkflowConflict, WorkflowError, WorkflowNotFound, WorkflowStore


router = APIRouter(prefix="/api/v1/workflow", tags=["workflow"])


class RouteSummaryV1(StrictModel):
    routeId: str
    flowId: str
    name: str
    headAttemptId: str
    stepCount: Annotated[int, Field(ge=0)]
    attemptCount: Annotated[int, Field(ge=0)]
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
    if isinstance(exc, ValidationError):
        return HTTPException(status_code=422, detail=exc.errors(include_url=False, include_context=False, include_input=False))
    return HTTPException(status_code=500, detail="workflow persistence failed")


def _call(operation):
    try:
        return operation()
    except (WorkflowError, ValidationError) as exc:
        raise _http_error(exc) from exc


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
