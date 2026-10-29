"""P0-2 workflow API: route persistence, branch append, idempotency, cancel."""

from __future__ import annotations

import json
import os
import unittest
import uuid

os.environ.setdefault("WORKBENCH_PORT", "8767")

from backend.main import app  # noqa: E402
from tests.test_api_integration import asgi_request, json_body  # noqa: E402
from tests.test_workflow_routes import plan_payload  # noqa: E402


def unique_plan(suffix: str) -> dict:
    payload = json.loads(json.dumps(plan_payload()))
    replacements = {
        "R01": f"R{suffix}",
        "A01": f"A{suffix}1",
        "A02": f"A{suffix}2",
        "S01": f"S{suffix}1",
        "S02": f"S{suffix}2",
        "S03": f"S{suffix}3",
        "RU1": f"RU{suffix}",
        "sub_a1": f"sub{suffix}a1",
        "sub_a2": f"sub{suffix}a2",
    }
    for old, new in replacements.items():
        payload = json.loads(json.dumps(payload).replace(old, new))
    return payload


def base_plan(suffix: str) -> dict:
    payload = unique_plan(suffix)
    payload["route"]["headAttemptId"] = payload["attempts"][0]["attemptId"]
    payload["attempts"] = payload["attempts"][:1]
    payload["segments"] = payload["segments"][:2]
    payload["reuse"] = []
    return payload


def append_body(suffix: str) -> dict:
    payload = unique_plan(suffix)
    return {
        "attempt": payload["attempts"][1],
        "segments": [payload["segments"][2]],
        "reuse": payload["reuse"],
    }


class WorkflowApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.lifespan = app.router.lifespan_context(app)
        await self.lifespan.__aenter__()

    async def asyncTearDown(self) -> None:
        await self.lifespan.__aexit__(None, None, None)

    def suffix(self) -> str:
        return uuid.uuid4().hex[:6]

    async def test_create_read_and_list_route(self):
        suffix = self.suffix()
        plan = unique_plan(suffix)
        created = await asgi_request("/api/v1/workflow/routes", method="POST", payload=plan)
        self.assertEqual(created[0], 201, created[2])
        body = json_body(created)
        self.assertEqual(body["plan"]["route"]["routeId"], plan["route"]["routeId"])

        fetched = await asgi_request(f"/api/v1/workflow/routes/{plan['route']['routeId']}")
        self.assertEqual(fetched[0], 200, fetched[2])
        self.assertEqual(json_body(fetched)["plan"]["route"]["routeId"], plan["route"]["routeId"])

        listed = await asgi_request("/api/v1/workflow/routes")
        self.assertEqual(listed[0], 200, listed[2])
        rows = json_body(listed)["routes"]
        self.assertTrue(any(row["routeId"] == plan["route"]["routeId"] for row in rows))

    async def test_append_branch_is_idempotent_and_materializes_prefix(self):
        suffix = self.suffix()
        base = base_plan(suffix)
        route_id = base["route"]["routeId"]
        created = await asgi_request("/api/v1/workflow/routes", method="POST", payload=base)
        self.assertEqual(created[0], 201, created[2])

        body = append_body(suffix)
        first = await asgi_request(f"/api/v1/workflow/routes/{route_id}/attempts", method="POST", payload=body)
        self.assertEqual(first[0], 200, first[2])
        first_plan = json_body(first)["plan"]
        self.assertEqual(first_plan["route"]["headAttemptId"], body["attempt"]["attemptId"])
        self.assertEqual(len(first_plan["attempts"]), 2)

        second = await asgi_request(f"/api/v1/workflow/routes/{route_id}/attempts", method="POST", payload=body)
        self.assertEqual(second[0], 200, second[2])
        second_plan = json_body(second)["plan"]
        self.assertEqual(len(second_plan["attempts"]), 2)
        self.assertEqual(len(second_plan["segments"]), 3)

        materialized = await asgi_request(
            f"/api/v1/workflow/routes/{route_id}/attempts/{body['attempt']['attemptId']}/materialize"
        )
        self.assertEqual(materialized[0], 200, materialized[2])
        refs = json_body(materialized)["segments"]
        self.assertEqual([item["reused"] for item in refs], [True, False])
        self.assertEqual([item["segmentId"] for item in refs], [base["segments"][0]["segmentId"], body["segments"][0]["segmentId"]])

    async def test_cancel_attempt_and_segment(self):
        suffix = self.suffix()
        plan = base_plan(suffix)
        plan["attempts"][0]["status"] = "planned"
        plan["segments"][0]["status"] = "planned"
        plan["segments"][1]["status"] = "planned"
        route_id = plan["route"]["routeId"]
        created = await asgi_request("/api/v1/workflow/routes", method="POST", payload=plan)
        self.assertEqual(created[0], 201, created[2])
        attempt_id = plan["attempts"][0]["attemptId"]
        segment_id = plan["segments"][0]["segmentId"]

        cancelled = await asgi_request(f"/api/v1/workflow/attempts/{attempt_id}/cancel", method="POST")
        self.assertEqual(cancelled[0], 200, cancelled[2])
        self.assertEqual(json_body(cancelled)["status"], "cancelled")

        again = await asgi_request(f"/api/v1/workflow/attempts/{attempt_id}/cancel", method="POST")
        self.assertEqual(again[0], 409, again[2])

        segment_cancelled = await asgi_request(f"/api/v1/workflow/segments/{segment_id}/cancel", method="POST")
        self.assertEqual(segment_cancelled[0], 200, segment_cancelled[2])
        self.assertEqual(json_body(segment_cancelled)["status"], "cancelled")

    async def test_duplicate_route_returns_409(self):
        suffix = self.suffix()
        plan = unique_plan(suffix)
        first = await asgi_request("/api/v1/workflow/routes", method="POST", payload=plan)
        self.assertEqual(first[0], 201, first[2])
        second = await asgi_request("/api/v1/workflow/routes", method="POST", payload=plan)
        self.assertEqual(second[0], 409, second[2])

    async def test_same_submission_key_for_another_attempt_returns_409(self):
        suffix = self.suffix()
        base = base_plan(suffix)
        route_id = base["route"]["routeId"]
        created = await asgi_request("/api/v1/workflow/routes", method="POST", payload=base)
        self.assertEqual(created[0], 201, created[2])
        body = append_body(suffix)
        first = await asgi_request(f"/api/v1/workflow/routes/{route_id}/attempts", method="POST", payload=body)
        self.assertEqual(first[0], 200, first[2])
        body["attempt"]["attemptId"] = f"A{suffix}9"
        conflict = await asgi_request(f"/api/v1/workflow/routes/{route_id}/attempts", method="POST", payload=body)
        self.assertEqual(conflict[0], 409, conflict[2])

    async def test_missing_route_returns_404(self):
        response = await asgi_request("/api/v1/workflow/routes/DOES_NOT_EXIST")
        self.assertEqual(response[0], 404, response[2])

    async def test_invalid_branch_returns_422(self):
        suffix = self.suffix()
        base = base_plan(suffix)
        route_id = base["route"]["routeId"]
        created = await asgi_request("/api/v1/workflow/routes", method="POST", payload=base)
        self.assertEqual(created[0], 201, created[2])
        body = append_body(suffix)
        body["segments"][0]["startAfterStepId"] = "s004"
        response = await asgi_request(f"/api/v1/workflow/routes/{route_id}/attempts", method="POST", payload=body)
        self.assertEqual(response[0], 422, response[2])


if __name__ == "__main__":
    unittest.main()
