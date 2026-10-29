"""Artifact preflight: estimator calibration and API warning/reject behavior.

The estimator is fitted to real completed runs but the tests stay synthetic:
they reuse the checked-in 22 nm FinFET fixture (4 layers, 13 steps) and only
change its domain or enabled-step count. No simulation is started: the
``/api/runs`` tests patch ``Supervisor.start`` so only the HTTP contract is
exercised.
"""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import preflight
from backend.main import app
from backend.supervisor import Supervisor
from backend.validation import validate_project
from tests.test_api_integration import asgi_request, default_project, json_body

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
FINFET_PROJECT = FIXTURES / "finfet22_project.json"
FINFET_GDS = FIXTURES / "finfet22_workbench.gds"


def finfet(*, grid_delta: float = 0.004, x_extent: float = 1.0,
           y_extent: float = 1.0, enabled_steps: int = 11,
           output_settings: dict[str, bool] | None = None) -> dict:
    project = json.loads(FINFET_PROJECT.read_text(encoding="utf-8"))
    project["domain"].update(gridDelta=grid_delta, xExtent=x_extent, yExtent=y_extent)
    for index, step in enumerate(project["steps"]):
        step["enabled"] = index < enabled_steps
    if output_settings is not None:
        project["outputSettings"] = output_settings
    return project


def normalized(project: dict) -> dict:
    with patch("backend.storage.resolve_gds_asset", return_value=FINFET_GDS):
        parsed, _warnings = validate_project(project)
    return parsed.model_dump(mode="json", exclude_none=True)


class EstimatorCalibration(unittest.TestCase):
    def test_incident_shape_is_rejected(self) -> None:
        estimate = preflight.estimate_artifacts(
            normalized(finfet(grid_delta=0.003, enabled_steps=13))
        )
        self.assertEqual(estimate["risk"], "reject")
        self.assertEqual(estimate["confidence"], "high")
        self.assertTrue(preflight.should_reject(estimate))
        self.assertGreater(estimate["estimatedBytes"], preflight.ARTIFACT_CAP_BYTES)
        # Calibrated at 599 MiB on disk; the fit is deliberately a little high.
        self.assertGreater(estimate["estimatedMiB"], 600)
        self.assertLess(estimate["estimatedMiB"], 800)
        kinds = {row["kind"] for row in estimate["contributors"]}
        self.assertTrue({"nativeStates", "initialSnapshot", "stepSnapshots"} <= kinds)
        message = preflight.warning_message(estimate)
        self.assertIn("产物预检", message)
        self.assertIn("建议：", message)
        self.assertIn("gridDelta", message)
        self.assertNotIn("Suggestions", message)

    def test_near_cap_shape_warns_but_is_allowed(self) -> None:
        estimate = preflight.estimate_artifacts(
            normalized(finfet(grid_delta=0.004, enabled_steps=13))
        )
        self.assertEqual(estimate["risk"], "warning")
        self.assertFalse(preflight.should_reject(estimate))
        self.assertGreaterEqual(estimate["utilization"], 0.75)
        self.assertLessEqual(estimate["utilization"], 1.0)
        self.assertTrue(preflight.warning_message(estimate))
        self.assertIn("产物预检", preflight.warning_message(estimate))

    def test_normal_real_shape_is_unchanged(self) -> None:
        estimate = preflight.estimate_artifacts(normalized(finfet(enabled_steps=11)))
        self.assertEqual(estimate["risk"], "ok")
        self.assertIsNone(preflight.warning_message(estimate))
        self.assertLess(estimate["utilization"], 0.75)
        # Calibrated at 256 MiB on disk. The archive (all snapshots) is larger
        # and is reported separately without turning the run into a warning.
        self.assertGreater(estimate["estimatedMiB"], 256)
        self.assertLess(estimate["estimatedMiB"], 384)
        self.assertEqual(estimate["archiveRisk"], "warning")

    def test_small_cell_real_shape_is_well_below_the_cap(self) -> None:
        estimate = preflight.estimate_artifacts(
            normalized(finfet(grid_delta=0.003, x_extent=0.48, y_extent=0.50, enabled_steps=13))
        )
        self.assertEqual(estimate["risk"], "ok")
        self.assertLess(estimate["utilization"], 0.4)

    def test_preview_counts_only_the_initial_state_and_snapshot(self) -> None:
        project = normalized(finfet(grid_delta=0.002, enabled_steps=0))
        estimate = preflight.estimate_artifacts(project, kind="preview")
        self.assertEqual(estimate["risk"], "ok")
        self.assertEqual(estimate["confidence"], "high")
        # Calibrated preview run on disk: 146 MiB.
        self.assertGreater(estimate["estimatedMiB"], 100)
        self.assertLess(estimate["estimatedMiB"], 200)

    def test_eager_snapshots_increase_the_estimate(self) -> None:
        project = normalized(finfet(grid_delta=0.004, enabled_steps=13))
        lazy = preflight.estimate_artifacts(project, lazy_snapshots=True)
        eager = preflight.estimate_artifacts(project, lazy_snapshots=False)
        self.assertGreater(eager["estimatedBytes"], lazy["estimatedBytes"])
        self.assertGreater(eager["estimatedBytes"], preflight.ARTIFACT_CAP_BYTES)
        self.assertEqual(eager["risk"], "reject")

    def test_optional_outputs_are_named_contributors(self) -> None:
        project = normalized(finfet(
            enabled_steps=11,
            output_settings={"levelSets": False, "levelSetMeshes": False, "surfaceMeshVtk": False,
                             "volumeMesh": True, "nativeMetadata": False},
        ))
        estimate = preflight.estimate_artifacts(project)
        optional = [row for row in estimate["contributors"] if row["kind"] == "optionalOutputs"]
        self.assertEqual(len(optional), 1)
        self.assertIn("volumeMesh", optional[0]["detail"])
        self.assertGreater(estimate["estimatedBytes"], 512 * 1024**2)
        self.assertEqual(estimate["risk"], "reject")
        # Volume meshes are calibrated on fin runs, not on this GDS shape.
        self.assertEqual(estimate["confidence"], "medium")

    def test_uncalibrated_level_sets_warn_instead_of_rejecting(self) -> None:
        project = normalized(finfet(
            grid_delta=0.003, enabled_steps=13,
            output_settings={"levelSets": True, "levelSetMeshes": False, "surfaceMeshVtk": False,
                             "volumeMesh": False, "nativeMetadata": False},
        ))
        estimate = preflight.estimate_artifacts(project)
        self.assertEqual(estimate["confidence"], "low")
        self.assertEqual(estimate["risk"], "warning")
        self.assertTrue(estimate["rejectionDowngraded"])
        self.assertIn("未校准", preflight.warning_message(estimate))

    def test_thresholds_match_the_published_resource_policy(self) -> None:
        policy = json.loads((ROOT / "docs" / "resource-policy.json").read_text(encoding="utf-8"))
        published = policy["run"]["artifactPreflight"]
        self.assertEqual(published["capBytes"], preflight.ARTIFACT_CAP_BYTES)
        self.assertEqual(published["warningFraction"], preflight.WARNING_FRACTION)
        self.assertEqual(published["rejectFraction"], preflight.REJECT_FRACTION)
        self.assertEqual(published["safetyFactor"], preflight.SAFETY_FACTOR)

    def test_tiny_baseline_projects_stay_ok(self) -> None:
        tiny = default_project()
        tiny["steps"] = [{"id": "step_preflight_01", "name": "tiny", "enabled": True,
                          "operation": "etch", "model": "isotropic",
                          "rateMagnitude": 0.1, "duration": 0.1}]
        estimate = preflight.estimate_artifacts(normalized(tiny))
        self.assertEqual(estimate["risk"], "ok")
        self.assertLess(estimate["estimatedMiB"], 1)


