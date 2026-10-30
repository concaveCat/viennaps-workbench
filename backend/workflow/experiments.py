"""P1/P2 experiment documents, serial queue execution and metric aggregation."""

from __future__ import annotations

import csv
import io
import itertools
import json
import math
import statistics
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from .adoption import adopt_project, first_route
from .execution import execute_segment
from .store import WorkflowConflict, WorkflowNotFound, WorkflowStore

DEFAULT_ESTIMATED_SECONDS = 1.0
_ALLOWED_FACTORS = {
    "duration", "rateMagnitude", "stickingProbability", "sourceExponent",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _apply_parameters(project: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
    clone = json.loads(json.dumps(project))
    for step in clone.get("steps", []):
        overrides = parameters.get(step.get("id"))
        if isinstance(overrides, dict):
            step.update(overrides)
    return clone


def _replicates(document: dict[str, Any]):
    for variant in document.get("variants") or []:
        for replicate in variant.get("replicates") or []:
            yield variant, replicate


def _elapsed(replicate: dict[str, Any]) -> float | None:
    started, finished = replicate.get("startedAt"), replicate.get("finishedAt")
    if not started or not finished:
        return None
    try:
        return max(0.0, (datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds())
    except ValueError:
        return None


def _queue_summary(document: dict[str, Any]) -> dict[str, Any]:
    rows = list(_replicates(document))
    statuses = [str(replicate.get("status") or "planned") for _variant, replicate in rows]
    pending = sum(1 for status in statuses if status in {"planned", "queued"})
    running = sum(1 for status in statuses if status == "running")
    succeeded = sum(1 for status in statuses if status == "succeeded")
    failed = sum(1 for status in statuses if status == "failed")
    durations = [value for _variant, replicate in rows if (value := _elapsed(replicate)) is not None]
    average = statistics.fmean(durations) if durations else DEFAULT_ESTIMATED_SECONDS
    attempts = sum(int(replicate.get("attemptCount") or 0) for _variant, replicate in rows)
    return {
        "total": len(rows),
        "pending": pending,
        "running": running,
        "succeeded": succeeded,
        "failed": failed,
        "executionAttempts": attempts,
        "retries": sum(max(0, int(replicate.get("attemptCount") or 0) - 1) for _variant, replicate in rows),
        "estimatedSecondsRemaining": round((pending + running) * average, 3),
        "estimatedCostUsd": round((pending + running) * float(document.get("costPerRunUsd") or 0.0), 6),
        "averageRunSeconds": round(average, 6),
    }


def _aggregate(document: dict[str, Any]) -> dict[str, Any]:
    unit = document.get("unit") or "um"
    rows = []
    for variant in document.get("variants") or []:
        values: list[float] = []
        for replicate in variant.get("replicates") or []:
            if replicate.get("statisticalSample") and replicate.get("status") == "succeeded":
                value = replicate.get("value")
                if isinstance(value, (int, float)) and math.isfinite(float(value)):
                    values.append(float(value))
        count = len(values)
        mean = statistics.fmean(values) if count else None
        standard_deviation = statistics.stdev(values) if count >= 2 else (0.0 if count == 1 else None)
        ci95 = 1.96 * standard_deviation / math.sqrt(count) if count >= 2 and standard_deviation is not None else None
        rows.append({
            "variantId": variant.get("variantId"),
            "ordinal": variant.get("ordinal"),
            "parameters": variant.get("parameters") or {},
            "n": count,
            "mean": mean,
            "std": standard_deviation,
            "ci95": ci95,
            "min": min(values) if values else None,
            "max": max(values) if values else None,
            "unit": unit,
            "statisticalStatus": "evaluated" if count >= 2 else "not_evaluated",
        })
    return {
        "metricId": document.get("metricId"),
        "unit": unit,
        "rows": rows,
        "generatedAt": _now(),
    }


def _save(store: WorkflowStore, document: dict[str, Any], *, prefix: str = "") -> None:
    document["updatedAt"] = _now()
    document["queue"] = _queue_summary(document)
    document["aggregation"] = _aggregate(document)
    store.save_experiment(document["experimentId"], document)


def _execute_replicate(
    store: WorkflowStore,
    supervisor: Any,
    document: dict[str, Any],
    variant: dict[str, Any],
    replicate: dict[str, Any],
) -> None:
    replicate["attemptCount"] = int(replicate.get("attemptCount") or 0) + 1
    replicate["status"] = "running"
    replicate["startedAt"] = _now()
    replicate.pop("reason", None)
    _save(store, document)
    try:
        project = _apply_parameters(document["project"], variant.get("parameters") or {})
        project.setdefault("solver", {})["random"] = {"mode": "fixed", "seed": int(replicate["seed"])}
        _flow, _binding, compiled = adopt_project(project, flow_id=document["flowId"])
        store.register_compile(compiled)
        plan = first_route(compiled, name=f"{document['name']} · v{variant['ordinal']}")
        store.create_plan(plan)
        segment = plan.segments[0]
        replicate["routeId"] = plan.route.routeId
        replicate["segmentId"] = segment.segmentId
        _save(store, document)
        _segment, run_id = execute_segment(store, supervisor, segment.segmentId)
        replicate["runId"] = run_id
        for _ in range(1800):
            current = store.get_segment(segment.segmentId)
            if current.status in {"succeeded", "failed", "cancelled", "interrupted"}:
                break
            time.sleep(0.2)
        if current.status != "succeeded":
            raise RuntimeError(f"segment finished with {current.status}")
        recipe = store.get_recipe(document["recipeId"], document["recipeVersion"])
        metric = store.get_metric(recipe.metricId)
        measurement = store.measure_run(recipe, metric, run_id, segment.throughStepId)
        bound = store.bind_measurement(measurement.measurementId, segment.segmentId, run_id, segment.throughStepId)
        replicate.update({
            "status": "succeeded",
            "statisticalSample": True,
            "finishedAt": _now(),
            "measurementId": bound.measurementId,
            "value": bound.value,
            "unit": bound.unit,
            "measurementStatus": bound.status,
            "bindingStatus": bound.bindingStatus,
        })
        store.register_artifact_ref(run_id, "experiment", document["experimentId"], "experiment")
        store.register_artifact_ref(run_id, "experiment", f"{document['experimentId']}:{replicate['replicateId']}", "replicate")
    except Exception as exc:
        replicate["reason"] = str(exc)
        replicate["finishedAt"] = _now()
        max_attempts = max(1, int(document.get("maxAttempts") or 1))
        replicate["status"] = "queued" if int(replicate["attemptCount"]) < max_attempts else "failed"
    _save(store, document)


_ACTIVE: set[str] = set()
_ACTIVE_LOCK = threading.Lock()
_EXECUTION_LOCK = threading.Lock()


def _worker(store: WorkflowStore, supervisor: Any, experiment_id: str) -> None:
    try:
        document = store.get_experiment(experiment_id)
        document["status"] = "running"
        _save(store, document)
        while True:
            document = store.get_experiment(experiment_id)
            job = next(
                (
                    (variant, replicate)
                    for variant, replicate in _replicates(document)
                    if str(replicate.get("status") or "planned") in {"planned", "queued"}
                ),
                None,
            )
            if job is None:
                break
            with _EXECUTION_LOCK:
                _execute_replicate(store, supervisor, document, job[0], job[1])
        document = store.get_experiment(experiment_id)
        document["status"] = "completed" if all(
            replicate.get("status") == "succeeded"
            for _variant, replicate in _replicates(document)
        ) else "failed"
        _save(store, document)
    except Exception as exc:
        try:
            document = store.get_experiment(experiment_id)
            document["status"] = "failed"
            document["reason"] = str(exc)
            _save(store, document)
        except Exception:
            pass
    finally:
        with _ACTIVE_LOCK:
            _ACTIVE.discard(experiment_id)


def start_experiment(store: WorkflowStore, supervisor: Any, experiment_id: str) -> dict[str, Any]:
    document = store.get_experiment(experiment_id)
    with _ACTIVE_LOCK:
        if experiment_id in _ACTIVE:
            return document
        _ACTIVE.add(experiment_id)
    thread = threading.Thread(target=_worker, args=(store, supervisor, experiment_id), daemon=True)
    thread.start()
    return document


def resume_experiments(store: WorkflowStore, supervisor: Any) -> None:
    """Resume experiments that were queued/running when the server stopped."""

    for summary in store.list_experiments():
        try:
            document = store.get_experiment(summary["experimentId"])
        except WorkflowNotFound:
            continue
        running = [replicate for _variant, replicate in _replicates(document) if replicate.get("status") == "running"]
        if document.get("status") not in {"queued", "running"} and not running:
            continue
        changed = False
        for _variant, replicate in _replicates(document):
            if replicate.get("status") == "running":
                replicate["status"] = "queued"
                replicate["reason"] = "interrupted by server restart"
                changed = True
        document["status"] = "queued"
        if changed:
            _save(store, document)
        else:
            store.save_experiment(document["experimentId"], document)
        start_experiment(store, supervisor, document["experimentId"])


def _new_replicate(seed: int) -> dict[str, Any]:
    return {
        "replicateId": f"rep_{uuid.uuid4().hex[:12]}",
        "seed": int(seed),
        "status": "planned",
        "statisticalSample": False,
        "attemptCount": 0,
    }


def create_experiment_document(
    *,
    name: str,
    project: dict[str, Any],
    flow_id: str,
    recipe_id: str,
    recipe_version: int,
    variants: list[dict[str, Any]],
    metric_id: str = "metric",
    unit: str = "um",
    max_attempts: int = 1,
    cost_per_run_usd: float = 0.0,
) -> dict[str, Any]:
    experiment_id = f"exp_{uuid.uuid4().hex[:16]}"
    rows = []
    for ordinal, variant in enumerate(variants, start=1):
        replicates = [
            _new_replicate(int(variant.get("seed", 20261029)) + index)
            for index in range(int(variant.get("replicates", 1)))
        ]
        rows.append({
            "variantId": f"var_{uuid.uuid4().hex[:12]}",
            "ordinal": ordinal,
            "parameters": variant.get("parameters") or {},
            "replicates": replicates,
        })
    document = {
        "schemaVersion": 2,
        "kind": "manual",
        "experimentId": experiment_id,
        "name": name,
        "flowId": flow_id,
        "project": project,
        "recipeId": recipe_id,
        "recipeVersion": recipe_version,
        "metricId": metric_id,
        "unit": unit,
        "maxAttempts": max(1, int(max_attempts)),
        "costPerRunUsd": float(cost_per_run_usd),
        "variants": rows,
        "status": "planned",
        "createdAt": _now(),
        "updatedAt": _now(),
    }
    document["queue"] = _queue_summary(document)
    document["aggregation"] = _aggregate(document)
    return document


def create_doe_document(
    *,
    name: str,
    project: dict[str, Any],
    flow_id: str,
    recipe_id: str,
    recipe_version: int,
    factors: list[dict[str, Any]],
    replicates: int,
    seed: int = 20261029,
    metric_id: str = "metric",
    unit: str = "um",
    max_attempts: int = 2,
    cost_per_run_usd: float = 0.0,
) -> dict[str, Any]:
    step_by_id = {str(step.get("id")): step for step in project.get("steps") or []}
    validated: list[dict[str, Any]] = []
    for factor in factors:
        step_id = str(factor["stepId"])
        parameter = str(factor["parameter"])
        values = factor["values"]
        if step_id not in step_by_id:
            raise ValueError(f"DOE factor step {step_id!r} does not exist in the project")
        if parameter not in _ALLOWED_FACTORS:
            raise ValueError(f"DOE factor {parameter!r} is not a supported numeric step parameter")
        if parameter not in step_by_id[step_id]:
            raise ValueError(f"DOE factor step {step_id!r} has no {parameter!r}")
        normalized = [float(value) for value in values]
        if not normalized or any(not math.isfinite(value) for value in normalized):
            raise ValueError(f"DOE factor {parameter!r} values must be finite")
        validated.append({"stepId": step_id, "parameter": parameter, "values": normalized})
    combos = list(itertools.product(*[factor["values"] for factor in validated]))
    if len(combos) > 256:
        raise ValueError("DOE full factorial design exceeds the 256 condition limit")
    variants = []
    for ordinal, combo in enumerate(combos, start=1):
        parameters: dict[str, dict[str, float]] = {}
        for factor, value in zip(validated, combo):
            parameters.setdefault(factor["stepId"], {})[factor["parameter"]] = value
        variants.append({
            "variantId": f"var_{uuid.uuid4().hex[:12]}",
            "ordinal": ordinal,
            "parameters": parameters,
            "replicates": [_new_replicate(seed + (ordinal - 1) * 100 + index) for index in range(replicates)],
        })
    document = {
        "schemaVersion": 2,
        "kind": "doe",
        "experimentId": f"exp_{uuid.uuid4().hex[:16]}",
        "name": name,
        "flowId": flow_id,
        "project": project,
        "recipeId": recipe_id,
        "recipeVersion": recipe_version,
        "metricId": metric_id,
        "unit": unit,
        "maxAttempts": max(1, int(max_attempts)),
        "costPerRunUsd": float(cost_per_run_usd),
        "design": {
            "method": "full-factorial",
            "factors": validated,
            "replicates": int(replicates),
            "seed": int(seed),
            "conditionCount": len(combos),
            "logicalRunCount": len(combos) * int(replicates),
        },
        "variants": variants,
        "status": "planned",
        "createdAt": _now(),
        "updatedAt": _now(),
    }
    document["queue"] = _queue_summary(document)
    document["aggregation"] = _aggregate(document)
    return document


def experiment_table(
    document: dict[str, Any],
    *,
    min_value: float | None = None,
    max_value: float | None = None,
    sort: str = "ordinal",
    order: str = "asc",
) -> dict[str, Any]:
    rows = list((document.get("aggregation") or {}).get("rows") or [])
    if min_value is not None:
        rows = [row for row in rows if row.get("mean") is not None and float(row["mean"]) >= min_value]
    if max_value is not None:
        rows = [row for row in rows if row.get("mean") is not None and float(row["mean"]) <= max_value]
    key = sort if sort in {"ordinal", "mean", "std", "min", "max", "n"} else "ordinal"
    filtered = [row for row in rows if row.get(key) is not None]
    missing = [row for row in rows if row.get(key) is None]
    filtered.sort(key=lambda row: row[key], reverse=order == "desc")
    rows = filtered + missing
    for row in rows:
        variant = next(
            (item for item in document.get("variants") or [] if item.get("variantId") == row.get("variantId")),
            None,
        )
        row["replicates"] = [
            {
                "replicateId": replicate.get("replicateId"),
                "seed": replicate.get("seed"),
                "status": replicate.get("status"),
                "value": replicate.get("value"),
                "unit": replicate.get("unit"),
                "runId": replicate.get("runId"),
                "statisticalSample": bool(replicate.get("statisticalSample")),
                "attemptCount": int(replicate.get("attemptCount") or 0),
            }
            for replicate in (variant or {}).get("replicates") or []
        ]
    return {
        "experimentId": document.get("experimentId"),
        "name": document.get("name"),
        "status": document.get("status"),
        "metricId": document.get("metricId"),
        "unit": document.get("unit"),
        "queue": document.get("queue") or _queue_summary(document),
        "rows": rows,
    }


def experiment_csv(document: dict[str, Any]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "experiment_id", "variant_id", "ordinal", "parameters", "replicate_id", "seed",
        "status", "value", "unit", "run_id", "statistical_sample", "attempt_count",
        "n", "mean", "std", "ci95", "min", "max",
    ])
    table = experiment_table(document)
    by_variant = {row["variantId"]: row for row in table["rows"]}
    for variant in document.get("variants") or []:
        summary = by_variant.get(variant.get("variantId"), {})
        for replicate in variant.get("replicates") or []:
            writer.writerow([
                document.get("experimentId"), variant.get("variantId"), variant.get("ordinal"),
                json.dumps(variant.get("parameters") or {}, sort_keys=True, separators=(",", ":")),
                replicate.get("replicateId"), replicate.get("seed"), replicate.get("status"),
                replicate.get("value"), replicate.get("unit"), replicate.get("runId"),
                bool(replicate.get("statisticalSample")), int(replicate.get("attemptCount") or 0),
                summary.get("n"), summary.get("mean"), summary.get("std"), summary.get("ci95"),
                summary.get("min"), summary.get("max"),
            ])
    return buffer.getvalue()
