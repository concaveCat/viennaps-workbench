"""P1 manual experiment variants, replicates and serial execution."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from .adoption import adopt_project, first_route
from .execution import execute_segment
from .store import WorkflowConflict, WorkflowStore


def _apply_parameters(project: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
    clone = __import__("json").loads(__import__("json").dumps(project))
    for step in clone.get("steps", []):
        overrides = parameters.get(step.get("id"))
        if isinstance(overrides, dict):
            step.update(overrides)
    return clone


def _monitor_experiment(store: WorkflowStore, supervisor: Any, experiment_id: str) -> None:
    document = store.get_experiment(experiment_id)
    recipe = store.get_recipe(document["recipeId"], document["recipeVersion"])
    metric = store.get_metric(recipe.metricId)
    document["status"] = "running"
    store.save_experiment(experiment_id, document)
    for variant in document["variants"]:
        for replicate in variant["replicates"]:
            if replicate.get("status") not in {"planned", "failed"}:
                continue
            try:
                project = _apply_parameters(document["project"], variant.get("parameters") or {})
                project.setdefault("solver", {})["random"] = {"mode": "fixed", "seed": int(replicate["seed"])}
                flow, _binding, compiled = adopt_project(project, flow_id=document["flowId"])
                store.register_compile(compiled)
                plan = first_route(compiled, name=f"{document['name']} · v{variant['ordinal']}")
                store.create_plan(plan)
                segment = plan.segments[0]
                replicate.update({"status": "running", "routeId": plan.route.routeId, "segmentId": segment.segmentId})
                store.save_experiment(experiment_id, document)
                _segment, run_id = execute_segment(store, supervisor, segment.segmentId)
                replicate["runId"] = run_id
                for _ in range(1800):
                    current = store.get_segment(segment.segmentId)
                    if current.status in {"succeeded", "failed", "cancelled", "interrupted"}:
                        break
                    time.sleep(0.2)
                if current.status != "succeeded":
                    replicate["status"] = current.status
                    store.save_experiment(experiment_id, document)
                    continue
                measurement = store.measure_run(recipe, metric, run_id, segment.throughStepId)
                bound = store.bind_measurement(measurement.measurementId, segment.segmentId, run_id, segment.throughStepId)
                replicate.update({
                    "status": "succeeded",
                    "measurementId": bound.measurementId,
                    "value": bound.value,
                    "unit": bound.unit,
                    "measurementStatus": bound.status,
                    "bindingStatus": bound.bindingStatus,
                })
            except Exception as exc:
                replicate.update({"status": "failed", "reason": str(exc)})
            store.save_experiment(experiment_id, document)
    document["status"] = "completed" if all(
        row.get("status") == "succeeded" for variant in document["variants"] for row in variant["replicates"]
    ) else "failed"
    store.save_experiment(experiment_id, document)


def start_experiment(store: WorkflowStore, supervisor: Any, experiment_id: str) -> dict[str, Any]:
    document = store.get_experiment(experiment_id)
    thread = threading.Thread(target=_monitor_experiment, args=(store, supervisor, experiment_id), daemon=True)
    thread.start()
    return document


def create_experiment_document(
    *,
    name: str,
    project: dict[str, Any],
    flow_id: str,
    recipe_id: str,
    recipe_version: int,
    variants: list[dict[str, Any]],
) -> dict[str, Any]:
    experiment_id = f"exp_{uuid.uuid4().hex[:16]}"
    rows = []
    for ordinal, variant in enumerate(variants, start=1):
        replicates = []
        for index in range(int(variant.get("replicates", 1))):
            replicates.append({
                "replicateId": f"rep_{uuid.uuid4().hex[:12]}",
                "seed": int(variant.get("seed", 20261029)) + index,
                "status": "planned",
            })
        rows.append({
            "variantId": f"var_{uuid.uuid4().hex[:12]}",
            "ordinal": ordinal,
            "parameters": variant.get("parameters") or {},
            "replicates": replicates,
        })
    return {
        "schemaVersion": 1,
        "experimentId": experiment_id,
        "name": name,
        "flowId": flow_id,
        "project": project,
        "recipeId": recipe_id,
        "recipeVersion": recipe_version,
        "variants": rows,
        "status": "planned",
    }
