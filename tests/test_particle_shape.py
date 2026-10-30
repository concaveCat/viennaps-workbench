"""Schema-7 ``particleShape``: native Box/Sphere geometric distributions.

The installed ViennaPS 4.6.2 ``BoxDistribution`` / ``SphereDistribution`` are
ViennaLS geometric-advection distributions, not velocity models. Bounded
subprocess probes established the only safe surface this batch exposes:

* the no-mask overload moves the exposed surface by exactly one radius or
  half-axis set and ignores the process duration;
* every masked deposition probe SIGSEGVs in 2D and 3D (material mask, raw
  ``viennals`` mask from the project material level set, and
  ``applyToSingleMaterial`` alike), so the JSON schema has no mask field;
* ``CustomSphereDistribution`` aborts with ``std::runtime_error`` because it
  indexes one radius per surface point; it stays unexposed;
* zero magnitudes and mixed-sign half-axes also crash or delete the surface.

These tests pin the safe subset against an independent native reference and
keep the rejected paths as explicit, measured evidence.
"""
from __future__ import annotations

import asyncio
import json
import math
import unittest
from pathlib import Path

from backend import snapshots as snapshot_store
from backend import storage
from backend.hashing import requested_input_hash
from backend.main import app
from backend.rates import has_motion, is_geometry_step
from backend.validation import ProjectInputError, validate_project
from tests.test_api_integration import asgi_request, json_body


ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DIR = storage.VAR / "test-particle-shape"


def base_project(dimension: int, geometry: dict | None = None) -> dict:
    value = {
        "schemaVersion": 7, "name": f"形状偏移 {dimension}D", "dimension": dimension,
        "units": {"length": "um", "time": "s", "rate": "um/s", "angle": "deg"},
        "domain": {"gridDelta": 0.25, "xExtent": 4.0,
                   **({"xBoundary": "reflective"} if dimension == 2 else {"yExtent": 4.0, "boundaryXY": "reflective"})},
        "geometry": geometry or {"kind": "plane", "height": 0.0, "substrateMaterial": "Si"},
        "steps": [],
        "solver": {"timeStepRatio": 0.25, "raysPerPoint": 32, "cpuThreads": 1,
                   "random": {"mode": "fixed", "seed": 271828}, "enginePreference": "cpu", "timeoutSeconds": 60,
                   "advection": {"spatialScheme": "ENGQUIST_OSHER_1ST_ORDER", "temporalScheme": "FORWARD_EULER",
                                 "adaptiveTimeStepping": False, "adaptiveTimeStepSubdivisions": 20,
                                 "calculateIntermediateVelocities": False, "checkDissipation": True,
                                 "dissipationAlpha": 1.0, "ignoreVoids": False, "velocityOutput": False},
                   "rayTracing": {"diskRadius": 0.0, "ignoreFluxBoundaries": False, "maxBoundaryHits": 80,
                                  "maxReflections": 3, "minNodeDistanceFactor": 0.05,
                                  "normalizationType": "SOURCE", "smoothingNeighbors": 1}},
        "outputSettings": {"levelSets": False, "levelSetMeshes": False, "volumeMesh": False, "nativeMetadata": False},
    }
    if dimension == 2:
        value["view"] = {"panX": 0.0, "panH": 0.0, "zoom": 1.0, "showGrid": True}
    else:
        value["view3D"] = {"projection": "parallel", "camera": None, "displayMode": "surface",
                           "visibleMaterialIds": [0, 10], "opacityByMaterial": {},
                           "clip": {"enabled": False, "axis": "x", "position": 0.0, "keep": "positive"},
                           "section": {"enabled": False, "axis": "y", "position": 0.0}}
    return value


def shape_step(dimension: int, *, step_id: str = "step_shape_0001", **overrides) -> dict:
    step = {"id": step_id, "name": "形状偏移", "enabled": True, "operation": "deposit",
            "model": "particleShape", "shape": "sphere", "radius": 0.5}
    step.update(overrides)
    return step


