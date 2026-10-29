"""P0-2 real execution: Segment -> supervisor -> terminal segment state."""

from __future__ import annotations

import asyncio
import os
import unittest
import uuid

os.environ.setdefault("WORKBENCH_PORT", "8767")

from backend.main import app  # noqa: E402
from tests.test_api_integration import asgi_request, json_body  # noqa: E402
from tests.workflow_fixtures import binding_plan_2d, compiled_2d_zero  # noqa: E402


class WorkflowExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.lifespan = app.router.lifespan_context(app)
        await self.lifespan.__aenter__()
        self.compiled = compiled_2d_zero()
        registered = await asgi_request(
            "/api/v1/workflow/compiles",
            method="POST",
            payload=self.compiled.model_dump(mode="json", exclude_none=True),
        )
        self.assertEqual(registered[0], 201, registered[2])
        self.suffix = uuid.uuid4().hex[:6]
        placeholder_run = "0" * 24
        plan = binding_plan_2d(
            self.compiled,
            suffix=self.suffix,
            run_id=placeholder_run,
            state_hash="0" * 64,
        )
        payload = plan.model_dump(mode="json", exclude_none=True)
        payload["attempts"][0]["status"] = "planned"
        payload["segments"][0]["status"] = "planned"
        created = await asgi_request("/api/v1/workflow/routes", method="POST", payload=payload)
        self.assertEqual(created[0], 201, created[2])
        self.segment_id = plan.segments[0].segmentId

    async def asyncTearDown(self) -> None:
        await self.lifespan.__aexit__(None, None, None)

    async def test_execute_segment_updates_status_and_checkpoint(self):
        started = await asgi_request(
            f"/api/v1/workflow/segments/{self.segment_id}/execute",
            method="POST",
            payload={},
        )
        self.assertEqual(started[0], 200, started[2])
        body = json_body(started)
        self.assertEqual(body["segment"]["status"], "running")
        self.assertRegex(body["runId"], r"^[a-f0-9]{24}$")

        deadline = asyncio.get_running_loop().time() + 30.0
        segment = None
        while asyncio.get_running_loop().time() < deadline:
            response = await asgi_request(f"/api/v1/workflow/segments/{self.segment_id}")
            self.assertEqual(response[0], 200, response[2])
            segment = json_body(response)
            if segment["status"] in {"succeeded", "failed", "cancelled", "interrupted"}:
                break
            await asyncio.sleep(0.2)
        self.assertIsNotNone(segment)
        self.assertEqual(segment["status"], "succeeded", segment)
        self.assertEqual(segment["runId"], body["runId"])
        self.assertIsNotNone(segment["endCheckpointRef"])
        self.assertEqual(segment["endCheckpointRef"]["stepId"], segment["throughStepId"])
        self.assertRegex(segment["segmentProjectHash"], r"^[a-f0-9]{64}$")

    async def test_execute_rejects_non_planned_segment(self):
        started = await asgi_request(
            f"/api/v1/workflow/segments/{self.segment_id}/execute",
            method="POST",
            payload={},
        )
        self.assertEqual(started[0], 200, started[2])
        again = await asgi_request(
            f"/api/v1/workflow/segments/{self.segment_id}/execute",
            method="POST",
            payload={},
        )
        self.assertEqual(again[0], 409, again[2])


if __name__ == "__main__":
    unittest.main()
