"""P1 templates, scalar variables and flow diff."""

from __future__ import annotations

import os
import unittest
import uuid

os.environ.setdefault("WORKBENCH_PORT", "8767")

from backend.main import app  # noqa: E402
from tests.test_api_integration import asgi_request, json_body  # noqa: E402
from tests.test_workflow_adoption import project  # noqa: E402


class TemplateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.lifespan = app.router.lifespan_context(app)
        await self.lifespan.__aenter__()

    async def asyncTearDown(self):
        await self.lifespan.__aexit__(None, None, None)

    async def test_template_variable_substitution_and_diff(self):
        suffix = uuid.uuid4().hex[:6]
        template_id = f"tpl_{suffix}"
        base_project = project()
        steps = [dict(item) for item in base_project["steps"]]
        steps[0]["duration"] = "${duration}"
        created = await asgi_request(
            "/api/v1/workflow/templates",
            method="POST",
            payload={
                "schemaVersion": 1,
                "templateId": template_id,
                "name": "variable template",
                "description": "",
                "steps": steps,
                "variables": [{"name": "duration", "value": 0.1, "unit": "s"}],
            },
        )
        self.assertEqual(created[0], 201, created[2])
        compiled = await asgi_request(
            "/api/v1/workflow/flows/from-template",
            method="POST",
            payload={"project": base_project, "templateId": template_id, "variables": {"duration": 0.2}},
        )
        self.assertEqual(compiled[0], 201, compiled[2])
        body = json_body(compiled)
        self.assertAlmostEqual(body["compile"]["project"]["steps"][0]["duration"], 0.2)
        diff = await asgi_request(
            f"/api/v1/workflow/compiles/{body['compileId']}/diff/{body['compileId']}"
        )
        self.assertEqual(diff[0], 200, diff[2])
        self.assertEqual(json_body(diff)["changedStepIds"], [])


if __name__ == "__main__":
    unittest.main()