def hash_baseline_project() -> dict:
    value = base_project(3)
    value["steps"] = [{"id": "step_iso_0001", "name": "各向同性", "enabled": True, "operation": "etch",
                       "model": "isotropic", "rateMagnitude": 0.2, "duration": 1.0, "rateMode": "uniform"}]
    value["solver"] = {**value["solver"], "enginePreference": "cpu"}
    value["view3D"]["visibleMaterialIds"] = [0, 10]
    return value


class ParticleShapeValidationTests(unittest.TestCase):
    def setUp(self):
        storage.initialize()

    def test_valid_sphere_and_box_steps_in_both_dimensions(self):
        for dimension in (2, 3):
            for step in (
                shape_step(dimension, radius=0.5),
                shape_step(dimension, step_id="step_shape_0002", shape="box",
                           radius=None, halfAxes=[0.5, 0.75] if dimension == 2 else [0.5, 0.5, 0.75]),
                shape_step(dimension, step_id="step_shape_0003", operation="etch", radius=0.25),
            ):
                with self.subTest(dimension=dimension, step=step):
                    project = base_project(dimension)
                    project["steps"] = [step]
                    parsed, warnings = validate_project(project, kind="simulate")
                    self.assertEqual(parsed.steps[0].model, "particleShape")
                    self.assertTrue(is_geometry_step(parsed.steps[0]))
                    self.assertFalse(has_motion(parsed.steps[0]))
                    self.assertFalse(hasattr(parsed.steps[0], "duration"))
                    self.assertFalse(hasattr(parsed.steps[0], "rateMagnitude"))
                    self.assertTrue(any("particleShape" in warning for warning in warnings))
                    normalized = parsed.model_dump(mode="json", exclude_none=True)["steps"][0]
                    self.assertNotIn("duration", normalized)
                    self.assertNotIn("mask", normalized)
                    self.assertNotIn("maskMaterials", normalized)

    def test_rejects_unsafe_or_unsupported_surfaces(self):
        cases = {
            "zero radius": {"radius": 0},
            "sub-grid radius": {"radius": 0.1},
            "over-bound radius": {"radius": 2.5},
            "sphere with halfAxes": {"halfAxes": [0.5, 0.5]},
            "box without halfAxes": {"shape": "box", "radius": None},
            "box with radius": {"shape": "box", "radius": 0.5},
            "too few box axes": {"shape": "box", "radius": None, "halfAxes": [0.5]},
            "too many box axes": {"shape": "box", "radius": None, "halfAxes": [0.5, 0.5, 0.5]},
            "mask material": {"maskMaterials": ["Mask"]},
            "raw mask": {"mask": "Mask"},
            "duration": {"duration": 1.0},
            "rate": {"rateMagnitude": 0.2},
        }
        for label, override in cases.items():
            with self.subTest(label=label):
                project = base_project(2)
                project["steps"] = [shape_step(2, **override)]
                with self.assertRaises(ProjectInputError):
                    validate_project(project, kind="simulate")

    def test_three_dimensional_half_axes_are_required_for_three_d(self):
        project = base_project(3)
        project["steps"] = [shape_step(3, shape="box", radius=None, halfAxes=[0.5, 0.5])]
        with self.assertRaises(ProjectInputError):
            validate_project(project, kind="simulate")
        project["steps"][0]["halfAxes"] = [0.5, 0.5, 0.25]
        parsed, _ = validate_project(project, kind="simulate")
        self.assertEqual(parsed.steps[0].halfAxes, [0.5, 0.5, 0.25])

    def test_periodic_boundaries_are_rejected(self):
        for dimension in (2, 3):
            project = base_project(dimension)
            if dimension == 2:
                project["domain"]["xBoundary"] = "periodic"
            else:
                project["domain"]["boundaryXY"] = "periodic"
            project["steps"] = [shape_step(dimension)]
            with self.subTest(dimension=dimension):
                with self.assertRaisesRegex(ProjectInputError, "periodic"):
                    validate_project(project, kind="simulate")

    def test_existing_request_hash_is_unchanged_and_the_new_step_is_additive(self):
        # Captured from the base commit before particleShape existed. Adding a
        # model to the schema-7 union must not move any existing request hash.
        baseline = hash_baseline_project()
        parsed, _ = validate_project(baseline, kind="simulate")
        self.assertEqual(requested_input_hash(parsed),
                         "605a109bed4b3193b5fea760773049d98d8c2ba7fb5f68e4c802cb9ac3e83c42")
        with_shape = base_project(3)
        with_shape["steps"] = [shape_step(3, radius=0.5)]
        shaped, _ = validate_project(with_shape, kind="simulate")
        self.assertNotEqual(requested_input_hash(shaped), requested_input_hash(parsed))
        without = base_project(3)
        without["steps"] = []
        empty, _ = validate_project(without)
        self.assertNotEqual(requested_input_hash(empty), requested_input_hash(shaped))

    def test_project_save_reopen_keeps_the_new_step(self):
        project = base_project(3)
        project["steps"] = [shape_step(3, shape="box", radius=None, halfAxes=[0.25, 0.5, 0.75])]
        normalized = validate_project(project)[0].model_dump(mode="json", exclude_none=True)
        record = storage.create_project(normalized)
        reopened = storage.read_json(storage.project_path(record["id"]))["project"]
        self.assertEqual(reopened["steps"][0], normalized["steps"][0])
        parsed, _ = validate_project(reopened, kind="simulate")
        self.assertEqual(parsed.steps[0].halfAxes, [0.25, 0.5, 0.75])


