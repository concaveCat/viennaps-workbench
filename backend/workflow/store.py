"""SQLite persistence for P0-2 route plans and execution branches."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .ids import sha256_hex
from .models import CompiledWorkflowV1
from .routes import (
    AttemptV1,
    RoutePlanV1,
    SegmentRefV1,
    SegmentReuseV1,
    SegmentV1,
    materialize_attempt,
)
from .verification import verify_route_plan_compiles


class WorkflowError(Exception):
    pass


class WorkflowNotFound(WorkflowError):
    pass


class WorkflowConflict(WorkflowError):
    pass


class WorkflowValidation(WorkflowError):
    pass


class WorkflowStore:
    def __init__(self, path: Path):
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS compiles (
                    compile_id TEXT PRIMARY KEY,
                    envelope_json TEXT NOT NULL,
                    envelope_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS routes (
                    route_id TEXT PRIMARY KEY,
                    flow_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    head_attempt_id TEXT NOT NULL,
                    step_order_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    attempt_id TEXT PRIMARY KEY,
                    route_id TEXT NOT NULL REFERENCES routes(route_id) ON DELETE CASCADE,
                    parent_attempt_id TEXT,
                    compile_id TEXT NOT NULL,
                    submission_key TEXT NOT NULL UNIQUE,
                    request_hash TEXT NOT NULL,
                    base_checkpoint_json TEXT,
                    start_after_step_id TEXT,
                    through_step_id TEXT NOT NULL,
                    changed_step_ids_json TEXT NOT NULL,
                    prefix_compatibility_json TEXT,
                    seed_policy TEXT NOT NULL,
                    run_seed INTEGER NOT NULL,
                    flow_digest TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS segments (
                    segment_id TEXT PRIMARY KEY,
                    attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id) ON DELETE CASCADE,
                    parent_segment_id TEXT,
                    source_compile_id TEXT NOT NULL,
                    segment_project_hash TEXT NOT NULL,
                    start_after_step_id TEXT,
                    through_step_id TEXT NOT NULL,
                    start_checkpoint_json TEXT,
                    end_checkpoint_json TEXT,
                    run_id TEXT NOT NULL,
                    provenance_input_hash TEXT NOT NULL,
                    flow_digest TEXT NOT NULL,
                    execution_hash TEXT NOT NULL,
                    result_digest TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reuse (
                    reuse_id TEXT PRIMARY KEY,
                    attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id) ON DELETE CASCADE,
                    reused_from_segment_id TEXT NOT NULL,
                    from_step_id TEXT NOT NULL,
                    to_step_id TEXT NOT NULL,
                    compatibility_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS attempts_route_idx ON attempts(route_id);
                CREATE INDEX IF NOT EXISTS segments_attempt_idx ON segments(attempt_id);
                CREATE INDEX IF NOT EXISTS reuse_attempt_idx ON reuse(attempt_id);
                """
            )
            attempt_columns = {row[1] for row in connection.execute("PRAGMA table_info(attempts)")}
            if "request_hash" not in attempt_columns:
                connection.execute("ALTER TABLE attempts ADD COLUMN request_hash TEXT NOT NULL DEFAULT ''")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _dump(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

    @staticmethod
    def _load(value: str | None) -> Any:
        return None if value is None else json.loads(value)

    @staticmethod
    def _attempt_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "attemptId": row["attempt_id"],
            "routeId": row["route_id"],
            "parentAttemptId": row["parent_attempt_id"],
            "compileId": row["compile_id"],
            "submissionKey": row["submission_key"],
            "baseCheckpointRef": WorkflowStore._load(row["base_checkpoint_json"]),
            "startAfterStepId": row["start_after_step_id"],
            "throughStepId": row["through_step_id"],
            "changedStepIds": WorkflowStore._load(row["changed_step_ids_json"]),
            "prefixCompatibility": WorkflowStore._load(row["prefix_compatibility_json"]),
            "seedPolicy": row["seed_policy"],
            "runSeed": row["run_seed"],
            "flowDigest": row["flow_digest"],
            "status": row["status"],
        }

    @staticmethod
    def _segment_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "segmentId": row["segment_id"],
            "attemptId": row["attempt_id"],
            "parentSegmentId": row["parent_segment_id"],
            "sourceCompileId": row["source_compile_id"],
            "segmentProjectHash": row["segment_project_hash"],
            "startAfterStepId": row["start_after_step_id"],
            "throughStepId": row["through_step_id"],
            "startCheckpointRef": WorkflowStore._load(row["start_checkpoint_json"]),
            "endCheckpointRef": WorkflowStore._load(row["end_checkpoint_json"]),
            "runId": row["run_id"],
            "provenanceInputHash": row["provenance_input_hash"],
            "flowDigest": row["flow_digest"],
            "executionHash": row["execution_hash"],
            "resultDigest": row["result_digest"],
            "status": row["status"],
        }

    @staticmethod
    def _reuse_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "reuseId": row["reuse_id"],
            "attemptId": row["attempt_id"],
            "reusedFromSegmentId": row["reused_from_segment_id"],
            "fromStepId": row["from_step_id"],
            "toStepId": row["to_step_id"],
            "compatibility": WorkflowStore._load(row["compatibility_json"]),
        }

    def register_compile(self, compiled: CompiledWorkflowV1) -> CompiledWorkflowV1:
        compiled = compiled.verify()
        payload = compiled.model_dump(mode="json", exclude_none=True)
        envelope_hash = sha256_hex(payload)
        try:
            with self._connect() as connection:
                existing = connection.execute(
                    "SELECT envelope_hash FROM compiles WHERE compile_id = ?",
                    (compiled.compileRecord.compileId,),
                ).fetchone()
                if existing is not None:
                    if existing["envelope_hash"] != envelope_hash:
                        raise WorkflowConflict("compileId already exists with different content")
                    return compiled
                connection.execute(
                    "INSERT INTO compiles (compile_id, envelope_json, envelope_hash, created_at) VALUES (?, ?, ?, ?)",
                    (
                        compiled.compileRecord.compileId,
                        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False),
                        envelope_hash,
                        self._now(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise WorkflowConflict(f"compile record conflicts with an existing object: {exc}") from exc
        return compiled

    def get_compile(self, compile_id: str) -> CompiledWorkflowV1:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT envelope_json FROM compiles WHERE compile_id = ?",
                (compile_id,),
            ).fetchone()
        if row is None:
            raise WorkflowNotFound(f"compile {compile_id!r} does not exist")
        try:
            return CompiledWorkflowV1.model_validate(json.loads(row["envelope_json"]))
        except (ValueError, TypeError) as exc:
            raise WorkflowValidation(f"stored compile {compile_id!r} is invalid") from exc

    def list_compiles(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT compile_id, envelope_json, created_at FROM compiles ORDER BY created_at DESC"
            ).fetchall()
        result = []
        for row in rows:
            try:
                compiled = CompiledWorkflowV1.model_validate(json.loads(row["envelope_json"]))
            except (ValueError, TypeError):
                continue
            result.append(
                {
                    "compileId": compiled.compileRecord.compileId,
                    "flowRevisionId": compiled.compileRecord.flowRevisionId,
                    "fullProjectHash": compiled.compileRecord.fullProjectHash,
                    "geometryBindingHash": compiled.compileRecord.geometryBindingHash,
                    "stepCount": len(compiled.project.steps),
                    "createdAt": row["created_at"],
                }
            )
        return result

    def _compile_map(self, plan: RoutePlanV1) -> dict[str, CompiledWorkflowV1]:
        ids = {attempt.compileId for attempt in plan.attempts}
        ids.update(segment.sourceCompileId for segment in plan.segments)
        return {compile_id: self.get_compile(compile_id) for compile_id in ids}

    def _verify_plan_compiles(self, plan: RoutePlanV1) -> None:
        try:
            verify_route_plan_compiles(plan, self._compile_map(plan))
        except WorkflowNotFound as exc:
            raise WorkflowValidation(str(exc)) from exc
        except WorkflowError:
            raise
        except (ValueError, KeyError) as exc:
            raise WorkflowValidation(str(exc)) from exc

    def create_plan(self, plan: RoutePlanV1) -> RoutePlanV1:
        self._verify_plan_compiles(plan)
        now = self._now()
        payload = plan.model_dump(mode="json", exclude_none=True)
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO routes (route_id, flow_id, name, head_attempt_id, step_order_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (payload["route"]["routeId"], payload["route"]["flowId"], payload["route"]["name"],
                     payload["route"]["headAttemptId"], self._dump(payload["stepOrder"]), now, now),
                )
                self._insert_attempts(connection, payload["attempts"], now)
                self._insert_segments(connection, payload["segments"], now)
                self._insert_reuse(connection, payload["reuse"], now)
        except sqlite3.IntegrityError as exc:
            raise WorkflowConflict(f"route plan conflicts with an existing workflow object: {exc}") from exc
        return self.get_plan(payload["route"]["routeId"])

    def _insert_attempts(
        self,
        connection: sqlite3.Connection,
        attempts: list[dict[str, Any]],
        now: str,
        request_hashes: dict[str, str] | None = None,
    ) -> None:
        request_hashes = request_hashes or {}
        for attempt in attempts:
            request_hash = request_hashes.get(attempt["attemptId"], sha256_hex(attempt))
            connection.execute(
                """
                INSERT INTO attempts (
                    attempt_id, route_id, parent_attempt_id, compile_id, submission_key,
                    request_hash, base_checkpoint_json, start_after_step_id, through_step_id,
                    changed_step_ids_json, prefix_compatibility_json, seed_policy,
                    run_seed, flow_digest, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt["attemptId"], attempt["routeId"], attempt.get("parentAttemptId"),
                    attempt["compileId"], attempt["submissionKey"], request_hash,
                    self._checkpoint_value(attempt.get("baseCheckpointRef")),
                    attempt.get("startAfterStepId"), attempt["throughStepId"],
                    self._dump(attempt.get("changedStepIds", [])),
                    self._dump(attempt["prefixCompatibility"]) if attempt.get("prefixCompatibility") is not None else None,
                    attempt["seedPolicy"], attempt["runSeed"], attempt["flowDigest"],
                    attempt["status"], now, now,
                ),
            )

    def _insert_segments(self, connection: sqlite3.Connection, segments: list[dict[str, Any]], now: str) -> None:
        for segment in segments:
            connection.execute(
                """
                INSERT INTO segments (
                    segment_id, attempt_id, parent_segment_id, source_compile_id,
                    segment_project_hash, start_after_step_id, through_step_id,
                    start_checkpoint_json, end_checkpoint_json, run_id,
                    provenance_input_hash, flow_digest, execution_hash,
                    result_digest, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    segment["segmentId"], segment["attemptId"], segment.get("parentSegmentId"),
                    segment["sourceCompileId"], segment["segmentProjectHash"],
                    segment.get("startAfterStepId"), segment["throughStepId"],
                    self._checkpoint_value(segment.get("startCheckpointRef")),
                    self._checkpoint_value(segment.get("endCheckpointRef")),
                    segment["runId"], segment["provenanceInputHash"],
                    segment["flowDigest"], segment["executionHash"],
                    segment.get("resultDigest"), segment["status"], now, now,
                ),
            )

    def _insert_reuse(self, connection: sqlite3.Connection, reuse: list[dict[str, Any]], now: str) -> None:
        for item in reuse:
            connection.execute(
                """
                INSERT INTO reuse (
                    reuse_id, attempt_id, reused_from_segment_id,
                    from_step_id, to_step_id, compatibility_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["reuseId"], item["attemptId"], item["reusedFromSegmentId"],
                    item["fromStepId"], item["toStepId"],
                    self._dump(item["compatibility"]), now,
                ),
            )

    @staticmethod
    def _checkpoint_value(value: Any) -> str | None:
        return None if value is None else WorkflowStore._dump(value)

    def list_plans(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT r.route_id, r.flow_id, r.name, r.head_attempt_id,
                       r.step_order_json, r.updated_at,
                       (SELECT COUNT(*) FROM attempts a WHERE a.route_id = r.route_id) AS attempt_count
                FROM routes r ORDER BY r.updated_at DESC
                """
            ).fetchall()
        return [
            {
                "routeId": row["route_id"],
                "flowId": row["flow_id"],
                "name": row["name"],
                "headAttemptId": row["head_attempt_id"],
                "stepCount": len(self._load(row["step_order_json"])),
                "attemptCount": row["attempt_count"],
                "updatedAt": row["updated_at"],
            }
            for row in rows
        ]

    def get_plan(self, route_id: str) -> RoutePlanV1:
        with self._connect() as connection:
            route = connection.execute("SELECT * FROM routes WHERE route_id = ?", (route_id,)).fetchone()
            if route is None:
                raise WorkflowNotFound(f"route {route_id!r} does not exist")
            attempts = connection.execute(
                "SELECT * FROM attempts WHERE route_id = ? ORDER BY created_at, attempt_id",
                (route_id,),
            ).fetchall()
            segments = connection.execute(
                "SELECT * FROM segments WHERE attempt_id IN (SELECT attempt_id FROM attempts WHERE route_id = ?) ORDER BY created_at, segment_id",
                (route_id,),
            ).fetchall()
            reuse = connection.execute(
                "SELECT * FROM reuse WHERE attempt_id IN (SELECT attempt_id FROM attempts WHERE route_id = ?) ORDER BY created_at, reuse_id",
                (route_id,),
            ).fetchall()
        return RoutePlanV1.model_validate(
            {
                "route": {
                    "routeId": route["route_id"],
                    "flowId": route["flow_id"],
                    "name": route["name"],
                    "headAttemptId": route["head_attempt_id"],
                },
                "stepOrder": self._load(route["step_order_json"]),
                "attempts": [self._attempt_payload(row) for row in attempts],
                "segments": [self._segment_payload(row) for row in segments],
                "reuse": [self._reuse_payload(row) for row in reuse],
            }
        )

    def find_attempt_by_submission_key(self, submission_key: str) -> AttemptV1 | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM attempts WHERE submission_key = ?",
                (submission_key,),
            ).fetchone()
        return None if row is None else AttemptV1.model_validate(self._attempt_payload(row))

    def get_attempt_request_hash(self, attempt_id: str) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT request_hash FROM attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
        if row is None:
            raise WorkflowNotFound(f"attempt {attempt_id!r} does not exist")
        return str(row["request_hash"])

    def append_attempt(
        self,
        route_id: str,
        attempt: AttemptV1,
        segments: list[SegmentV1],
        reuse: list[SegmentReuseV1],
    ) -> RoutePlanV1:
        request_hash = sha256_hex(
            {
                "attempt": attempt.model_dump(mode="json", exclude_none=True),
                "segments": [segment.model_dump(mode="json", exclude_none=True) for segment in segments],
                "reuse": [item.model_dump(mode="json", exclude_none=True) for item in reuse],
            }
        )
        existing = self.find_attempt_by_submission_key(attempt.submissionKey)
        if existing is not None:
            if existing.routeId != route_id or existing.attemptId != attempt.attemptId:
                raise WorkflowConflict("submissionKey already belongs to a different attempt")
            if self.get_attempt_request_hash(existing.attemptId) != request_hash:
                raise WorkflowConflict("submissionKey already exists with different request content")
            return self.get_plan(route_id)

        if any(segment.attemptId != attempt.attemptId for segment in segments):
            raise WorkflowConflict("new segments must belong to the new attempt")
        if any(item.attemptId != attempt.attemptId for item in reuse):
            raise WorkflowConflict("reuse entries must belong to the new attempt")

        plan = self.get_plan(route_id)
        payload = plan.model_dump(mode="json", exclude_none=True)
        payload["route"]["headAttemptId"] = attempt.attemptId
        payload["attempts"].append(attempt.model_dump(mode="json", exclude_none=True))
        payload["segments"].extend(segment.model_dump(mode="json", exclude_none=True) for segment in segments)
        payload["reuse"].extend(item.model_dump(mode="json", exclude_none=True) for item in reuse)
        updated = RoutePlanV1.model_validate(payload)
        self._verify_plan_compiles(updated)

        now = self._now()
        try:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE routes SET head_attempt_id = ?, updated_at = ? WHERE route_id = ?",
                    (attempt.attemptId, now, route_id),
                )
                self._insert_attempts(
                    connection,
                    [attempt.model_dump(mode="json", exclude_none=True)],
                    now,
                    request_hashes={attempt.attemptId: request_hash},
                )
                self._insert_segments(connection, [segment.model_dump(mode="json", exclude_none=True) for segment in segments], now)
                self._insert_reuse(connection, [item.model_dump(mode="json", exclude_none=True) for item in reuse], now)
        except sqlite3.IntegrityError as exc:
            raise WorkflowConflict(f"attempt conflicts with an existing workflow object: {exc}") from exc
        return updated

    def get_attempt(self, attempt_id: str) -> AttemptV1:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM attempts WHERE attempt_id = ?", (attempt_id,)).fetchone()
        if row is None:
            raise WorkflowNotFound(f"attempt {attempt_id!r} does not exist")
        return AttemptV1.model_validate(self._attempt_payload(row))

    def get_attempt_detail(self, attempt_id: str) -> dict[str, Any]:
        attempt = self.get_attempt(attempt_id)
        with self._connect() as connection:
            segments = connection.execute(
                "SELECT * FROM segments WHERE attempt_id = ? ORDER BY created_at, segment_id",
                (attempt_id,),
            ).fetchall()
            reuse = connection.execute(
                "SELECT * FROM reuse WHERE attempt_id = ? ORDER BY created_at, reuse_id",
                (attempt_id,),
            ).fetchall()
        return {
            "attempt": attempt,
            "segments": [SegmentV1.model_validate(self._segment_payload(row)) for row in segments],
            "reuse": [SegmentReuseV1.model_validate(self._reuse_payload(row)) for row in reuse],
        }

    def get_segment(self, segment_id: str) -> SegmentV1:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM segments WHERE segment_id = ?", (segment_id,)).fetchone()
        if row is None:
            raise WorkflowNotFound(f"segment {segment_id!r} does not exist")
        return SegmentV1.model_validate(self._segment_payload(row))

    def update_attempt_status(self, attempt_id: str, status: str) -> AttemptV1:
        attempt = self.get_attempt(attempt_id)
        allowed = {
            "planned": {"running", "cancelled", "failed", "interrupted"},
            "running": {"succeeded", "failed", "cancelled", "interrupted"},
        }
        if status not in allowed.get(attempt.status, set()):
            raise WorkflowConflict(f"attempt {attempt_id!r} cannot move from {attempt.status!r} to {status!r}")
        with self._connect() as connection:
            connection.execute(
                "UPDATE attempts SET status = ?, updated_at = ? WHERE attempt_id = ?",
                (status, self._now(), attempt_id),
            )
        return self.get_attempt(attempt_id)

    def update_segment_status(self, segment_id: str, status: str) -> SegmentV1:
        segment = self.get_segment(segment_id)
        allowed = {
            "planned": {"running", "cancelled", "failed", "interrupted"},
            "running": {"succeeded", "failed", "cancelled", "interrupted"},
        }
        if status not in allowed.get(segment.status, set()):
            raise WorkflowConflict(f"segment {segment_id!r} cannot move from {segment.status!r} to {status!r}")
        with self._connect() as connection:
            connection.execute(
                "UPDATE segments SET status = ?, updated_at = ? WHERE segment_id = ?",
                (status, self._now(), segment_id),
            )
        return self.get_segment(segment_id)

    def materialize(self, route_id: str, attempt_id: str) -> list[SegmentRefV1]:
        plan = self.get_plan(route_id)
        return materialize_attempt(plan, attempt_id)
