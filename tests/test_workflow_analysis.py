"""P3 snapshot comparison, process window, report and calibration."""

from __future__ import annotations

import asyncio
import json
import os
import time
import unittest
import uuid

os.environ.setdefault("WORKBENCH_PORT", "8767")

from backend.main import app  # noqa: E402
from backend.workflow.analysis import compare_snapshots, process_window  # noqa: E402
from tests.test_api_integration import asgi_request, json_body  # noqa: E402
from tests.test_workflow_adoption import project  # noqa: E402


class AnalysisTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.lifespan = app.router.lifespan_context(app)
        await self.lifespan.__aenter__()
        suffix = uuid.uuid4().hex[:6]
        self.metric_id = f"metric_an_{suffix}"
        self.recipe_id = f"recipe_an_{suffix}"
        for path, payload in (
            ("/api/v1/workflow/metrics", {"schemaVersion": 1, "metricId": self.metric_id, "name": "depth", "unit": "um"}),
            ("/api/v1/workflow/metrology-recipes", {
                "schemaVersion": 1, "recipeId": self.recipe_id, "version": 1, "metricId": self.metric_id,
                "name": "depth", "dimension": 2, "method": "etch_depth",
                "parameters": {"kind": "etch_depth", "x": 0, "referenceHeight": 0}, "material": "Si",
            }),
        ):
            await asgi_request(path, method="POST", payload=payload)
        base = project()
        base["steps"][0].update({"rateMagnitude": 0.02, "duration": 0.02})
        adopted = await asgi_request("/api/v1/workflow/flows/from-project", method="POST", payload={"project": base})
        self.assertEqual(adopted[0], 201, adopted[2])
        self.adopted = json_body(adopted)
        self.step_id = self.adopted["compile"]["sourceMap"][-1]["stepId"]
        created = await asgi_request(
            "/api/v1/workflow/experiments", method="POST",
            payload={
                "name": "analysis three", "project": base, "flowId": self.adopted["flowId"],
                "compileId": self.adopted["compileId"], "recipeId": self.recipe_id, "recipeVersion": 1,
                "variants": [
                    {"parameters": {"step_etch_01": {"duration": duration}}, "replicates": 2, "seed": 700 + index * 10}
                    for index, duration in enumerate((0.02, 0.04, 0.06))
                ],
                "maxAttempts": 2,
            },
        )
        self.assertEqual(created[0], 201, created[2])
        self.experiment = json_body(created)
        started = await asgi_request(
            f"/api/v1/workflow/experiments/{self.experiment['experimentId']}/run", method="POST", payload={}
        )
        self.assertEqual(started[0], 200, started[2])
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            fetched = await asgi_request(f"/api/v1/workflow/experiments/{self.experiment['experimentId']}")
            self.experiment = json_body(fetched)
            if self.experiment["status"] in {"completed", "failed"}:
                break
            await asyncio.sleep(0.3)
        self.assertEqual(self.experiment["status"], "completed", self.experiment)
        self.run_ids = [variant["replicates"][0]["runId"] for variant in self.experiment["variants"]]
        self.measurement_id = self.experiment["variants"][0]["replicates"][0]["measurementId"]

    async def asyncTearDown(self):
        await self.lifespan.__aexit__(None, None, None)

    async def test_compare_view_process_window_and_report(self):
        candidates = [
            {"runId": run_id, "stepId": self.step_id, "label": f"candidate {index + 1}"}
            for index, run_id in enumerate(self.run_ids)
        ]
        compared = await asgi_request(
            "/api/v1/workflow/analysis/compare", method="POST",
            payload={"candidates": candidates, "tolerance": 1e-4},
        )
        self.assertEqual(compared[0], 200, compared[2])
        comparison = json_body(compared)
        self.assertEqual(len(comparison["candidates"]), 3)
        self.assertEqual(len(comparison["pairwise"]), 3)
        for candidate in comparison["candidates"]:
            self.assertEqual(candidate["summary"]["dimension"], 2)
            self.assertRegex(candidate["summary"]["digest"], r"^[a-f0-9]{64}$")
            self.assertEqual(candidate["summary"]["measureKind"], "length_um")
        self.assertTrue(any(row["status"] in {"different", "within_tolerance"} for row in comparison["pairwise"]))
        self.assertTrue(all(row["materialDelta"] for row in comparison["pairwise"]))

        view = await asgi_request(f"/api/v1/workflow/analysis/candidates/{self.run_ids[0]}/{self.step_id}/view")
        self.assertEqual(view[0], 200, view[2])
        view_body = json_body(view)
        self.assertEqual(view_body["dimension"], 2)
        self.assertTrue(view_body["points"])
        self.assertTrue(view_body["lines"])
        self.assertEqual(len(view_body["lineMaterialIds"]), len(view_body["lines"]))

        window = await asgi_request(
            "/api/v1/workflow/analysis/process-window", method="POST",
            payload={"experimentId": self.experiment["experimentId"], "lower": 0.0001, "upper": 0.0008},
        )
        self.assertEqual(window[0], 200, window[2])
        window_body = json_body(window)
        self.assertEqual(window_body["summary"]["total"], 3)
        self.assertEqual(window_body["summary"]["pass"] + window_body["summary"]["fail"] + window_body["summary"]["uncertain"], 3)
        self.assertTrue(window_body["factorWindows"])
        self.assertTrue(window_body["candidates"])
        self.assertEqual(window_body["candidates"][0]["status"], "pass")
        self.assertTrue(all(row["n"] == 2 for row in window_body["rows"]))

        report = await asgi_request(
            "/api/v1/workflow/analysis/report", method="POST",
            payload={
                "title": "P3 acceptance report", "experimentId": self.experiment["experimentId"],
                "runId": self.run_ids[0], "stepId": self.step_id, "measurementId": self.measurement_id,
                "conclusions": "window candidate selected", "lower": 0.0001, "upper": 0.0008,
            },
        )
        self.assertEqual(report[0], 200, report[2])
        body = json_body(report)
        self.assertRegex(body["reportId"], r"^report_[a-f0-9]{16}$")
        self.assertRegex(body["sha256"], r"^[a-f0-9]{64}$")
        html = body["html"]
        for text in (
            "P3 acceptance report", self.adopted["flowId"], "ViennaPS", "actual seed",
            self.metric_id, "Process window", "window candidate selected", "report-svg",
            "Evidence / Provenance", self.measurement_id, "geometry kind",
        ):
            self.assertIn(text, html)
        self.assertIn("打印 / 保存为 PDF", html)

        single = await asgi_request(
            "/api/v1/workflow/analysis/report", method="POST",
            payload={"title": "Single run report", "runId": self.run_ids[0], "stepId": self.step_id},
        )
        self.assertEqual(single[0], 200, single[2])
        single_html = json_body(single)["html"]
        for text in (self.adopted["flowId"], self.metric_id, "geometry kind", "variables", "compileId"):
            self.assertIn(text, single_html)
        self.assertNotIn("No compile envelope selected", single_html)

    def test_process_window_rejects_single_sample_candidates(self):
        document = {
            "experimentId": "exp_single", "metricId": "m", "unit": "um",
            "aggregation": {"rows": [
                {"ordinal": 1, "variantId": "v1", "parameters": {}, "mean": 1.0, "ci95": None, "n": 1, "statisticalStatus": "not_evaluated"},
                {"ordinal": 2, "variantId": "v2", "parameters": {}, "mean": 2.0, "ci95": None, "n": 1, "statisticalStatus": "not_evaluated"},
            ]},
        }
        result = process_window(document, 0.0, 3.0)
        self.assertEqual(result["summary"]["notEvaluated"], 2)
        self.assertEqual(result["summary"]["pass"], 0)
        self.assertEqual(result["candidates"], [])

    def test_structural_diff_checks_connectivity_and_material_units(self):
        points = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]]
        base = {
            "dimension": 3, "topology": "triangles", "points": points,
            "triangles": [[0, 1, 2], [1, 3, 2]], "cellData": {"materialIds": [1, 1]},
            "materials": {"1": {"name": "Mask", "color": "#fff"}, "2": {"name": "Si", "color": "#ccc"}},
        }
        reordered = {**base, "triangles": [[0, 1, 3], [1, 3, 2]]}
        connectivity = compare_snapshots(base, reordered, 1e-3)
        self.assertEqual(connectivity["status"], "different")
        self.assertTrue(any("connectivity" in reason for reason in connectivity["reasons"]))
        material = {**base, "cellData": {"materialIds": [1, 2]}}
        mixed = compare_snapshots(base, material, 1e-3)
        self.assertEqual(mixed["status"], "different")
        self.assertAlmostEqual(mixed["materialTolerance"], 1e-6)
        self.assertTrue(any("material" in reason for reason in mixed["reasons"]))
        line_a = {"dimension": 2, "topology": "lines", "points": [[0, 0, 0], [1, 0, 0], [1, 1, 0]],
                  "lines": [[0, 1]], "cellData": {"materialIds": [1]}, "materials": {"1": {"name": "Si"}}}
        line_b = {**line_a, "lines": [[1, 2]]}
        self.assertEqual(compare_snapshots(line_a, line_b, 1e-6)["status"], "different")

    async def test_calibration_least_squares_and_not_validated(self):
        csv_text = "condition,measured,simulated,weight,split\na,1.0,1.0,1,train\nb,2.0,2.0,1,train\nc,3.1,3.0,1,train\nd,4.0,4.0,1,validation\n"
        dataset = await asgi_request(
            "/api/v1/workflow/calibration/datasets", method="POST",
            payload={"name": "fit one", "csv": csv_text},
        )
        self.assertEqual(dataset[0], 201, dataset[2])
        dataset_body = json_body(dataset)
        self.assertEqual(dataset_body["trainCount"], 3)
        self.assertEqual(dataset_body["validationCount"], 1)
        fitted = await asgi_request(
            "/api/v1/workflow/calibration/fit", method="POST",
            payload={"datasetId": dataset_body["datasetId"], "model": "scale_offset", "method": "least-squares"},
        )
        self.assertEqual(fitted[0], 201, fitted[2])
        fit = json_body(fitted)
        self.assertAlmostEqual(fit["parameters"][0], 1.05, places=8)
        self.assertAlmostEqual(fit["parameters"][1], -0.0666666667, places=7)
        self.assertEqual(fit["trainResiduals"]["n"], 3)
        self.assertEqual(fit["validationResiduals"]["n"], 1)
        self.assertEqual(fit["validationStatus"], "validated")
        fetched = await asgi_request(f"/api/v1/workflow/calibration/fits/{fit['fitId']}")
        self.assertEqual(fetched[0], 200, fetched[2])
        self.assertEqual(json_body(fetched)["fitId"], fit["fitId"])

        train_only = await asgi_request(
            "/api/v1/workflow/calibration/datasets", method="POST",
            payload={"name": "train only", "csv": "measured,simulated\n1,1\n2,2\n3,3\n"},
        )
        self.assertEqual(train_only[0], 201, train_only[2])
        not_validated = await asgi_request(
            "/api/v1/workflow/calibration/fit", method="POST",
            payload={"datasetId": json_body(train_only)["datasetId"], "model": "power", "method": "nelder-mead"},
        )
        self.assertEqual(not_validated[0], 201, not_validated[2])
        result = json_body(not_validated)
        self.assertEqual(result["validationStatus"], "not_validated")
        self.assertEqual(result["validationResiduals"]["n"], 0)
        self.assertGreater(result["iterations"], 0)

        invalid = await asgi_request(
            "/api/v1/workflow/calibration/datasets", method="POST",
            payload={"name": "bad", "csv": "measured,other\n1,2\n"},
        )
        self.assertEqual(invalid[0], 422, invalid[2])

        malformed = await asgi_request(
            "/api/v1/workflow/calibration/datasets", method="POST",
            payload={"name": "bad weight", "csv": "measured,simulated,weight\n1,2,not-a-number\n"},
        )
        self.assertEqual(malformed[0], 422, malformed[2])

        zero_power = await asgi_request(
            "/api/v1/workflow/calibration/datasets", method="POST",
            payload={"name": "zero power", "csv": "measured,simulated\n5,0\n2,1\n0.7,2\n0.3,4\n"},
        )
        self.assertEqual(zero_power[0], 201, zero_power[2])
        zero_fit = await asgi_request(
            "/api/v1/workflow/calibration/fit", method="POST",
            payload={"datasetId": json_body(zero_power)["datasetId"], "model": "power", "method": "nelder-mead"},
        )
        self.assertEqual(zero_fit[0], 422, zero_fit[2])
        self.assertIn("positive simulated", json_body(zero_fit)["detail"])
        offset_fit = await asgi_request(
            "/api/v1/workflow/calibration/fit", method="POST",
            payload={"datasetId": json_body(zero_power)["datasetId"], "model": "power_offset", "method": "nelder-mead"},
        )
        self.assertEqual(offset_fit[0], 201, offset_fit[2])
        offset_body = json_body(offset_fit)
        self.assertEqual(len(offset_body["parameters"]), 3)
        self.assertIsNotNone(offset_body["trainResiduals"]["rmse"])


if __name__ == "__main__":
    unittest.main()
