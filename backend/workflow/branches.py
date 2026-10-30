"""Append a branch attempt that reuses a verified prefix checkpoint."""

from __future__ import annotations

import uuid

from .hashing import full_project_hash
from .routes import AttemptV1, SegmentReuseV1, SegmentV1
from .store import WorkflowConflict, WorkflowStore
from .verification import prefix_compatibility


def create_branch(
    store: WorkflowStore,
    route_id: str,
    compile_id: str,
    start_after_step_id: str,
    changed_step_ids: list[str],
    *,
    source_segment_id: str | None = None,
    submission_key: str | None = None,
):
    plan = store.get_plan(route_id)
    target = store.get_compile(compile_id)
    step_ids = [step.id for step in target.project.steps]
    if step_ids != plan.stepOrder:
        raise WorkflowConflict("branch compile step ids must match the existing route")
    if start_after_step_id not in step_ids:
        raise WorkflowConflict("branch startAfterStepId is not in the route")
    prefix_ids = step_ids[: step_ids.index(start_after_step_id) + 1]
    if source_segment_id is None:
        source = next(
            (segment for segment in plan.segments
             if segment.throughStepId == start_after_step_id and segment.status == "succeeded" and segment.endCheckpointRef),
            None,
        )
    else:
        source = next(
            (segment for segment in plan.segments if segment.segmentId == source_segment_id),
            None,
        )
        if source is not None and (
            source.throughStepId != start_after_step_id
            or source.status != "succeeded"
            or source.endCheckpointRef is None
        ):
            source = None
    if source is None:
        raise WorkflowConflict("no succeeded prefix segment provides the branch checkpoint")
    source_attempt = store.get_attempt(source.attemptId)
    source_compile = store.get_compile(source.sourceCompileId)
    source_prefix = prefix_compatibility(source_compile, prefix_ids, source_attempt.runSeed)
    target_prefix = prefix_compatibility(target, prefix_ids, source_attempt.runSeed)
    if source_prefix != target_prefix:
        raise WorkflowConflict("branch compile is not compatible with the reused prefix")
    suffix = uuid.uuid4().hex[:10]
    attempt = AttemptV1(
        attemptId=f"attempt_{suffix}", routeId=route_id, parentAttemptId=None,
        compileId=compile_id, submissionKey=submission_key or f"branch_{suffix}",
        baseCheckpointRef=source.endCheckpointRef, startAfterStepId=start_after_step_id,
        throughStepId=step_ids[-1], changedStepIds=changed_step_ids,
        prefixCompatibility=target_prefix, seedPolicy="shared-run-seed",
        runSeed=source_attempt.runSeed, flowDigest=target.compileRecord.fullProjectHash, status="planned",
    )
    segment = SegmentV1(
        segmentId=f"segment_{suffix}", attemptId=attempt.attemptId, sourceCompileId=compile_id,
        segmentProjectHash=full_project_hash(target.project), startAfterStepId=start_after_step_id,
        throughStepId=step_ids[-1], startCheckpointRef=source.endCheckpointRef,
        endCheckpointRef=None, runId=f"pending_{suffix}", provenanceInputHash="0" * 64,
        flowDigest=target.compileRecord.fullProjectHash, executionHash="0" * 64, status="planned",
    )
    reuse = SegmentReuseV1(
        reuseId=f"reuse_{suffix}", attemptId=attempt.attemptId,
        reusedFromSegmentId=source.segmentId, fromStepId=step_ids[0],
        toStepId=start_after_step_id, compatibility=source_prefix,
    )
    return store.append_attempt(route_id, attempt, [segment], [reuse])
