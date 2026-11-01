"""Shadow-hook tests for the v2 isotropic bridge.

The hook is default-off. These tests pin both sides:

* with the env flag unset, ``run_process_step`` behaves exactly as before
  (no copy, no shadow event, no v2 metadata);
* with ``VIENNAPS_WORKBENCH_V2_ISOTROPIC_SHADOW=1``, an eligible uniform
  level-set step runs the bridge on a deep copy and records a
  ``v2-isotropic-shadow`` event whose digests and material map match the
  worker path, while the returned metadata and the worker domain do not
  change.

Ineligible steps (material rates, geometric/auto-geometric) must record a
``v2-isotropic-shadow-skipped`` event and never call the bridge.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import viennals
import viennaps as ps

import worker.simulate as simulate
from tests.test_worker_v2_isotropic import (
    GRID_DELTA,
    _old_reference,
    isotropic_step,
    plane_domain,
    plane_project,
)
from worker.process_models_v2 import run_isotropic_v2
from worker.steps.context import RunContext
from worker.steps.process import (
    V2_ISOTROPIC_SHADOW_ENV,
    _v2_isotropic_shadow_eligible,
    run_process_step,
)

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / "var" / "test-worker-v2-shadow"


def _worker_project() -> dict[str, Any]:
    """Complete the bridge fixture so the worker's native-state write can parse it."""
    project = plane_project(2)
    project["solver"].update(
        {
            "random": {"mode": "fixed", "seed": 314159},
            "enginePreference": "cpu",
            "timeoutSeconds": 30,
        }
    )
    project["view"] = {"panX": 0.0, "panH": 0.0, "zoom": 1.0, "showGrid": True}
    return project


def _top(domain: Any) -> float:
    mesh = domain.getSurfaceMesh(addInterfaces=True, sharpCorners=False, minNodeDistanceFactor=0.01)
    return max(float(node[1]) for node in mesh.getNodes())


