"""P0-6 typed workflow routes appear in a generated OpenAPI schema."""

from __future__ import annotations

import unittest

from backend.main import app


class WorkflowOpenApiTests(unittest.TestCase):
    def test_workflow_routes_are_in_generated_schema(self):
        schema = app.openapi()
        paths = schema["paths"]
        for path in (
            "/api/v1/workflow/routes",
            "/api/v1/workflow/compiles",
            "/api/v1/workflow/measurements",
            "/api/v1/workflow/measurements/{measurement_id}/bind",
            "/api/v1/workflow/measurements/{measurement_id}/provenance",
            "/api/v1/workflow/equivalence/check",
            "/api/v1/workflow/gc/dry-run",
        ):
            self.assertIn(path, paths)
        self.assertIn("RoutePlanV1", schema["components"]["schemas"])
        self.assertIn("MeasurementResultV1", schema["components"]["schemas"])


if __name__ == "__main__":
    unittest.main()
