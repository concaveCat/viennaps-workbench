"""SQLite persistence for P0-2 route plans and execution branches."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .binding import verify_binding
from .hashing import full_project_hash
from .ids import sha256_hex
from .metrology import (
    MeasurementResultV1,
    MetrologyRecipeV1,
    MetricDefinitionV1,
    measure_snapshot,
)
from ..models import parse_project
from .models import CompiledWorkflowV1
from .provenance import VerificationRecordV1
from .routes import (
    AttemptV1,
    RoutePlanV1,
    SegmentRefV1,
    SegmentReuseV1,
    SegmentV1,
    materialize_attempt,
)
from .verification import prefix_compatibility, verify_route_plan_compiles


SCHEMA_VERSION = "4"


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
        if self.path.exists():
            # Read-only preflight: an unknown future schema must be rejected
            # before this process creates any tables in the database.
            with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as preflight:
                table = preflight.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'schema_meta'"
                ).fetchone()
                if table is not None:
                    row = preflight.execute(
                        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
                    ).fetchone()
                    if row is not None and row[0] not in {"1", "2", "3", SCHEMA_VERSION}:
                        raise RuntimeError(
                            f"unsupported workflow schema version {row[0]!r}; "
                            f"expected {SCHEMA_VERSION!r}"
                        )
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
                    legacy_unverified INTEGER NOT NULL DEFAULT 0,
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
                    legacy_compatibility_json TEXT,
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
                    legacy_compatibility_json TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS metrics (
                    metric_id TEXT PRIMARY KEY,
                    definition_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS metrology_recipes (
                    recipe_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    recipe_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (recipe_id, version)
                );
                CREATE TABLE IF NOT EXISTS measurements (
                    measurement_id TEXT PRIMARY KEY,
                    metric_id TEXT NOT NULL,
                    recipe_id TEXT NOT NULL,
                    recipe_version INTEGER NOT NULL,
                    method TEXT NOT NULL,
                    algorithm_version TEXT NOT NULL,
                    source_run_id TEXT,
                    source_step_id TEXT,
                    source_state_id TEXT,
                    source_snapshot_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    value REAL,
                    unit TEXT NOT NULL,
                    reason TEXT,
                    intersections_json TEXT NOT NULL,
                    material TEXT NOT NULL,
                    region_json TEXT,
                    coordinate_system TEXT NOT NULL,
                    uncertainty REAL,
                    uncertainty_status TEXT NOT NULL,
                    binding_status TEXT NOT NULL DEFAULT 'unbound',
                    binding_evidence_json TEXT,
                    equivalence_status TEXT NOT NULL DEFAULT 'not_evaluated',
                    equivalence_evidence_json TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS attempts_route_idx ON attempts(route_id);
                CREATE INDEX IF NOT EXISTS segments_attempt_idx ON segments(attempt_id);
                CREATE INDEX IF NOT EXISTS reuse_attempt_idx ON reuse(attempt_id);
                CREATE TABLE IF NOT EXISTS measurement_verifications (
                    verification_id TEXT PRIMARY KEY,
                    measurement_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    method_version TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS measurements_run_idx ON measurements(source_run_id);
                CREATE INDEX IF NOT EXISTS measurements_recipe_idx ON measurements(recipe_id, recipe_version);
                CREATE INDEX IF NOT EXISTS measurement_verifications_idx ON measurement_verifications(measurement_id);
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            route_columns = {row[1] for row in connection.execute("PRAGMA table_info(routes)")}
            if "legacy_unverified" not in route_columns:
                connection.execute("ALTER TABLE routes ADD COLUMN legacy_unverified INTEGER NOT NULL DEFAULT 0")
            attempt_columns = {row[1] for row in connection.execute("PRAGMA table_info(attempts)")}
            if "request_hash" not in attempt_columns:
                connection.execute("ALTER TABLE attempts ADD COLUMN request_hash TEXT NOT NULL DEFAULT ''")
            if "legacy_compatibility_json" not in attempt_columns:
                connection.execute("ALTER TABLE attempts ADD COLUMN legacy_compatibility_json TEXT")
            reuse_columns = {row[1] for row in connection.execute("PRAGMA table_info(reuse)")}
            if "legacy_compatibility_json" not in reuse_columns:
                connection.execute("ALTER TABLE reuse ADD COLUMN legacy_compatibility_json TEXT")
            measurement_columns = {row[1] for row in connection.execute("PRAGMA table_info(measurements)")}
            if "binding_status" not in measurement_columns:
                connection.execute("ALTER TABLE measurements ADD COLUMN binding_status TEXT NOT NULL DEFAULT 'unbound'")
            if "binding_evidence_json" not in measurement_columns:
                connection.execute("ALTER TABLE measurements ADD COLUMN binding_evidence_json TEXT")
            if "equivalence_status" not in measurement_columns:
                connection.execute("ALTER TABLE measurements ADD COLUMN equivalence_status TEXT NOT NULL DEFAULT 'not_evaluated'")
            if "equivalence_evidence_json" not in measurement_columns:
                connection.execute("ALTER TABLE measurements ADD COLUMN equivalence_evidence_json TEXT")

            version_row = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
            if version_row is None:
                connection.execute(
                    "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?)",
                    (SCHEMA_VERSION,),
                )
            elif version_row["value"] in {"1", "2"}:
                self._migrate_legacy_compatibility(connection)
                connection.execute(
                    "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
                    (SCHEMA_VERSION,),
                )
            elif version_row["value"] == "3":
                connection.execute(
                    "UPDATE schema_meta SET value = ? WHERE key = 'schema_version'",
                    (SCHEMA_VERSION,),
                )
            elif version_row["value"] != SCHEMA_VERSION:
                raise RuntimeError(
                    f"unsupported workflow schema version {version_row['value']!r}; "
                    f"expected {SCHEMA_VERSION!r}"
                )

    def _migrate_legacy_compatibility(self, connection: sqlite3.Connection) -> None:
        """Repair v2 prefix summaries only after a full cross-compile check.

        A source and target can each be rebuilt from their own compile and still
        be incompatible with each other. The migrated RoutePlan is therefore
        assembled in memory and validated before any row is updated. If that
        fails, the original summaries are preserved and the whole route is
        marked legacy/unverified.
        """

        route_rows = connection.execute("SELECT * FROM routes").fetchall()
        for route in route_rows:
            route_id = route["route_id"]
            attempts = connection.execute(
                "SELECT * FROM attempts WHERE route_id = ?", (route_id,)
            ).fetchall()
            segments = connection.execute(
                "SELECT * FROM segments WHERE attempt_id IN (SELECT attempt_id FROM attempts WHERE route_id = ?)",
                (route_id,),
            ).fetchall()
            reuse = connection.execute(
                "SELECT * FROM reuse WHERE attempt_id IN (SELECT attempt_id FROM attempts WHERE route_id = ?)",
                (route_id,),
            ).fetchall()
            segments_by_id = {row["segment_id"]: row for row in segments}
            attempts_by_id = {row["attempt_id"]: row for row in attempts}
            compiles: dict[str, CompiledWorkflowV1] = {}
            for compile_id in {row["compile_id"] for row in attempts} | {row["source_compile_id"] for row in segments}:
                compile_row = connection.execute(
                    "SELECT envelope_json FROM compiles WHERE compile_id = ?",
                    (compile_id,),
                ).fetchone()
                if compile_row is None:
                    continue
                try:
                    compiles[compile_id] = CompiledWorkflowV1.model_validate(json.loads(compile_row["envelope_json"]))
                except (ValueError, TypeError, KeyError):
                    continue

            legacy = False

            def repair_attempt(row: sqlite3.Row) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
                nonlocal legacy
                raw = row["prefix_compatibility_json"]
                if raw is None:
                    return None, None
                old = json.loads(raw)
                if "solverDigest" in old:
                    return old, old
                compiled = compiles.get(row["compile_id"])
                if compiled is not None:
                    try:
                        candidate = prefix_compatibility(compiled, old["stepIds"], row["run_seed"]).model_dump(mode="json", exclude_none=True)
                        if all(
                            old.get(key) == candidate.get(key)
                            for key in ("geometryBindingHash", "seedPolicy", "runSeed", "stepIds", "resolvedStepDigest")
                        ):
                            return candidate, old
                    except (ValueError, KeyError, TypeError):
                        pass
                legacy = True
                placeholder = dict(old)
                placeholder.setdefault("prefixDigest", "0" * 64)
                placeholder["solverDigest"] = "0" * 64
                return placeholder, old

            def repair_reuse(item: sqlite3.Row) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
                nonlocal legacy
                raw = item["compatibility_json"]
                if raw is None:
                    return None, None
                old = json.loads(raw)
                if "solverDigest" in old:
                    return old, old
                source = segments_by_id.get(item["reused_from_segment_id"])
                source_attempt = None if source is None else attempts_by_id.get(source["attempt_id"])
                if source is not None and source_attempt is not None:
                    compiled = compiles.get(source["source_compile_id"])
                    if compiled is not None:
                        try:
                            candidate = prefix_compatibility(compiled, old["stepIds"], source_attempt["run_seed"]).model_dump(mode="json", exclude_none=True)
                            if all(
                                old.get(key) == candidate.get(key)
                                for key in ("geometryBindingHash", "seedPolicy", "runSeed", "stepIds", "resolvedStepDigest")
                            ):
                                return candidate, old
                        except (ValueError, KeyError, TypeError):
                            pass
                legacy = True
                placeholder = dict(old)
                placeholder.setdefault("prefixDigest", "0" * 64)
                placeholder["solverDigest"] = "0" * 64
                return placeholder, old

            attempt_candidates = {row["attempt_id"]: repair_attempt(row) for row in attempts}
            reuse_candidates = {item["reuse_id"]: repair_reuse(item) for item in reuse}

            def payload(*, legacy_flag: bool) -> dict[str, Any]:
                attempt_payloads = []
                for row in attempts:
                    item = self._attempt_payload(row)
                    candidate, original = attempt_candidates[row["attempt_id"]]
                    item["prefixCompatibility"] = candidate
                    item["legacyCompatibility"] = original if legacy_flag else None
                    attempt_payloads.append(item)
                reuse_payloads = []
                for item_row in reuse:
                    item = self._reuse_payload(item_row)
                    candidate, original = reuse_candidates[item_row["reuse_id"]]
                    item["compatibility"] = candidate
                    item["legacyCompatibility"] = original if legacy_flag else None
                    reuse_payloads.append(item)
                return {
                    "route": {
                        "routeId": route["route_id"],
                        "flowId": route["flow_id"],
                        "name": route["name"],
                        "headAttemptId": route["head_attempt_id"],
                        "legacyUnverified": legacy_flag,
                    },
                    "stepOrder": json.loads(route["step_order_json"]),
                    "attempts": attempt_payloads,
                    "segments": [self._segment_payload(row) for row in segments],
                    "reuse": reuse_payloads,
                }

            verified_payload = payload(legacy_flag=False)
            try:
                migrated = RoutePlanV1.model_validate(verified_payload)
                verify_route_plan_compiles(migrated, compiles)
            except (ValueError, KeyError, TypeError):
                legacy = True
            final_payload = payload(legacy_flag=legacy)
            try:
                RoutePlanV1.model_validate(final_payload)
            except (ValueError, KeyError, TypeError) as exc:
                raise RuntimeError(f"legacy route {route_id!r} cannot be reconstructed safely: {exc}") from exc

            for row in attempts:
                candidate, original = attempt_candidates[row["attempt_id"]]
                connection.execute(
                    "UPDATE attempts SET prefix_compatibility_json = ?, legacy_compatibility_json = ? WHERE attempt_id = ?",
                    (
                        None if candidate is None else self._dump(candidate),
                        None if not legacy or original is None else self._dump(original),
                        row["attempt_id"],
                    ),
                )
            for item in reuse:
                candidate, original = reuse_candidates[item["reuse_id"]]
                connection.execute(
                    "UPDATE reuse SET compatibility_json = ?, legacy_compatibility_json = ? WHERE reuse_id = ?",
                    (
                        None if candidate is None else self._dump(candidate),
                        None if not legacy or original is None else self._dump(original),
                        item["reuse_id"],
                    ),
                )
            connection.execute(
                "UPDATE routes SET legacy_unverified = ? WHERE route_id = ?",
                (1 if legacy else 0, route_id),
            )

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
            "legacyCompatibility": WorkflowStore._load(row["legacy_compatibility_json"]),
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
            "legacyCompatibility": WorkflowStore._load(row["legacy_compatibility_json"]),
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

    def schema_version(self) -> str:
        with self._connect() as connection:
            row = connection.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
        return "" if row is None else str(row["value"])

    def register_metric(self, metric: MetricDefinitionV1) -> MetricDefinitionV1:
        payload = metric.model_dump(mode="json", exclude_none=True)
        digest = sha256_hex(payload)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT definition_json FROM metrics WHERE metric_id = ?",
                (metric.metricId,),
            ).fetchone()
            if existing is not None:
                if sha256_hex(json.loads(existing["definition_json"])) != digest:
                    raise WorkflowConflict("metricId already exists with different content")
                return metric
            connection.execute(
                "INSERT INTO metrics (metric_id, definition_json, created_at) VALUES (?, ?, ?)",
                (metric.metricId, self._dump(payload), self._now()),
            )
        return metric

    def get_metric(self, metric_id: str) -> MetricDefinitionV1:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT definition_json FROM metrics WHERE metric_id = ?",
                (metric_id,),
            ).fetchone()
        if row is None:
            raise WorkflowNotFound(f"metric {metric_id!r} does not exist")
        return MetricDefinitionV1.model_validate(json.loads(row["definition_json"]))

    def register_recipe(self, recipe: MetrologyRecipeV1) -> MetrologyRecipeV1:
        payload = recipe.model_dump(mode="json", exclude_none=True)
        digest = sha256_hex(payload)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT recipe_json FROM metrology_recipes WHERE recipe_id = ? AND version = ?",
                (recipe.recipeId, recipe.version),
            ).fetchone()
            if existing is not None:
                if sha256_hex(json.loads(existing["recipe_json"])) != digest:
                    raise WorkflowConflict("recipeId/version already exists with different content")
                return recipe
            connection.execute(
                "INSERT INTO metrology_recipes (recipe_id, version, recipe_json, created_at) VALUES (?, ?, ?, ?)",
                (recipe.recipeId, recipe.version, self._dump(payload), self._now()),
            )
        return recipe

    def get_recipe(self, recipe_id: str, version: int | None = None) -> MetrologyRecipeV1:
        with self._connect() as connection:
            if version is None:
                row = connection.execute(
                    "SELECT recipe_json FROM metrology_recipes WHERE recipe_id = ? ORDER BY version DESC LIMIT 1",
                    (recipe_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT recipe_json FROM metrology_recipes WHERE recipe_id = ? AND version = ?",
                    (recipe_id, version),
                ).fetchone()
        if row is None:
            raise WorkflowNotFound(f"metrology recipe {recipe_id!r} does not exist")
        return MetrologyRecipeV1.model_validate(json.loads(row["recipe_json"]))

    def list_recipes(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT recipe_id, version, recipe_json, created_at FROM metrology_recipes ORDER BY recipe_id, version DESC"
            ).fetchall()
        result = []
        for row in rows:
            recipe = MetrologyRecipeV1.model_validate(json.loads(row["recipe_json"]))
            result.append(
                {
                    "recipeId": recipe.recipeId,
                    "version": recipe.version,
                    "metricId": recipe.metricId,
                    "method": recipe.method,
                    "name": recipe.name,
                    "createdAt": row["created_at"],
                }
            )
        return result

    @staticmethod
    def _measurement_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "measurementId": row["measurement_id"],
            "metricId": row["metric_id"],
            "recipeId": row["recipe_id"],
            "recipeVersion": row["recipe_version"],
            "method": row["method"],
            "algorithmVersion": row["algorithm_version"],
            "sourceRunId": row["source_run_id"],
            "sourceStepId": row["source_step_id"],
            "sourceStateId": row["source_state_id"],
            "sourceSnapshotHash": row["source_snapshot_hash"],
            "status": row["status"],
            "value": row["value"],
            "unit": row["unit"],
            "reason": row["reason"],
            "intersections": json.loads(row["intersections_json"]),
            "material": row["material"],
            "region": None if row["region_json"] is None else json.loads(row["region_json"]),
            "coordinateSystem": row["coordinate_system"],
            "uncertainty": row["uncertainty"],
            "uncertaintyStatus": row["uncertainty_status"],
            "bindingStatus": row["binding_status"],
            "bindingEvidence": None if row["binding_evidence_json"] is None else json.loads(row["binding_evidence_json"]),
            "equivalenceStatus": row["equivalence_status"],
            "equivalenceEvidence": None if row["equivalence_evidence_json"] is None else json.loads(row["equivalence_evidence_json"]),
            "createdAt": row["created_at"],
        }

    def record_measurement(self, result: MeasurementResultV1) -> MeasurementResultV1:
        payload = result.model_dump(mode="json", exclude_none=True)
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO measurements (
                        measurement_id, metric_id, recipe_id, recipe_version, method,
                        algorithm_version, source_run_id, source_step_id, source_state_id,
                        source_snapshot_hash, status, value, unit, reason,
                        intersections_json, material, region_json, coordinate_system,
                        uncertainty, uncertainty_status, binding_status, binding_evidence_json,
                        equivalence_status, equivalence_evidence_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        result.measurementId, result.metricId, result.recipeId, result.recipeVersion,
                        result.method, result.algorithmVersion, result.sourceRunId, result.sourceStepId,
                        result.sourceStateId, result.sourceSnapshotHash, result.status, result.value,
                        result.unit, result.reason, self._dump(result.intersections), result.material,
                        None if result.region is None else self._dump(result.region.model_dump(mode="json")),
                        result.coordinateSystem, result.uncertainty, result.uncertaintyStatus,
                        result.bindingStatus,
                        None if result.bindingEvidence is None else self._dump(result.bindingEvidence.model_dump(mode="json", exclude_none=True)),
                        result.equivalenceStatus,
                        None if result.equivalenceEvidence is None else self._dump(result.equivalenceEvidence.model_dump(mode="json", exclude_none=True)),
                        result.createdAt or self._now(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise WorkflowConflict(f"measurement conflicts with an existing record: {exc}") from exc
        return self.get_measurement(result.measurementId)

    def get_measurement(self, measurement_id: str) -> MeasurementResultV1:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM measurements WHERE measurement_id = ?",
                (measurement_id,),
            ).fetchone()
        if row is None:
            raise WorkflowNotFound(f"measurement {measurement_id!r} does not exist")
        return MeasurementResultV1.model_validate(self._measurement_payload(row))

    def list_measurements(
        self,
        *,
        run_id: str | None = None,
        recipe_id: str | None = None,
        binding_status: str | None = None,
    ) -> list[MeasurementResultV1]:
        clauses = []
        params: list[Any] = []
        if run_id is not None:
            clauses.append("source_run_id = ?")
            params.append(run_id)
        if recipe_id is not None:
            clauses.append("recipe_id = ?")
            params.append(recipe_id)
        if binding_status is not None:
            clauses.append("binding_status = ?")
            params.append(binding_status)
        query = "SELECT * FROM measurements"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC, measurement_id"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [MeasurementResultV1.model_validate(self._measurement_payload(row)) for row in rows]

    def measure_and_record(
        self,
        recipe: MetrologyRecipeV1,
        metric: MetricDefinitionV1,
        snapshot: dict[str, Any],
        *,
        source_run_id: str | None = None,
        source_step_id: str | None = None,
        source_state_id: str | None = None,
    ) -> MeasurementResultV1:
        result = measure_snapshot(
            recipe,
            metric,
            snapshot,
            source_run_id=source_run_id,
            source_step_id=source_step_id,
            source_state_id=source_state_id,
        )
        return self.record_measurement(result)

    def measure_run(self, recipe: MetrologyRecipeV1, metric: MetricDefinitionV1, run_id: str, step_id: str) -> MeasurementResultV1:
        from .. import snapshots, storage

        try:
            run_dir = storage.run_path(run_id)
            manifest = storage.read_json(run_dir / "manifest.json")
            row = next((item for item in manifest.get("snapshots", []) if item.get("stepId") == step_id), None)
            if row is None:
                raise WorkflowNotFound(f"snapshot {step_id!r} does not exist in run {run_id!r}")
            snapshot_path = snapshots.ensure_materialized(run_dir, step_id)
            snapshot = storage.read_json(snapshot_path)
            state_id = (row.get("nativeState") or {}).get("stateId")
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise WorkflowNotFound(f"historical snapshot is unavailable: {exc}") from exc
        return self.measure_and_record(
            recipe,
            metric,
            snapshot,
            source_run_id=run_id,
            source_step_id=step_id,
            source_state_id=state_id,
        )

    @staticmethod
    def _verification_payload(row: sqlite3.Row) -> dict[str, Any]:
        evidence = json.loads(row["evidence_json"])
        return {
            "verificationId": row["verification_id"],
            "measurementId": row["measurement_id"],
            "status": row["status"],
            "methodVersion": row["method_version"],
            "createdAt": row["created_at"],
            "evidence": evidence,
        }

    def list_verifications(self, measurement_id: str) -> list[VerificationRecordV1]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM measurement_verifications WHERE measurement_id = ? ORDER BY rowid",
                (measurement_id,),
            ).fetchall()
        return [VerificationRecordV1.model_validate(self._verification_payload(row)) for row in rows]

    def _record_verification(self, measurement_id: str, evidence, now: str) -> VerificationRecordV1:
        verification_id = f"v_{uuid.uuid4().hex}"
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO measurement_verifications (
                    verification_id, measurement_id, status, method_version, evidence_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    verification_id,
                    measurement_id,
                    "verified" if evidence.binding_verified else "failed",
                    evidence.methodVersion,
                    self._dump(evidence.model_dump(mode="json", exclude_none=True)),
                    now,
                ),
            )
        return VerificationRecordV1(
            verificationId=verification_id,
            measurementId=measurement_id,
            status="verified" if evidence.binding_verified else "failed",
            methodVersion=evidence.methodVersion,
            createdAt=now,
            evidence=evidence,
        )

    def bind_measurement(self, measurement_id: str, segment_id: str, run_id: str, step_id: str) -> MeasurementResultV1:
        """Recompute execution/step/source evidence; keep failures explicit."""

        from .. import snapshots, storage

        measurement = self.get_measurement(measurement_id)
        segment = self.get_segment(segment_id)
        attempt = self.get_attempt(segment.attemptId)
        plan = self.get_plan(attempt.routeId)
        compile_record = self.get_compile(segment.sourceCompileId)

        manifest: dict[str, Any] | None = None
        snapshot_row: dict[str, Any] | None = None
        snapshot: dict[str, Any] | None = None
        state_hash: str | None = None
        submitted_project_hash: str | None = None
        submitted_project = None
        try:
            run_dir = storage.run_path(run_id)
            manifest = storage.read_json(run_dir / "manifest.json")
            snapshot_row = next(
                (item for item in manifest.get("snapshots", []) if item.get("stepId") == step_id),
                None,
            )
            if snapshot_row is not None:
                snapshot_path = snapshots.ensure_materialized(run_dir, step_id)
                snapshot = storage.read_json(snapshot_path)
                state = snapshot_row.get("nativeState") or {}
                if state:
                    state_path = storage.resolve_domain_state(state)
                    state_hash = storage.file_sha256(state_path)
            try:
                submitted_project = parse_project(manifest["normalizedProject"])
                submitted_project_hash = full_project_hash(submitted_project)
            except (KeyError, ValueError, TypeError):
                submitted_project = None
                submitted_project_hash = None
        except (FileNotFoundError, OSError, ValueError):
            pass

        evidence = verify_binding(
            measurement=measurement,
            plan=plan,
            attempt=attempt,
            segment=segment,
            compile_record=compile_record,
            manifest=manifest,
            snapshot_row=snapshot_row,
            snapshot=snapshot,
            state_hash=state_hash,
            submitted_project=submitted_project,
            submitted_project_hash=submitted_project_hash,
            run_id=run_id,
            step_id=step_id,
        )
        now = self._now()
        self._record_verification(measurement_id, evidence, now)
        status = "verified" if evidence.binding_verified else "failed"
        update_cache = True
        if status == "failed" and measurement.bindingStatus == "verified":
            current = measurement.bindingEvidence
            same_binding = (
                current is not None
                and current.execution.segmentId == segment_id
                and current.execution.runId == run_id
                and current.targetStep.stepId == step_id
            )
            update_cache = same_binding
        if update_cache:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE measurements SET binding_status = ?, binding_evidence_json = ? WHERE measurement_id = ?",
                    (status, self._dump(evidence.model_dump(mode="json", exclude_none=True)), measurement_id),
                )
        return self.get_measurement(measurement_id)

    def create_plan(self, plan: RoutePlanV1) -> RoutePlanV1:
        self._verify_plan_compiles(plan)
        now = self._now()
        payload = plan.model_dump(mode="json", exclude_none=True)
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO routes (route_id, flow_id, name, head_attempt_id, step_order_json, legacy_unverified, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (payload["route"]["routeId"], payload["route"]["flowId"], payload["route"]["name"],
                     payload["route"]["headAttemptId"], self._dump(payload["stepOrder"]),
                     1 if payload["route"].get("legacyUnverified", False) else 0, now, now),
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
                    run_seed, flow_digest, status, legacy_compatibility_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt["attemptId"], attempt["routeId"], attempt.get("parentAttemptId"),
                    attempt["compileId"], attempt["submissionKey"], request_hash,
                    self._checkpoint_value(attempt.get("baseCheckpointRef")),
                    attempt.get("startAfterStepId"), attempt["throughStepId"],
                    self._dump(attempt.get("changedStepIds", [])),
                    self._dump(attempt["prefixCompatibility"]) if attempt.get("prefixCompatibility") is not None else None,
                    attempt["seedPolicy"], attempt["runSeed"], attempt["flowDigest"],
                    attempt["status"], self._dump(attempt["legacyCompatibility"]) if attempt.get("legacyCompatibility") is not None else None,
                    now, now,
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
                    from_step_id, to_step_id, compatibility_json, legacy_compatibility_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["reuseId"], item["attemptId"], item["reusedFromSegmentId"],
                    item["fromStepId"], item["toStepId"],
                    self._dump(item["compatibility"]),
                    self._dump(item["legacyCompatibility"]) if item.get("legacyCompatibility") is not None else None,
                    now,
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
                       r.step_order_json, r.legacy_unverified, r.updated_at,
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
                "legacyUnverified": bool(row["legacy_unverified"]),
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
                    "legacyUnverified": bool(route["legacy_unverified"]),
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