def _events(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "events.ndjson"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _events_of(run_dir: Path, event_type: str) -> list[dict[str, Any]]:
    return [record for record in _events(run_dir) if record.get("type") == event_type]


@contextlib.contextmanager
def _shadow_flag(enabled: bool):
    """Set or clear only this hook's env flag for the duration of a call."""
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop(V2_ISOTROPIC_SHADOW_ENV, None)
        if enabled:
            os.environ[V2_ISOTROPIC_SHADOW_ENV] = "1"
        yield


class V2IsotropicShadowTests(unittest.TestCase):
    def setUp(self) -> None:
        ps.setNumThreads(1)
        ps.Length.setUnit("um")
        ps.setDimension(2)
        self.project = _worker_project()
        self.work_dir = TEST_ROOT / self._testMethodName
        shutil.rmtree(self.work_dir, ignore_errors=True)
        self.work_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.work_dir, ignore_errors=True)

    def _context(self, name: str, step: dict[str, Any]) -> RunContext:
        run_dir = self.work_dir / name
        run_dir.mkdir(parents=True, exist_ok=True)
        return RunContext(
            project=self.project,
            ps=ps,
            domain=plane_domain(2),
            run_dir=run_dir,
            run_id=f"run-{name}",
            config={},
            actual_steps=[step],
            step_engines={},
            engine_preference="cpu",
            run_seed=None,
            selected_gpu_device=None,
            cuda_readback=None,
            gpu_sample=None,
            native_engine_fingerprint=None,
            ps_version=str(ps.__version__),
            ls_version=str(viennals.__version__),
            # run_process_step reads ctx.snapshots[-1] for the measured step
            # change; a real run always has the initial snapshot first.
            snapshots=[{"stepId": "initial", "materialTops": None}],
            field_state={},
            actual_execution=[],
            intermediate_outputs=[],
        )

    def _assert_no_v2_metadata(self, metadata: dict[str, Any]) -> None:
        self.assertEqual([key for key in metadata if "v2" in key.lower()], [])

    def _assert_shadow_matches(self, run_dir: Path) -> dict[str, Any]:
        records = _events_of(run_dir, "v2-isotropic-shadow")
        self.assertEqual(len(records), 1, records)
        shadow = records[0]
        self.assertIs(shadow["matched"], True, shadow["reasons"])
        self.assertEqual(shadow["reasons"], [])
        self.assertEqual(shadow["oldDigest"], shadow["newDigest"])
        self.assertEqual(shadow["oldMaterialMap"], shadow["newMaterialMap"])
        self.assertEqual(shadow["oldPointCount"], shadow["newPointCount"])
        self.assertEqual(shadow["oldCellCount"], shadow["newCellCount"])
        self.assertIsInstance(shadow["v2Warnings"], list)
        self.assertIsInstance(shadow["v2Metadata"], dict)
        plan = shadow["v2Plan"]
        self.assertEqual(plan["process_id"], "isotropic")
        self.assertEqual(plan["variant_id"], "native")
        self.assertEqual(plan["engine_id"], "viennaps")
        self.assertEqual(plan["engine_version"], str(ps.__version__))
        return shadow

    def test_flag_off_keeps_the_default_path(self) -> None:
        step = isotropic_step("etch", rate=0.3, duration=1.0)
        ctx = self._context("flag-off", step)
        before = _top(ctx.domain)

        with _shadow_flag(False):
            result = run_process_step(ctx, 1, step)

        self.assertAlmostEqual(_top(ctx.domain) - before, -0.3, delta=1e-6)
        self._assert_no_v2_metadata(result.metadata)
        self.assertEqual(_events_of(ctx.run_dir, "v2-isotropic-shadow"), [])
        self.assertEqual(_events_of(ctx.run_dir, "v2-isotropic-shadow-skipped"), [])
        self.assertEqual(len(_events_of(ctx.run_dir, "step-configured")), 1)

    def test_flag_on_etch_shadows_a_copy_and_matches_the_worker(self) -> None:
        step = isotropic_step("etch", rate=0.3, duration=1.0, ratio=0.25)
        ctx = self._context("flag-on-etch", step)
        before = _top(ctx.domain)

        with _shadow_flag(True):
            result = run_process_step(ctx, 1, step)

        # The worker domain moved exactly once; the shadow ran on a copy.
        self.assertAlmostEqual(_top(ctx.domain) - before, -0.3, delta=1e-6)
        shadow = self._assert_shadow_matches(ctx.run_dir)
        self.assertEqual(simulate.surface_state(ctx.domain, 2)["digest"], shadow["oldDigest"])
        self.assertEqual(shadow["v2Metadata"]["operation"], "etch")
        self._assert_no_v2_metadata(result.metadata)
        self.assertEqual(_events_of(ctx.run_dir, "v2-isotropic-shadow-skipped"), [])

    def test_flag_on_deposit_shadows_a_copy_and_matches_the_worker(self) -> None:
        step = isotropic_step("deposit", rate=0.3, duration=1.0)
        ctx = self._context("flag-on-deposit", step)
        before = _top(ctx.domain)

        with _shadow_flag(True):
            result = run_process_step(ctx, 1, step)

        self.assertAlmostEqual(_top(ctx.domain) - before, 0.3, delta=1e-6)
        shadow = self._assert_shadow_matches(ctx.run_dir)
        self.assertEqual(simulate.surface_state(ctx.domain, 2)["digest"], shadow["oldDigest"])
        self.assertIn(int(ps.Material.SiO2), shadow["newMaterialMap"])
        self.assertEqual(shadow["v2Metadata"]["depositMaterial"], "SiO2")
        self._assert_no_v2_metadata(result.metadata)

    def test_snapshot_geometry_matches_the_worker_path(self) -> None:
        """The v2 domain must produce the same snapshot JSON geometry as the old path."""
        for operation in ("etch", "deposit"):
            with self.subTest(operation=operation):
                step = isotropic_step(operation, rate=0.3, duration=1.0)
                old_domain = plane_domain(2)
                new_domain = plane_domain(2)
                _old_reference(ps, self.project, step, old_domain)
                run_isotropic_v2(
                    ps,
                    self.project,
                    step,
                    new_domain,
                    ps_version=str(ps.__version__),
                )

                old_dir = self.work_dir / f"old-snapshot-{operation}"
                new_dir = self.work_dir / f"new-snapshot-{operation}"
                old_dir.mkdir()
                new_dir.mkdir()
                for domain, directory in ((old_domain, old_dir), (new_domain, new_dir)):
                    simulate.save_snapshot(
                        ps,
                        domain,
                        directory,
                        run_id="snapshot-parity",
                        step_id=step["id"],
                        step_name=step["name"],
                        step_index=1,
                        requested=self.project,
                        step=step,
                    )

                old_payload = json.loads((old_dir / f"{step['id']}.json").read_text(encoding="utf-8"))
                new_payload = json.loads((new_dir / f"{step['id']}.json").read_text(encoding="utf-8"))
                # Paths are run-directory relative and therefore not part of the
                # geometry contract; every other field (points, topology, material
                # data, measured tops/change, readback) must be identical.
                ignored = {"outputs"}
                self.assertEqual(
                    {key: value for key, value in old_payload.items() if key not in ignored},
                    {key: value for key, value in new_payload.items() if key not in ignored},
                )

    def test_native_state_hash_matches_the_worker_path(self) -> None:
        """Replay identity starts with the persisted native state file."""
        for operation in ("etch", "deposit"):
            with self.subTest(operation=operation):
                step = isotropic_step(operation, rate=0.3, duration=1.0)
                old_domain = plane_domain(2)
                new_domain = plane_domain(2)
                _old_reference(ps, self.project, step, old_domain)
                run_isotropic_v2(
                    ps,
                    self.project,
                    step,
                    new_domain,
                    ps_version=str(ps.__version__),
                )

                old_dir = self.work_dir / f"old-state-{operation}"
                new_dir = self.work_dir / f"new-state-{operation}"
                old_dir.mkdir()
                new_dir.mkdir()
                old_state = simulate.save_native_state(
                    ps,
                    old_domain,
                    old_dir,
                    project=self.project,
                    run_id="state-parity",
                    step_id=step["id"],
                    viennaps_version=str(ps.__version__),
                    viennals_version=str(viennals.__version__),
                )
                new_state = simulate.save_native_state(
                    ps,
                    new_domain,
                    new_dir,
                    project=self.project,
                    run_id="state-parity",
                    step_id=step["id"],
                    viennaps_version=str(ps.__version__),
                    viennals_version=str(viennals.__version__),
                )
                self.assertEqual(old_state["stateId"], new_state["stateId"])

    def test_material_rate_mode_is_skipped_without_a_bridge_call(self) -> None:
        step = isotropic_step("etch", rate=0.0, duration=1.0)
        step["rateMode"] = "material"
        step["materialRates"] = {"Si": 0.2}
        ctx = self._context("flag-on-material", step)
        before = _top(ctx.domain)

        with _shadow_flag(True), mock.patch("worker.process_models_v2.run_isotropic_v2") as bridge:
            run_process_step(ctx, 1, step)

        bridge.assert_not_called()
        self.assertAlmostEqual(_top(ctx.domain) - before, -0.2, delta=1e-6)
        skipped = _events_of(ctx.run_dir, "v2-isotropic-shadow-skipped")
        self.assertEqual(len(skipped), 1, skipped)
        self.assertEqual(skipped[0]["stepId"], step["id"])
        self.assertIn("material", skipped[0]["reason"].lower())
        self.assertIn("uniform", skipped[0]["reason"].lower())
        self.assertEqual(_events_of(ctx.run_dir, "v2-isotropic-shadow"), [])

    def test_geometric_mode_is_skipped_without_a_bridge_call(self) -> None:
        # Thickness 0.3 um is >= gridDelta 0.2 um, so the explicit geometric
        # path is a legal request and the worker really runs it.
        self.assertGreaterEqual(0.3, GRID_DELTA)
        step = isotropic_step("etch", rate=0.3, duration=1.0)
        step["isotropicMethod"] = "geometric"
        ctx = self._context("flag-on-geometric", step)
        before = _top(ctx.domain)

        with _shadow_flag(True), mock.patch("worker.process_models_v2.run_isotropic_v2") as bridge:
            run_process_step(ctx, 1, step)

        bridge.assert_not_called()
        self.assertLess(_top(ctx.domain) - before, -0.1)
        skipped = _events_of(ctx.run_dir, "v2-isotropic-shadow-skipped")
        self.assertEqual(len(skipped), 1, skipped)
        self.assertIn("geometric", skipped[0]["reason"].lower())
        self.assertEqual(_events_of(ctx.run_dir, "v2-isotropic-shadow"), [])


