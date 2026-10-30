"""P2 full-factorial DOE, queue recovery, aggregation, table/CSV and retention."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import time
import unittest
import uuid

os.environ.setdefault("WORKBENCH_PORT", "8767")

from backend.main import app  # noqa: E402
from backend.workflow.api import get_store  # noqa: E402
from backend.workflow.experiments import resume_experiments  # noqa: E402
from tests.test_api_integration import asgi_request, json_body  # noqa: E402
from tests.test_workflow_adoption import project  # noqa: E402


def two_step_project() -> dict:
    base = project()
    first = dict(base["steps"][0])
    first.update({"rateMagnitude": 0.02, "duration": 0.02})
    second = dict(first)
    second.update({"id": "step_etch_02", "name": "second", "rateMagnitude": 0.01, "duration": 0.01})
    base["steps"] = [first, second]
    return base


class DoeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.lifespan = app.router.lifespan_context(app)
        await self.lifespan.__aenter__()
        suffix = uuid.uuid4().hex[:6]
        self.metric_id = f"metric_doe_{suffix}"
        self.recipe_id = f"recipe_doe_{suffix}"
        for path, payload in (
            ("/api/v1/workflow/metrics", {"schemaVersion": 1, "metricId": self.metric_id, "name": "depth", "unit": "um"}),
            ("/api/v1/workflow/metrology-recipes", {
                "schemaVersion": 1, "recipeId": self.recipe_id, "version": 1, "metricId": self.metric_id,
                "name": "depth", "dimension": 2, "method": "etch_depth",
                "parameters": {"kind": "etch_depth", "x": 0, "referenceHeight": 0}, "material": "Si",
            }),
        ):
            response = await asgi_request(path, method="POST", payload=payload)
            self.assertIn(response[0], (201, 409), response[2])

    async def asyncTearDown(self):
        await self.lifespan.__aexit__(None, None, None)

    async def _create_doe(self, *, replicates: int = 3) -> dict:
        payload = {
            "name": "full factorial", "project": two_step_project(), "flowId": "flow_doe_p2",
            "recipeId": self.recipe_id, "recipeVersion": 1,
            "factors": [
                {"stepId": "step_etch_01", "parameter": "rateMagnitude", "values": [0.01, 0.02, 0.03]},
                {"stepId": "step_etch_01", "parameter": "duration", "values": [0.02, 0.04, 0.06]},
                {"stepId": "step_etch_02", "parameter": "rateMagnitude", "values": [0.01, 0.02, 0.03]},
            ],
            "replicates": replicates, "seed": 5000, "maxAttempts": 2, "costPerRunUsd": 0.001,
        }
        response = await asgi_request("/api/v1/workflow/experiments/doe", method="POST", payload=payload)
        self.assertEqual(response[0], 201, response[2])
        return json_body(response)

    async def _wait(self, experiment_id: str, timeout: float = 240.0) -> dict:
        deadline = time.monotonic() + timeout
        document = {}
        while time.monotonic() < deadline:
            response = await asgi_request(f"/api/v1/workflow/experiments/{experiment_id}")
            document = json_body(response)
            if document.get("status") in {"completed", "failed"}:
                return document
            await asyncio.sleep(0.5)
        self.fail(f"experiment did not finish: {document.get('status')}")

    async def test_full_factorial_81_logical_runs_and_table_export_gc(self):
        document = await self._create_doe()
        self.assertEqual(document["design"]["conditionCount"], 27)
        self.assertEqual(document["design"]["logicalRunCount"], 81)
        self.assertEqual(len(document["variants"]), 27)
        self.assertTrue(all(len(variant["replicates"]) == 3 for variant in document["variants"]))
        self.assertEqual(document["queue"]["total"], 81)
        self.assertEqual(document["queue"]["pending"], 81)
        self.assertGreater(document["queue"]["estimatedSecondsRemaining"], 0)
        self.assertGreater(document["queue"]["estimatedCostUsd"], 0)
        seeds = [replicate["seed"] for variant in document["variants"] for replicate in variant["replicates"]]
        self.assertEqual(len(seeds), 81)
        self.assertEqual(len(set(seeds)), 81)

        started = await asgi_request(f"/api/v1/workflow/experiments/{document['experimentId']}/run", method="POST", payload={})
        self.assertEqual(started[0], 200, started[2])
        final = await self._wait(document["experimentId"])
        self.assertEqual(final["status"], "completed", final.get("reason"))
        self.assertEqual(final["queue"]["succeeded"], 81)
        self.assertEqual(final["queue"]["failed"], 0)
        self.assertEqual(final["queue"]["retries"], 0)
        self.assertEqual(len(final["aggregation"]["rows"]), 27)
        run_ids = []
        for variant in final["variants"]:
            self.assertEqual(len(variant["replicates"]), 3)
            for replicate in variant["replicates"]:
                self.assertEqual(replicate["status"], "succeeded", replicate)
                self.assertTrue(replicate["statisticalSample"])
                self.assertTrue(replicate["runId"])
                self.assertTrue(replicate["measurementId"])
                run_ids.append(replicate["runId"])
        for row in final["aggregation"]["rows"]:
            self.assertEqual(row["n"], 3)
            self.assertEqual(row["statisticalStatus"], "evaluated")
            self.assertIsNotNone(row["mean"])
            self.assertIsNotNone(row["std"])
            self.assertIsNotNone(row["ci95"])
            self.assertEqual(row["unit"], "um")

        table = await asgi_request(
            f"/api/v1/workflow/experiments/{document['experimentId']}/table?sort=mean&order=desc&min_value=0"
        )
        self.assertEqual(table[0], 200, table[2])
        table_body = json_body(table)
        means = [row["mean"] for row in table_body["rows"]]
        self.assertEqual(means, sorted(means, reverse=True))
        self.assertEqual(len(table_body["rows"]), 27)

        exported = await asgi_request(f"/api/v1/workflow/experiments/{document['experimentId']}/export.csv")
        self.assertEqual(exported[0], 200, exported[2])
        text = exported[2].decode("utf-8")
        rows = list(csv.reader(io.StringIO(text)))
        self.assertEqual(rows[0][:4], ["experiment_id", "variant_id", "ordinal", "parameters"])
        self.assertEqual(len(rows), 1 + 81)

        dry = await asgi_request("/api/v1/workflow/gc/dry-run", method="POST", payload={})
        self.assertEqual(dry[0], 200, dry[2])
        dry_body = json_body(dry)
        candidates = {item["artifactId"] for item in dry_body["candidates"]}
        self.assertTrue(set(run_ids).isdisjoint(candidates), "GC must not select experiment runs")
        self.assertEqual(len(set(run_ids)), 81)

    async def test_restart_resumes_queue_without_adding_statistical_samples(self):
        document = await self._create_doe(replicates=1)
        # Shrink the document to two pending logical runs and simulate a crash.
        experiment_id = document["experimentId"]
        store = get_store()
        doc = store.get_experiment(experiment_id)
        doc["variants"] = doc["variants"][:2]
        for variant in doc["variants"]:
            variant["replicates"] = variant["replicates"][:1]
        first = doc["variants"][0]["replicates"][0]
        first.update({"status": "running", "attemptCount": 1, "startedAt": "2026-01-01T00:00:00+00:00"})
        doc["variants"][1]["replicates"][0].update({"status": "planned", "attemptCount": 0})
        doc["status"] = "running"
        store.save_experiment(experiment_id, doc)

        resume_experiments(store, app.state.supervisor)
        final = await self._wait(experiment_id)
        self.assertEqual(final["status"], "completed", final.get("reason"))
        self.assertEqual(final["queue"]["succeeded"], 2)
        first = final["variants"][0]["replicates"][0]
        self.assertEqual(first["status"], "succeeded")
        self.assertEqual(first["attemptCount"], 2, "interrupted attempt must be retried")
        self.assertTrue(first["statisticalSample"])
        self.assertEqual(final["queue"]["retries"], 1)
        self.assertEqual(final["aggregation"]["rows"][0]["n"], 1, "a retry must not become a second sample")
        # Recovery must not restart an already completed experiment.
        resume_experiments(store, app.state.supervisor)
        after = store.get_experiment(experiment_id)
        self.assertEqual(after["status"], "completed")


if __name__ == "__main__":
    unittest.main()