class PreflightApiContract(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.lifespan = app.router.lifespan_context(app)
        await self.lifespan.__aenter__()

    async def asyncTearDown(self) -> None:
        await self.lifespan.__aexit__(None, None, None)

    async def validate(self, project: dict) -> tuple[int, dict]:
        with patch("backend.storage.resolve_gds_asset", return_value=FINFET_GDS):
            response = await asgi_request("/api/projects/validate", method="POST", payload=project)
        return response[0], json_body(response)

    async def test_validate_warning_is_additive_and_keeps_valid_true(self) -> None:
        status, body = await self.validate(finfet(grid_delta=0.004, enabled_steps=13))
        self.assertEqual(status, 200, body)
        self.assertTrue(body["valid"])
        self.assertIn("normalizedProject", body)
        self.assertIn("requestedInputHash", body)
        self.assertIn("artifactEstimate", body)
        self.assertEqual(body["artifactEstimate"]["risk"], "warning")
        self.assertTrue(any("产物预检" in warning for warning in body["warnings"]))

    async def test_validate_normal_project_has_no_new_warning(self) -> None:
        status, body = await self.validate(finfet(enabled_steps=11))
        self.assertEqual(status, 200, body)
        self.assertTrue(body["valid"])
        self.assertEqual(body["artifactEstimate"]["risk"], "ok")
        self.assertFalse(any("产物预检" in warning for warning in body["warnings"]))

    async def test_run_rejects_incident_shape_before_the_worker_starts(self) -> None:
        with patch("backend.storage.resolve_gds_asset", return_value=FINFET_GDS), \
                patch.object(Supervisor, "start", side_effect=AssertionError("worker must not start")):
            response = await asgi_request(
                "/api/runs", method="POST",
                payload={"kind": "simulate", "project": finfet(grid_delta=0.003, enabled_steps=13)},
            )
        self.assertEqual(response[0], 422, response[2])
        detail = json_body(response)["detail"]
        self.assertEqual(detail["code"], "artifact-budget-preflight")
        self.assertIn("512 MiB", detail["message"])
        self.assertIn("artifactEstimate", detail)
        self.assertEqual(detail["artifactEstimate"]["risk"], "reject")

    async def test_run_allows_near_cap_shape_with_a_warning(self) -> None:
        fake = {"runId": "a" * 24, "status": "pending", "requestedInputHash": "b" * 64}
        with patch("backend.storage.resolve_gds_asset", return_value=FINFET_GDS), \
                patch.object(Supervisor, "start", return_value=dict(fake)):
            response = await asgi_request(
                "/api/runs", method="POST",
                payload={"kind": "simulate", "project": finfet(grid_delta=0.004, enabled_steps=13)},
            )
        self.assertEqual(response[0], 202, response[2])
        body = json_body(response)
        self.assertEqual(body["runId"], fake["runId"])
        self.assertIn("artifactWarning", body)
        self.assertIn("产物预检", body["artifactWarning"])
        self.assertEqual(body["artifactEstimate"]["risk"], "warning")

    async def test_run_leaves_normal_projects_unchanged(self) -> None:
        fake = {"runId": "c" * 24, "status": "pending", "requestedInputHash": "d" * 64}
        with patch.object(Supervisor, "start", return_value=dict(fake)):
            response = await asgi_request(
                "/api/runs", method="POST",
                payload={"kind": "preview", "project": default_project()},
            )
        self.assertEqual(response[0], 202, response[2])
        body = json_body(response)
        self.assertEqual(body["runId"], fake["runId"])
        self.assertNotIn("artifactWarning", body)
        self.assertEqual(body["artifactEstimate"]["risk"], "ok")


if __name__ == "__main__":
    unittest.main()
