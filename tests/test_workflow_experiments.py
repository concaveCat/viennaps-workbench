"""P1 persistent manual experiment/variant/replicate skeleton."""

from __future__ import annotations

import os
import unittest
import uuid

os.environ.setdefault("WORKBENCH_PORT", "8767")

from backend.main import app  # noqa: E402
from tests.test_api_integration import asgi_request, json_body  # noqa: E402
from tests.test_workflow_adoption import project  # noqa: E402


class ExperimentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.lifespan = app.router.lifespan_context(app)
        await self.lifespan.__aenter__()
        suffix = uuid.uuid4().hex[:6]
        self.metric_id = f"metric_exp_{suffix}"
        self.recipe_id = f"recipe_exp_{suffix}"
        for path, payload in (
            ("/api/v1/workflow/metrics", {"schemaVersion": 1, "metricId": self.metric_id, "name": "exp", "unit": "um"}),
            ("/api/v1/workflow/metrology-recipes", {
                "schemaVersion": 1, "recipeId": self.recipe_id, "version": 1, "metricId": self.metric_id,
                "name": "exp", "dimension": 2, "method": "etch_depth",
                "parameters": {"kind": "etch_depth", "x": 0, "referenceHeight": 0}, "material": "Si",
            }),
        ):
            response = await asgi_request(path, method="POST", payload=payload)
            self.assertIn(response[0], (201, 409), response[2])

    async def asyncTearDown(self):
        await self.lifespan.__aexit__(None, None, None)

    async def test_create_ten_variants_with_replicates(self):
        p = project()
        variants = [{"parameters": {"step_etch_01": {"duration": 0.01 + index * 0.001}}, "replicates": 2, "seed": 100 + index} for index in range(10)]
        response = await asgi_request(
            "/api/v1/workflow/experiments", method="POST",
            payload={
                "name": "ten variants", "project": p, "flowId": "flow_exp_01",
                "recipeId": self.recipe_id, "recipeVersion": 1, "variants": variants,
            },
        )
        self.assertEqual(response[0], 201, response[2])
        document = json_body(response)
        self.assertEqual(len(document["variants"]), 10)
        self.assertTrue(all(len(variant["replicates"]) == 2 for variant in document["variants"]))
        fetched = await asgi_request(f"/api/v1/workflow/experiments/{document['experimentId']}")
        self.assertEqual(fetched[0], 200, fetched[2])
        self.assertEqual(len(json_body(fetched)["variants"]), 10)


if __name__ == "__main__":
    unittest.main()
