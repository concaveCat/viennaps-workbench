"""P4-2 measurement-driven branch decisions, replay and error boundaries."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import unittest
import uuid

os.environ.setdefault("WORKBENCH_PORT", "8767")

from backend import storage  # noqa: E402
from backend.main import app  # noqa: E402
from backend.workflow.store import SCHEMA_VERSION, WorkflowStore  # noqa: E402
from tests.test_api_integration import asgi_request, json_body  # noqa: E402


def three_step_project(flow_id: str) -> dict:
    return {
        "schemaVersion": 7,
        "name": "decision 2D",
        "dimension": 2,
        "units": {"length": "um", "time": "s", "rate": "um/s", "angle": "deg"},
        "domain": {"gridDelta": 0.1, "xExtent": 2.0, "xBoundary": "reflective"},
        "geometry": {"kind": "plane", "height": 0.0, "substrateMaterial": "Si"},
        "steps": [
            {
                "id": "step_etch_01", "name": "first", "enabled": True, "operation": "etch",
                "model": "isotropic", "rateMagnitude": 0.1, "duration": 0.1, "rateMode": "uniform",
            },
            {
                "id": "step_etch_02", "name": "second", "enabled": True, "operation": "etch",
                "model": "isotropic", "rateMagnitude": 0.1, "duration": 0.1, "rateMode": "uniform",
            },
            {
                "id": "step_etch_03", "name": "tail", "enabled": True, "operation": "etch",
                "model": "isotropic", "rateMagnitude": 0.1, "duration": 0.1, "rateMode": "uniform",
            },
        ],
        "solver": {
            "timeStepRatio": 0.25, "raysPerPoint": 16, "cpuThreads": 1,
            "random": {"mode": "fixed", "seed": 20261030}, "enginePreference": "cpu",
            "timeoutSeconds": 30,
        },
        "view": {"panX": 0.0, "panH": 0.0, "zoom": 1.0, "showGrid": True},
    }


class MeasurementDecisionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.lifespan = app.router.lifespan_context(app)
        await self.lifespan.__aenter__()
        self.suffix = uuid.uuid4().hex[:6]
        self.flow_id = f"flow_dec_{self.suffix}"
        adopted = await asgi_request(
            "/api/v1/workflow/flows/from-project",
            method="POST",
            payload={"project": three_step_project(self.flow_id), "flowId": self.flow_id},
        )
        self.assertEqual(adopted[0], 201, adopted[2])
        self.adopted = json_body(adopted)
        self.compile_id = self.adopted["compileId"]
        source_map = self.adopted["compile"]["sourceMap"]
        self.step_ids = {
            entry["authoredStepId"]: entry["stepId"] for entry in source_map
        }
        self.prefix_step_id = self.step_ids["step_etch_02"]
        self.tail_step_id = self.step_ids["step_etch_03"]

        route = await asgi_request(
            "/api/v1/workflow/routes/from-compile",
            method="POST",
            payload={
                "compileId": self.compile_id,
                "name": "decision route",
                "splitAfterStepId": self.prefix_step_id,
            },
        )
        self.assertEqual(route[0], 201, route[2])
        self.route = json_body(route)["plan"]
        self.route_id = self.route["route"]["routeId"]
        self.prefix_segment = self.route["segments"][0]
        self.assertEqual(self.prefix_segment["throughStepId"], self.prefix_step_id)

        self.metric_id = f"metric_dec_{self.suffix}"
        self.recipe_id = f"recipe_dec_{self.suffix}"
        for path, payload in (
            (
                "/api/v1/workflow/metrics",
                {
                    "schemaVersion": 1, "metricId": self.metric_id,
                    "name": "decision depth", "unit": "um", "description": "",
                },
            ),
            (
                "/api/v1/workflow/metrology-recipes",
                {
                    "schemaVersion": 1, "recipeId": self.recipe_id, "version": 1,
                    "metricId": self.metric_id, "name": "decision depth",
                    "dimension": 2, "method": "etch_depth",
                    "parameters": {"kind": "etch_depth", "x": 0.0, "referenceHeight": 0.0},
                    "material": "Si",
                },
            ),
        ):
            response = await asgi_request(path, method="POST", payload=payload)
            self.assertIn(response[0], (201, 409), response[2])

        first = await self._execute(self.prefix_segment["segmentId"])
        self.assertEqual(first["status"], "succeeded", first)

    async def asyncTearDown(self) -> None:
        await self.lifespan.__aexit__(None, None, None)

    async def _wait_segment(self, segment_id: str, timeout: float = 60.0) -> dict:
        deadline = asyncio.get_running_loop().time() + timeout
        body: dict = {}
        while asyncio.get_running_loop().time() < deadline:
            response = await asgi_request(f"/api/v1/workflow/segments/{segment_id}")
            self.assertEqual(response[0], 200, response[2])
            body = json_body(response)
            if body["status"] in {"succeeded", "failed", "cancelled", "interrupted"}:
                return body
            await asyncio.sleep(0.2)
        self.fail(f"segment {segment_id} did not finish: {body}")

    async def _execute(self, segment_id: str) -> dict:
        response = await asgi_request(
            f"/api/v1/workflow/segments/{segment_id}/execute", method="POST", payload={}
        )
        self.assertEqual(response[0], 200, response[2])
        return await self._wait_segment(segment_id)

    def _decision_payload(self, *, operator: str = "gt", threshold: float = 0.0,
                          true_parameters: dict, false_parameters: dict) -> dict:
        return {
            "startAfterStepId": self.prefix_step_id,
            "recipeId": self.recipe_id,
            "recipeVersion": 1,
            "operator": operator,
            "threshold": threshold,
            "branches": {
                "true": {"parameters": true_parameters},
                "false": {"parameters": false_parameters},
            },
        }

    async def _decide(self, **kwargs) -> dict:
        response = await asgi_request(
            f"/api/v1/workflow/routes/{self.route_id}/measurement-decisions",
            method="POST",
            payload=self._decision_payload(**kwargs),
        )
        self.assertEqual(response[0], 201, response[2])
        return json_body(response)

    async def _target_step(self, compile_id: str, authored_step_id: str) -> dict:
        response = await asgi_request(f"/api/v1/workflow/compiles/{compile_id}")
        self.assertEqual(response[0], 200, response[2])
        compiled = json_body(response)
        step_id = next(
            entry["stepId"]
            for entry in compiled["sourceMap"]
            if entry["authoredStepId"] == authored_step_id
        )
        return next(step for step in compiled["project"]["steps"] if step["id"] == step_id)

    async def test_measurement_selects_both_directions_and_records_overrides(self):
        true_body = await self._decide(
            operator="gt", threshold=0.0,
            true_parameters={"step_etch_03": {"duration": 0.03}},
            false_parameters={"step_etch_03": {"duration": 0.2}},
        )
        decision = true_body["decision"]
        self.assertEqual(decision["chosenBranch"], "true")
        self.assertTrue(decision["result"])
        self.assertEqual(decision["measurementId"], true_body["measurement"]["measurementId"])
        self.assertEqual(true_body["measurement"]["status"], "measured")
        self.assertAlmostEqual(decision["measuredValue"], true_body["measurement"]["value"])
        self.assertGreater(decision["measuredValue"], 0.0)
        self.assertRegex(decision["workflowRunId"], r"^wfr_")
        self.assertEqual(true_body["workflowRunId"], decision["workflowRunId"])
        self.assertEqual(
            true_body["resolvedParameters"],
            {"step_etch_03": {"duration": 0.03}},
        )
        self.assertEqual(decision["measurementBindingStatus"], "verified")
        self.assertIsNone(decision["replayOf"])

        target = await self._target_step(decision["targetCompileId"], "step_etch_03")
        self.assertAlmostEqual(target["duration"], 0.03)

        compile_response = await asgi_request(
            f"/api/v1/workflow/compiles/{decision['targetCompileId']}"
        )
        self.assertEqual(compile_response[0], 200, compile_response[2])
        record = json_body(compile_response)["compileRecord"]
        self.assertTrue(any(
            item.get("stepId") == "step_etch_03"
            and item.get("parameter") == "duration"
            and abs(float(item.get("value")) - 0.03) < 1e-12
            for item in record["overrides"]
        ))
        self.assertTrue(any(
            item.get("name") == "step_etch_03.duration"
            and abs(float(item.get("value")) - 0.03) < 1e-12
            for item in record["variableSet"]
        ))

        plan = true_body["plan"]
        attempt = next(
            item for item in plan["attempts"]
            if item["attemptId"] == decision["branchAttemptId"]
        )
        segment = next(
            item for item in plan["segments"]
            if item["segmentId"] == decision["branchSegmentId"]
        )
        self.assertEqual(attempt["compileId"], decision["targetCompileId"])
        self.assertEqual(segment["sourceCompileId"], decision["targetCompileId"])
        self.assertEqual(segment["startAfterStepId"], self.prefix_step_id)
        self.assertEqual(segment["status"], "planned")

        false_body = await self._decide(
            operator="gt", threshold=1.0,
            true_parameters={"step_etch_03": {"duration": 0.03}},
            false_parameters={"step_etch_03": {"duration": 0.2}},
        )
        false_decision = false_body["decision"]
        self.assertEqual(false_decision["chosenBranch"], "false")
        self.assertFalse(false_decision["result"])
        self.assertEqual(
            false_decision["resolvedParameters"],
            {"step_etch_03": {"duration": 0.2}},
        )
        self.assertEqual(
            false_decision["workflowRunId"], decision["workflowRunId"]
        )
        false_target = await self._target_step(
            false_decision["targetCompileId"], "step_etch_03"
        )
        self.assertAlmostEqual(false_target["duration"], 0.2)

        listed = await asgi_request(
            f"/api/v1/workflow/routes/{self.route_id}/measurement-decisions"
        )
        self.assertEqual(listed[0], 200, listed[2])
        self.assertEqual(len(json_body(listed)["decisions"]), 2)
        fetched = await asgi_request(
            f"/api/v1/workflow/routes/{self.route_id}/measurement-decisions/"
            f"{decision['decisionId']}"
        )
        self.assertEqual(fetched[0], 200, fetched[2])
        self.assertEqual(json_body(fetched)["decisionId"], decision["decisionId"])

        tail = await self._execute(decision["branchSegmentId"])
        self.assertEqual(tail["status"], "succeeded", tail)

    async def test_replay_uses_recorded_decision_without_measuring(self):
        created = await self._decide(
            operator="gt", threshold=0.0,
            true_parameters={"step_etch_03": {"duration": 0.03}},
            false_parameters={"step_etch_03": {"duration": 0.2}},
        )
        original = created["decision"]
        run_id = original["sourceRunId"]
        before = await asgi_request(f"/api/v1/workflow/measurements?run_id={run_id}")
        self.assertEqual(before[0], 200, before[2])
        count_before = len(json_body(before)["measurements"])

        response = await asgi_request(
            f"/api/v1/workflow/routes/{self.route_id}/measurement-decisions/"
            f"{original['decisionId']}/replay",
            method="POST",
            payload={},
        )
        self.assertEqual(response[0], 201, response[2])
        replayed = json_body(response)
        decision = replayed["decision"]
        self.assertNotEqual(decision["decisionId"], original["decisionId"])
        self.assertEqual(decision["replayOf"], original["decisionId"])
        self.assertEqual(decision["measurementId"], original["measurementId"])
        self.assertEqual(decision["chosenBranch"], original["chosenBranch"])
        self.assertEqual(decision["resolvedParameters"], original["resolvedParameters"])
        self.assertEqual(decision["targetCompileId"], original["targetCompileId"])
        self.assertEqual(decision["workflowRunId"], original["workflowRunId"])
        self.assertEqual(replayed["measurement"]["measurementId"], original["measurementId"])
        self.assertEqual(replayed["workflowRunId"], original["workflowRunId"])
        self.assertEqual(
            replayed["resolvedParameters"], {"step_etch_03": {"duration": 0.03}}
        )
        self.assertNotEqual(decision["branchAttemptId"], original["branchAttemptId"])

        after = await asgi_request(f"/api/v1/workflow/measurements?run_id={run_id}")
        self.assertEqual(after[0], 200, after[2])
        self.assertEqual(len(json_body(after)["measurements"]), count_before)
        measurement = await asgi_request(
            f"/api/v1/workflow/measurements/{original['measurementId']}"
        )
        self.assertEqual(measurement[0], 200, measurement[2])
        self.assertEqual(
            json_body(measurement)["measurementId"], original["measurementId"]
        )

        plan = replayed["plan"]
        attempt = next(
            item for item in plan["attempts"]
            if item["attemptId"] == decision["branchAttemptId"]
        )
        segment = next(
            item for item in plan["segments"]
            if item["segmentId"] == decision["branchSegmentId"]
        )
        self.assertEqual(attempt["compileId"], original["targetCompileId"])
        self.assertEqual(segment["sourceCompileId"], original["targetCompileId"])
        self.assertEqual(segment["startAfterStepId"], original["startAfterStepId"])

    async def test_error_boundaries_are_explicit(self):
        # No succeeded prefix on a fresh route.
        route = await asgi_request(
            "/api/v1/workflow/routes/from-compile",
            method="POST",
            payload={"compileId": self.compile_id, "name": "empty prefix"},
        )
        self.assertEqual(route[0], 201, route[2])
        empty_route_id = json_body(route)["plan"]["route"]["routeId"]
        empty = await asgi_request(
            f"/api/v1/workflow/routes/{empty_route_id}/measurement-decisions",
            method="POST",
            payload=self._decision_payload(
                true_parameters={"step_etch_03": {"duration": 0.03}},
                false_parameters={"step_etch_03": {"duration": 0.2}},
            ),
        )
        self.assertEqual(empty[0], 404, empty[2])

        # A non-measured recipe must not pick a branch.
        bad_metric = f"metric_none_{self.suffix}"
        bad_recipe = f"recipe_none_{self.suffix}"
        for path, payload in (
            (
                "/api/v1/workflow/metrics",
                {"schemaVersion": 1, "metricId": bad_metric, "name": "none", "unit": "um"},
            ),
            (
                "/api/v1/workflow/metrology-recipes",
                {
                    "schemaVersion": 1, "recipeId": bad_recipe, "version": 1,
                    "metricId": bad_metric, "name": "outside", "dimension": 2,
                    "method": "etch_depth",
                    "parameters": {"kind": "etch_depth", "x": 999.0, "referenceHeight": 0.0},
                    "material": "Si",
                },
            ),
        ):
            response = await asgi_request(path, method="POST", payload=payload)
            self.assertIn(response[0], (201, 409), response[2])
        non_measured = await asgi_request(
            f"/api/v1/workflow/routes/{self.route_id}/measurement-decisions",
            method="POST",
            payload={
                **self._decision_payload(
                    true_parameters={"step_etch_03": {"duration": 0.03}},
                    false_parameters={"step_etch_03": {"duration": 0.2}},
                ),
                "recipeId": bad_recipe,
            },
        )
        self.assertEqual(non_measured[0], 422, non_measured[2])

        # Invalid operator is rejected by the request contract.
        invalid_operator = await asgi_request(
            f"/api/v1/workflow/routes/{self.route_id}/measurement-decisions",
            method="POST",
            payload={
                **self._decision_payload(
                    true_parameters={"step_etch_03": {"duration": 0.03}},
                    false_parameters={"step_etch_03": {"duration": 0.2}},
                ),
                "operator": "between",
            },
        )
        self.assertEqual(invalid_operator[0], 422, invalid_operator[2])

        # Missing branch parameters and unknown authored steps are 422.
        missing_branch = await asgi_request(
            f"/api/v1/workflow/routes/{self.route_id}/measurement-decisions",
            method="POST",
            payload={
                "startAfterStepId": self.prefix_step_id,
                "recipeId": self.recipe_id,
                "recipeVersion": 1,
                "operator": "gt",
                "threshold": 0.0,
                "branches": {"true": {}},
            },
        )
        self.assertEqual(missing_branch[0], 422, missing_branch[2])

        unknown_step = await asgi_request(
            f"/api/v1/workflow/routes/{self.route_id}/measurement-decisions",
            method="POST",
            payload=self._decision_payload(
                true_parameters={"step_missing": {"duration": 0.03}},
                false_parameters={"step_etch_03": {"duration": 0.2}},
            ),
        )
        self.assertEqual(unknown_step[0], 422, unknown_step[2])

        prefix_override = await asgi_request(
            f"/api/v1/workflow/routes/{self.route_id}/measurement-decisions",
            method="POST",
            payload=self._decision_payload(
                true_parameters={"step_etch_01": {"duration": 0.03}},
                false_parameters={"step_etch_03": {"duration": 0.2}},
            ),
        )
        self.assertEqual(prefix_override[0], 422, prefix_override[2])

        # Cross-route replay is not allowed.
        created = await self._decide(
            operator="gt", threshold=0.0,
            true_parameters={"step_etch_03": {"duration": 0.03}},
            false_parameters={"step_etch_03": {"duration": 0.2}},
        )
        cross = await asgi_request(
            f"/api/v1/workflow/routes/{empty_route_id}/measurement-decisions/"
            f"{created['decision']['decisionId']}/replay",
            method="POST",
            payload={},
        )
        self.assertEqual(cross[0], 404, cross[2])


class MeasurementDecisionMigrationTests(unittest.TestCase):
    def test_v6_database_migrates_to_decisions_schema(self):
        suffix = uuid.uuid4().hex[:6]
        path = storage.VAR / f"test-workflow-decisions-{suffix}.sqlite3"
        store = WorkflowStore(path)
        store.initialize()
        self.assertEqual(store.schema_version(), "7")
        with sqlite3.connect(path) as connection:
            connection.execute("DROP TABLE measurement_decisions")
            connection.execute(
                "UPDATE schema_meta SET value = '6' WHERE key = 'schema_version'"
            )
        migrated = WorkflowStore(path)
        migrated.initialize()
        self.assertEqual(migrated.schema_version(), SCHEMA_VERSION)
        with sqlite3.connect(path) as connection:
            table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'measurement_decisions'"
            ).fetchone()
            version = connection.execute(
                "SELECT value FROM schema_meta WHERE key = 'schema_version'"
            ).fetchone()
        self.assertIsNotNone(table)
        self.assertEqual(version[0], "7")


if __name__ == "__main__":
    unittest.main()
