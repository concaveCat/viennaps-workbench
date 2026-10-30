"""Real continuous/segmented, prefix-reuse and same-segment replay reports."""

from __future__ import annotations

import asyncio
import os
import unittest
import uuid

os.environ.setdefault("WORKBENCH_PORT", "8767")

from backend.main import app  # noqa: E402
from backend.workflow.hashing import full_project_hash  # noqa: E402
from backend.workflow.routes import AttemptV1, RoutePlanV1, RouteV1, SegmentV1  # noqa: E402
from tests.test_api_integration import asgi_request, json_body  # noqa: E402
from tests.workflow_fixtures import binding_plan_2d, compiled_2d_zero_five  # noqa: E402


class EquivalenceEndToEndTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.lifespan = app.router.lifespan_context(app)
        await self.lifespan.__aenter__()
        self.compiled = compiled_2d_zero_five()
        registered = await asgi_request(
            "/api/v1/workflow/compiles", method="POST",
            payload=self.compiled.model_dump(mode="json", exclude_none=True),
        )
        self.assertEqual(registered[0], 201, registered[2])

    async def asyncTearDown(self):
        await self.lifespan.__aexit__(None, None, None)

    async def _wait_run(self, run_id: str, timeout: float = 30.0):
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            response = await asgi_request(f"/api/runs/{run_id}")
            body = json_body(response)
            if body["status"] in {"succeeded", "failed", "cancelled", "interrupted"}:
                return body
            await asyncio.sleep(0.2)
        self.fail("run did not finish")

    async def _wait_segment(self, segment_id: str, timeout: float = 30.0):
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            response = await asgi_request(f"/api/v1/workflow/segments/{segment_id}")
            body = json_body(response)
            if body["status"] in {"succeeded", "failed", "cancelled", "interrupted"}:
                return body
            await asyncio.sleep(0.2)
        self.fail("segment did not finish")

    async def _execute(self, segment_id: str):
        response = await asgi_request(f"/api/v1/workflow/segments/{segment_id}/execute", method="POST", payload={})
        self.assertEqual(response[0], 200, response[2])
        return await self._wait_segment(segment_id)

    async def _snapshot(self, run_id: str, step_id: str) -> dict:
        response = await asgi_request(f"/api/runs/{run_id}/snapshots/{step_id}")
        self.assertEqual(response[0], 200, response[2])
        return json_body(response)

    async def _report(self, check_type: str, protocol: str, left: dict, right: dict,
                      left_run: str, right_run: str, left_segment: str, right_segment: str,
                      left_step: str, right_step: str) -> dict:
        response = await asgi_request(
            "/api/v1/workflow/equivalence/check", method="POST",
            payload={
                "protocol": {"schemaVersion": 1, "protocolId": protocol, "version": 1,
                             "pointTolerance": 0.001, "checkType": check_type},
                "left": left, "right": right,
                "leftRunId": left_run, "rightRunId": right_run,
                "leftSegmentId": left_segment, "rightSegmentId": right_segment,
                "leftStepId": left_step, "rightStepId": right_step,
            },
        )
        self.assertEqual(response[0], 200, response[2])
        return json_body(response)

    async def test_continuous_segmented_prefix_and_replay_reports(self):
        suffix = uuid.uuid4().hex[:6]
        step_ids = [step.id for step in self.compiled.project.steps]
        # Continuous route.
        continuous = binding_plan_2d(self.compiled, suffix=f"c{suffix}", run_id="0" * 24, state_hash="0" * 64)
        payload = continuous.model_dump(mode="json", exclude_none=True)
        payload["attempts"][0]["status"] = "planned"
        payload["segments"][0]["status"] = "planned"
        created = await asgi_request("/api/v1/workflow/routes", method="POST", payload=payload)
        self.assertEqual(created[0], 201, created[2])
        continuous = json_body(created)["plan"]
        continuous_segment = continuous["segments"][0]
        continuous_done = await self._execute(continuous_segment["segmentId"])
        continuous_run = continuous_done["runId"]
        continuous_snapshot = await self._snapshot(continuous_run, step_ids[-1])

        # Two-segment route.
        plan = binding_plan_2d(self.compiled, suffix=f"s{suffix}", run_id="0" * 24, state_hash="0" * 64)
        seg = plan.segments[0]
        s1 = seg.model_copy(update={"segmentId": f"S{suffix}1", "throughStepId": step_ids[1], "runId": f"run{suffix}1", "status": "planned", "segmentProjectHash": full_project_hash(self.compiled.project), "endCheckpointRef": None})
        s2 = seg.model_copy(update={"segmentId": f"S{suffix}2", "startAfterStepId": step_ids[1], "throughStepId": step_ids[-1], "runId": f"run{suffix}2", "status": "planned", "segmentProjectHash": full_project_hash(self.compiled.project), "endCheckpointRef": None})
        attempt = plan.attempts[0].model_copy(update={"routeId": f"R{suffix}", "attemptId": f"A{suffix}"})
        s1 = s1.model_copy(update={"attemptId": attempt.attemptId})
        s2 = s2.model_copy(update={"attemptId": attempt.attemptId})
        segmented = RoutePlanV1(route=RouteV1(routeId=f"R{suffix}", flowId="flow_zero_2d_05", name="segmented", headAttemptId=attempt.attemptId),
                                stepOrder=step_ids, attempts=[attempt], segments=[s1, s2], reuse=[])
        created = await asgi_request("/api/v1/workflow/routes", method="POST", payload=segmented.model_dump(mode="json", exclude_none=True))
        self.assertEqual(created[0], 201, created[2])
        segmented = json_body(created)["plan"]
        first_done = await self._execute(segmented["segments"][0]["segmentId"])
        second_done = await self._execute(segmented["segments"][1]["segmentId"])
        segmented_run = second_done["runId"]
        segmented_snapshot = await self._snapshot(segmented_run, step_ids[-1])

        report = await self._report(
            "continuous_vs_segmented", f"proto_cs_{suffix}", continuous_snapshot, segmented_snapshot,
            continuous_run, segmented_run, continuous_segment["segmentId"], segmented["segments"][1]["segmentId"],
            step_ids[-1], step_ids[-1],
        )
        self.assertEqual(report["status"], "established", report)
        self.assertEqual(report["statisticalStatus"], "not_evaluated")

        # Prefix reuse: right segment starts at the left segment's checkpoint.
        prefix = await self._report(
            "prefix_reuse", f"proto_pre_{suffix}",
            await self._snapshot(first_done["runId"], step_ids[1]),
            await self._snapshot(first_done["runId"], step_ids[1]),
            first_done["runId"], first_done["runId"],
            segmented["segments"][0]["segmentId"], segmented["segments"][1]["segmentId"],
            step_ids[1], step_ids[1],
        )
        self.assertEqual(prefix["status"], "established", prefix)

        # Same segment replay: use the engine replay envelope and compare final snapshots.
        replay_doc = json_body(await asgi_request(f"/api/runs/{continuous_run}/replay-project"))
        replay_started = await asgi_request("/api/runs/replay", method="POST", payload=replay_doc)
        self.assertEqual(replay_started[0], 202, replay_started[2])
        replay_run = json_body(replay_started)["runId"]
        await self._wait_run(replay_run)
        replay_snapshot = await self._snapshot(replay_run, step_ids[-1])
        replay = await self._report(
            "same_segment_replay", f"proto_replay_{suffix}", continuous_snapshot, replay_snapshot,
            continuous_run, replay_run, continuous_segment["segmentId"], continuous_segment["segmentId"],
            step_ids[-1], step_ids[-1],
        )
        self.assertEqual(replay["status"], "established", replay)


if __name__ == "__main__":
    unittest.main()