class UnsafeNativePathEvidenceTests(unittest.IsolatedAsyncioTestCase):
    """Keep the rejected native paths measured, not merely asserted in prose."""

    async def probe(self, label: str, case: dict) -> tuple[int, dict, str]:
        REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
        case_path = REFERENCE_DIR / f"{label}.case.json"
        output_path = REFERENCE_DIR / f"{label}.out.json"
        guard_path = REFERENCE_DIR / f"{label}.guard.json"
        log_path = REFERENCE_DIR / f"{label}.log"
        case_path.write_text(json.dumps(case) + "\n", encoding="utf-8")
        command = [str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/bounded_child.py"),
                   "--cwd", str(ROOT), "--record", str(guard_path), "--log-path", str(log_path),
                   "--timeout-seconds", "40", "--max-rss-mib", "2048", "--max-log-bytes", "1048576", "--",
                   str(ROOT / ".venv/bin/python"), str(ROOT / "worker/probe_particle_distributions.py"),
                   str(case_path), str(output_path)]
        completed = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.DEVNULL)
        await completed.wait()
        guard = json.loads(guard_path.read_text(encoding="utf-8"))
        return guard["returnCode"], guard, log_path.read_text(encoding="utf-8", errors="replace")

    async def test_custom_sphere_aborts_in_two_and_three_dimensions(self):
        for dimension in (2, 3):
            with self.subTest(dimension=dimension):
                code, guard, log = await self.probe(
                    f"custom-{dimension}d",
                    {"dimension": dimension, "shape": "customSphere", "magnitude": 0.0,
                     "radii": [0.1, 0.2, 0.3], "mask": "none"})
                self.assertFalse(guard["timedOut"])
                self.assertEqual(code, -6)
                self.assertIn("out of bounds for CustomSphereDistribution", log)

    async def test_masked_deposition_segfaults_and_zero_is_unsafe(self):
        for label, case in {
            "masked-sphere-3d": {"dimension": 3, "shape": "sphere", "magnitude": 0.2,
                                 "mask": "material", "geometry": "trench"},
            "masked-box-2d": {"dimension": 2, "shape": "box", "magnitude": 0.2,
                              "mask": "raw-material", "geometry": "trench"},
            "zero-box-2d": {"dimension": 2, "shape": "box", "magnitude": 0.0, "mask": "none"},
        }.items():
            with self.subTest(label=label):
                code, guard, _log = await self.probe(label, case)
                self.assertFalse(guard["timedOut"])
                self.assertEqual(code, -11)

    async def test_unmasked_probe_still_reports_a_real_surface_change(self):
        code, _guard, _log = await self.probe(
            "safe-sphere-3d", {"dimension": 3, "shape": "sphere", "magnitude": 0.2, "mask": "none"})
        self.assertEqual(code, 0)
        output = json.loads((REFERENCE_DIR / "safe-sphere-3d.out.json").read_text(encoding="utf-8"))
        before = output["before"]["boundingBox"][2]
        after = output["after"]["boundingBox"][2]
        self.assertAlmostEqual(after[1] - before[1], 0.2, places=6)


class ParticleShapeRunTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
        self.life = app.router.lifespan_context(app)
        await self.life.__aenter__()

    async def asyncTearDown(self):
        await self.life.__aexit__(None, None, None)

    async def wait(self, run_id: str) -> dict:
        for _ in range(2400):
            info = json_body(await asgi_request(f"/api/runs/{run_id}"))
            if info["status"] in {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}:
                self.assertEqual(info["status"], "succeeded", info.get("failureReason"))
                return info
            await asyncio.sleep(0.05)
        self.fail("bounded native worker exceeded acceptance timeout")

    async def execute(self, project: dict) -> tuple[str, dict]:
        response = await asgi_request("/api/runs", method="POST", payload={"kind": "simulate", "project": project})
        self.assertEqual(response[0], 202, response[2])
        run_id = json_body(response)["runId"]
        return run_id, await self.wait(run_id)

    async def reference(self, project: dict, label: str) -> dict:
        input_path = REFERENCE_DIR / f"{label}-input.json"
        output_path = REFERENCE_DIR / f"{label}-direct-reference.json"
        guard_path = REFERENCE_DIR / f"{label}-reference-guard.json"
        log_path = REFERENCE_DIR / f"{label}-reference.log"
        input_path.write_text(json.dumps(project, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        command = [str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/bounded_child.py"),
                   "--cwd", str(ROOT), "--record", str(guard_path), "--log-path", str(log_path),
                   "--timeout-seconds", "90", "--max-rss-mib", "2048", "--max-log-bytes", "2097152", "--",
                   str(ROOT / ".venv/bin/python"), str(ROOT / "tests/fixtures/particle_shape_direct_reference.py"),
                   str(input_path), str(output_path)]
        completed = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.DEVNULL)
        self.assertEqual(await completed.wait(), 0, log_path.read_text(encoding="utf-8", errors="replace"))
        return json.loads(output_path.read_text(encoding="utf-8"))

    async def snapshot(self, run_id: str, step_id: str) -> dict:
        snapshot_store.ensure_materialized(storage.run_path(run_id), step_id)
        return json_body(await asgi_request(f"/api/runs/{run_id}/snapshots/{step_id}"))

    async def compare(self, project: dict, label: str) -> dict:
        reference = await self.reference(project, label)
        run_id, info = await self.execute(project)
        dimension = project["dimension"]
        topology = "lines" if dimension == 2 else "triangles"
        step_id = project["steps"][-1]["id"]
        actual = await self.snapshot(run_id, step_id)
        expected = reference[step_id]
        self.assertEqual(actual["cellData"]["materialIds"], expected["materialIds"])
        self.assertEqual(actual["materialIdsByLevelSetIndex"], expected["materialMap"])
        self.assertEqual(len(actual["points"]), len(expected["points"]))
        error = max((math.dist(a, b) for a, b in zip(actual["points"], expected["points"])), default=0.0)
        self.assertLessEqual(error, 1e-6, f"{label} maximum coordinate error {error}")

        manifest = storage.read_json(storage.run_path(run_id) / "manifest.json")
        self.assertEqual(manifest["actualExecution"][-1]["actualEngine"], "not-applicable")
        self.assertNotIn("duration", manifest["actualExecution"][-1])
        self.assertNotIn("actualRate", manifest["actualExecution"][-1])
        parameters = manifest["actualExecution"][-1]["actualParameters"]
        self.assertEqual(parameters["geometryOperation"], "particleShape")
        self.assertEqual(parameters["nativeMask"]["mode"], "none")
        self.assertFalse(parameters["nativeMask"]["supported"])
        self.assertIn("ignored", parameters["nativeDurationIgnored"])
        self.assertNotEqual(parameters["surfaceBoundsBeforeUm"], parameters["surfaceBoundsAfterUm"])
        self.assertTrue(parameters["levelSetPointCountsAfter"])

        replay = json_body(await asgi_request(f"/api/runs/{run_id}/replay-project"))
        replay_response = await asgi_request("/api/runs/replay", method="POST", payload=replay)
        self.assertEqual(replay_response[0], 202, replay_response[2])
        replay_id = json_body(replay_response)["runId"]
        await self.wait(replay_id)
        repeated = await self.snapshot(replay_id, step_id)
        for field in ("points", topology, "cellData", "materialIdsByLevelSetIndex"):
            self.assertEqual(actual[field], repeated[field], field)
        return {"runId": run_id, "replayRunId": replay_id, "steps": [row["stepId"] for row in info["snapshots"]],
                "nativeClass": parameters["nativeClass"], "maxCoordinateError": error}

    async def test_sphere_deposit_matches_native_reference_in_2d_and_3d(self):
        for dimension in (2, 3):
            with self.subTest(dimension=dimension):
                project = base_project(dimension)
                project["steps"] = [shape_step(dimension, radius=0.5)]
                result = await self.compare(project, f"sphere-{dimension}d")
                self.assertEqual(result["nativeClass"], "SphereDistribution")

    async def test_box_etch_matches_native_reference_in_2d_and_3d(self):
        for dimension in (2, 3):
            with self.subTest(dimension=dimension):
                project = base_project(dimension)
                project["steps"] = [shape_step(
                    dimension, operation="etch", shape="box", radius=None,
                    halfAxes=[0.5, 0.75] if dimension == 2 else [0.25, 0.5, 0.75])]
                result = await self.compare(project, f"box-etch-{dimension}d")
                self.assertEqual(result["nativeClass"], "BoxDistribution")

    async def test_box_deposition_on_a_trench_matches_native_reference(self):
        project = base_project(2, geometry={"kind": "trench", "width": 2.0, "depth": 1.0, "taperAngle": 0.0,
                                            "maskHeight": 0.5, "maskTaperAngle": 0.0, "substrateMaterial": "Si"})
        project["steps"] = [shape_step(2, shape="box", radius=None, halfAxes=[0.5, 0.25])]
        result = await self.compare(project, "box-trench-2d")
        parameters = storage.read_json(storage.run_path(result["runId"]) / "manifest.json")["actualExecution"][-1]["actualParameters"]
        self.assertEqual(parameters["nativeHalfAxesUm"], [0.5, 0.25, 0.0])
        self.assertGreater(len(parameters["levelSetPointCountsAfter"]), 1)

    async def test_two_consecutive_shape_steps_replay_identically(self):
        project = base_project(3)
        project["steps"] = [
            shape_step(3, radius=0.5),
            shape_step(3, step_id="step_shape_0002", shape="box", radius=None, halfAxes=[0.25, 0.25, 0.25]),
        ]
        result = await self.compare(project, "stacked-3d")
        # The comparison targets the last snapshot; also assert the first step
        # produced its own materialized snapshot in the direct reference.
        reference = json.loads((REFERENCE_DIR / "stacked-3d-direct-reference.json").read_text(encoding="utf-8"))
        self.assertIn("step_shape_0001", reference)
        self.assertIn("step_shape_0002", reference)
        self.assertNotEqual(reference["step_shape_0001"]["points"], reference["step_shape_0002"]["points"])

    async def test_api_rejects_mask_fields_periodic_boundaries_and_zero_magnitude(self):
        masked = base_project(2)
        masked["steps"] = [shape_step(2, maskMaterials=["Mask"])]
        response = await asgi_request("/api/runs", method="POST", payload={"kind": "simulate", "project": masked})
        self.assertEqual(response[0], 422, response[2])
        self.assertIn("maskMaterials", response[2].decode("utf-8"))

        periodic = base_project(2)
        periodic["domain"]["xBoundary"] = "periodic"
        periodic["steps"] = [shape_step(2)]
        response = await asgi_request("/api/runs", method="POST", payload={"kind": "simulate", "project": periodic})
        self.assertEqual(response[0], 422, response[2])
        self.assertIn("periodic", response[2].decode("utf-8"))

        zero = base_project(2)
        zero["steps"] = [shape_step(2, radius=0)]
        response = await asgi_request("/api/runs", method="POST", payload={"kind": "simulate", "project": zero})
        self.assertEqual(response[0], 422, response[2])


if __name__ == "__main__":
    unittest.main()
