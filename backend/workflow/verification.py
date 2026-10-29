"""Derive prefix compatibility from verified compile envelopes."""

from __future__ import annotations

from typing import Mapping

from .ids import sha256_hex
from .models import CompiledWorkflowV1
from .routes import PrefixCompatibilityV1, RoutePlanV1


def prefix_compatibility(
    compiled: CompiledWorkflowV1,
    step_ids: list[str],
    run_seed: int,
) -> PrefixCompatibilityV1:
    """Derive the reusable-prefix identity from a verified compile envelope."""

    project_step_ids = [step.id for step in compiled.project.steps]
    if not step_ids:
        raise ValueError("prefix must contain at least one step")
    if step_ids != project_step_ids[: len(step_ids)]:
        raise ValueError("prefix step ids must be the leading compiled steps")
    steps = compiled.project.steps[: len(step_ids)]
    step_payloads = [step.model_dump(mode="json", exclude_none=True) for step in steps]
    resolved_digest = sha256_hex(step_payloads)
    solver_payload = compiled.project.solver.model_dump(mode="json", exclude_none=True)
    for key in ("random", "timeoutSeconds"):
        solver_payload.pop(key, None)
    solver_digest = sha256_hex(solver_payload)
    prefix_digest = sha256_hex(
        {
            "stepIds": step_ids,
            "steps": step_payloads,
            "geometryBindingHash": compiled.compileRecord.geometryBindingHash,
            "solverDigest": solver_digest,
        }
    )
    return PrefixCompatibilityV1(
        prefixDigest=prefix_digest,
        geometryBindingHash=compiled.compileRecord.geometryBindingHash,
        solverDigest=solver_digest,
        seedPolicy=compiled.compileRecord.seedPolicy,
        runSeed=run_seed,
        stepIds=list(step_ids),
        resolvedStepDigest=resolved_digest,
    )


def verify_route_plan_compiles(
    plan: RoutePlanV1,
    compiles: Mapping[str, CompiledWorkflowV1],
) -> None:
    """Check that declarations come from the verified source/target compiles.

    Structural consistency is already enforced by RoutePlanV1. This function
    closes the gap between two independently supplied compatibility summaries.
    """

    if plan.route.legacyUnverified:
        raise ValueError("legacy unverified route cannot be used for execution")
    attempts = {attempt.attemptId: attempt for attempt in plan.attempts}
    segments = {segment.segmentId: segment for segment in plan.segments}
    reuse_by_attempt: dict[str, list] = {attempt.attemptId: [] for attempt in plan.attempts}
    for item in plan.reuse:
        reuse_by_attempt[item.attemptId].append(item)

    for segment in plan.segments:
        compile_record = compiles.get(segment.sourceCompileId)
        if compile_record is None:
            raise ValueError(f"segment {segment.segmentId!r} references an unverified compile")
        if segment.flowDigest != compile_record.compileRecord.fullProjectHash:
            raise ValueError(f"segment {segment.segmentId!r} flowDigest does not match its compile")

    for attempt in plan.attempts:
        target_compile = compiles.get(attempt.compileId)
        if target_compile is None:
            raise ValueError(f"attempt {attempt.attemptId!r} references an unverified compile")
        if attempt.flowDigest != target_compile.compileRecord.fullProjectHash:
            raise ValueError(f"attempt {attempt.attemptId!r} flowDigest does not match its compile")

        reuses = reuse_by_attempt[attempt.attemptId]
        if not reuses:
            if attempt.prefixCompatibility is not None:
                raise ValueError(f"attempt {attempt.attemptId!r} declares compatibility without reuse")
            continue
        if attempt.prefixCompatibility is None:
            raise ValueError(f"attempt {attempt.attemptId!r} reuses a prefix without compatibility")

        target_expected = prefix_compatibility(
            target_compile,
            attempt.prefixCompatibility.stepIds,
            attempt.runSeed,
        )
        if attempt.prefixCompatibility != target_expected:
            raise ValueError(f"attempt {attempt.attemptId!r} compatibility is not derived from its compile")

        for item in reuses:
            source_segment = segments.get(item.reusedFromSegmentId)
            if source_segment is None:
                raise ValueError(f"reuse {item.reuseId!r} references a missing segment")
            source_attempt = attempts.get(source_segment.attemptId)
            if source_attempt is None:
                raise ValueError(f"reuse {item.reuseId!r} source attempt is missing")
            source_compile = compiles.get(source_segment.sourceCompileId)
            if source_compile is None:
                raise ValueError(f"reuse {item.reuseId!r} references an unverified source compile")
            source_expected = prefix_compatibility(
                source_compile,
                item.compatibility.stepIds,
                source_attempt.runSeed,
            )
            if item.compatibility != source_expected:
                raise ValueError(f"reuse {item.reuseId!r} compatibility is not derived from its source compile")
            if item.compatibility != attempt.prefixCompatibility:
                raise ValueError(f"reuse {item.reuseId!r} source and target prefixes differ")