class V2IsotropicShadowEligibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        ps.setNumThreads(1)
        ps.Length.setUnit("um")
        ps.setDimension(2)
        self.project = plane_project(2)

    def test_uniform_level_set_is_eligible(self) -> None:
        step = isotropic_step("etch", rate=0.3, duration=1.0)
        step["isotropicMethod"] = "levelSet"

        eligible, reason = _v2_isotropic_shadow_eligible(simulate, self.project, step)

        self.assertTrue(eligible, reason)
        self.assertIn("uniform", reason)

    def test_material_rate_mode_is_not_eligible(self) -> None:
        step = isotropic_step("etch", rate=0.0, duration=1.0)
        step["rateMode"] = "material"
        step["materialRates"] = {"Si": 0.2}

        eligible, reason = _v2_isotropic_shadow_eligible(simulate, self.project, step)

        self.assertFalse(eligible)
        self.assertIn("material", reason.lower())
        self.assertIn("uniform", reason.lower())

    def test_geometric_method_is_not_eligible(self) -> None:
        step = isotropic_step("etch", rate=0.3, duration=1.0)
        step["isotropicMethod"] = "geometric"

        eligible, reason = _v2_isotropic_shadow_eligible(simulate, self.project, step)

        self.assertFalse(eligible)
        self.assertIn("geometric", reason.lower())

    def test_auto_that_resolves_geometric_is_not_eligible(self) -> None:
        step = isotropic_step("etch", rate=0.3, duration=1.0)
        step["isotropicMethod"] = "auto"
        method, _dispatch_reason = simulate.resolve_isotropic_method(self.project, step)
        self.assertEqual(method, "geometric")

        eligible, reason = _v2_isotropic_shadow_eligible(simulate, self.project, step)

        self.assertFalse(eligible)
        self.assertIn("geometric", reason.lower())

    def test_schema_three_is_not_eligible(self) -> None:
        project = plane_project(2)
        project["schemaVersion"] = 3
        step = isotropic_step("etch", rate=0.3, duration=1.0)

        eligible, reason = _v2_isotropic_shadow_eligible(simulate, project, step)

        self.assertFalse(eligible)
        self.assertIn("schema", reason.lower())


if __name__ == "__main__":
    unittest.main()
