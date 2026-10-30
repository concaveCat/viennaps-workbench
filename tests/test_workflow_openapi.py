"""P0-6 typed workflow routes appear in a generated OpenAPI schema."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

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
        static = json.loads((Path(__file__).resolve().parents[1] / "docs" / "workflow-openapi.json").read_text(encoding="utf-8"))
        self.assertEqual(static["info"]["title"], schema["info"]["title"])
        self.assertEqual(set(static["paths"]), set(paths))
        snapshot = schema["components"]["schemas"]["SnapshotPayloadV1"]
        self.assertFalse(snapshot.get("additionalProperties", False))
        self.assertIn("gridDelta", snapshot["required"])
        self.assertIn("dimension", snapshot["required"])
        self.assertEqual(
            schema["components"]["schemas"]["MeasureSnapshotRequest"]["properties"]["snapshot"]["$ref"],
            "#/components/schemas/SnapshotPayloadV1",
        )
        self.assertEqual(
            schema["components"]["schemas"]["EquivalenceCheckRequest"]["properties"]["left"]["$ref"],
            "#/components/schemas/SnapshotPayloadV1",
        )


if __name__ == "__main__":
    unittest.main()
