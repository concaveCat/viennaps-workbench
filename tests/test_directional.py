"""DirectionalProcess schema, native, persistence and replay acceptance."""
from __future__ import annotations

import asyncio
import copy
import json
import math
import subprocess
import unittest
import zipfile
import io
from pathlib import Path

from backend import snapshots as snapshot_store
from backend import storage
from backend.hashing import requested_input_hash
from backend.main import app
from backend.validation import ProjectInputError, select_engine, validate_project
from tests.test_api_integration import asgi_request, json_body


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "directional-evidence"


def project(dimension: int = 2, geometry: str = "plane") -> dict:
    value = {
        "schemaVersion": 5,
        "name": f"定向过程 {dimension}D",
        "dimension": dimension,
        "units": {"length": "um", "time": "s", "rate": "um/s", "angle": "deg"},
        "domain": {"gridDelta": 0.2, "xExtent": 4.0,
                   **({"xBoundary": "reflective"} if dimension == 2 else {"yExtent": 4.0, "boundaryXY": "reflective"})},
        "geometry": {"kind": "plane", "height": 0.0, "substrateMaterial": "Si"},
        "steps": [],
        "solver": {"timeStepRatio": 0.25, "raysPerPoint": 32, "cpuThreads": 1,
                   "random": {"mode": "fixed", "seed": 314159}, "enginePreference": "cpu", "timeoutSeconds": 30},
    }
    if geometry == "trench":
        value["geometry"] = {"kind": "trench", "width": 1.6, "depth": 1.2, "taperAngle": 0.0,
                              "maskHeight": 0.6, "maskTaperAngle": 0.0, "substrateMaterial": "Si", "maskMaterial": "Mask"}
    if dimension == 2:
        value["view"] = {"panX": 0.0, "panH": 0.0, "zoom": 1.0, "showGrid": True}
    else:
        value["view3D"] = {"projection": "parallel", "camera": None, "displayMode": "surface",
                           "visibleMaterialIds": [], "opacityByMaterial": {},
                           "clip": {"enabled": False, "axis": "x", "position": 0.0, "keep": "positive"},
                           "section": {"enabled": False, "axis": "y", "position": 0.0}}
    return value


def directional_step(*, operation="etch", direction=None, directional=0.3, isotropic=0.1,
                     visibility=True, deposit_material=None, step_id="step_directional"):
    step = {"id": step_id, "name": "定向工艺", "enabled": True, "operation": operation,
            "model": "directional", "direction": direction or [0.0, -1.0, 0.0],
            "directionalRateMagnitude": directional, "isotropicRateMagnitude": isotropic,
            "calculateVisibility": visibility, "duration": 0.4}
    if operation == "deposit":
        step["depositMaterial"] = deposit_material or "W"
    return step


class DirectionalContract(unittest.TestCase):
    def test_validation_hash_mapping_and_no_ray_fields(self):
        for dimension in (2, 3):
            p = project(dimension)
            p["steps"] = [directional_step(direction=[0.0, -1.0, 0.0] if dimension == 2 else [0.0, 0.0, -1.0])]
            parsed, _ = validate_project(p, kind="simulate")
            baseline = requested_input_hash(parsed)
            scaled = copy.deepcopy(p)
            if dimension == 2:
                scaled["steps"][0]["direction"] = [0.0, -0.5, 0.0]
            else:
                scaled["steps"][0]["direction"] = [0.0, 0.0, -0.5]
            self.assertEqual(baseline, requested_input_hash(validate_project(scaled)[0]))
            changed = copy.deepcopy(p)
            changed["steps"][0]["directionalRateMagnitude"] = 0.2
            self.assertNotEqual(baseline, requested_input_hash(validate_project(changed)[0]))
            bad_cases = []
            bad = copy.deepcopy(p); bad["steps"][0]["direction"] = [0.0, 0.0, 0.0]; bad_cases.append(bad)
            if dimension == 2:
                bad = copy.deepcopy(p); bad["steps"][0]["direction"][2] = 1.0; bad_cases.append(bad)
            bad = copy.deepcopy(p); bad["steps"][0]["rayTracingParameters"] = {"raysPerPoint": 64}; bad_cases.append(bad)
            bad = copy.deepcopy(p); bad["steps"][0]["direction"] = [0.0, 1.01, 0.0]; bad_cases.append(bad)
            for bad in bad_cases:
                with self.assertRaises(ProjectInputError):
                    validate_project(bad)
            gpu_preferred = copy.deepcopy(p); gpu_preferred["solver"]["enginePreference"] = "gpu"
            parsed_gpu, _ = validate_project(gpu_preferred, kind="simulate")
            normalized_gpu = parsed_gpu.model_dump(mode="json", exclude_none=True)
            self.assertEqual(select_engine(normalized_gpu, normalized_gpu["steps"][0]), "not-applicable")

    def test_zero_directional_and_isotropic_components_are_noop_independently(self):
        p = project(2)
        step = directional_step(directional=0.0, isotropic=0.0)
        p["steps"] = [step]
        first = requested_input_hash(validate_project(p)[0])
        q = copy.deepcopy(p)
        q["steps"][0]["direction"] = [1.0, 0.0, 0.0]
        q["steps"][0]["calculateVisibility"] = False
        self.assertEqual(first, requested_input_hash(validate_project(q)[0]))
        q["steps"][0]["isotropicRateMagnitude"] = 0.1
        self.assertNotEqual(first, requested_input_hash(validate_project(q)[0]))


class DirectionalWorkflow(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        self.life = app.router.lifespan_context(app)
        await self.life.__aenter__()

    async def asyncTearDown(self):
        await self.life.__aexit__(None, None, None)

    async def wait(self, run_id):
        for _ in range(600):
            info = json_body(await asgi_request(f"/api/runs/{run_id}"))
            if info["status"] in {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}:
                self.assertEqual(info["status"], "succeeded", info.get("failureReason"))
                return info
            await asyncio.sleep(0.05)
        self.fail("bounded native worker exceeded acceptance timeout")

    async def execute(self, value):
        response = await asgi_request("/api/runs", method="POST", payload={"kind": "simulate", "project": value})
        self.assertEqual(response[0], 202, response[2])
        run_id = json_body(response)["runId"]
        return run_id, await self.wait(run_id)

    async def reference(self, value, label):
        input_path = EVIDENCE / f"{label}-input.json"
        output_path = EVIDENCE / f"{label}-direct-reference.json"
        guard_path = EVIDENCE / f"{label}-reference-guard.json"
        log_path = EVIDENCE / f"{label}-reference.log"
        input_path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        command = [str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/bounded_child.py"),
                   "--cwd", str(ROOT), "--record", str(guard_path), "--log-path", str(log_path),
                   "--timeout-seconds", "60", "--max-rss-mib", "2048", "--max-log-bytes", "2097152", "--",
                   str(ROOT / ".venv/bin/python"), str(ROOT / "tests/fixtures/directional_direct_reference.py"),
                   str(input_path), str(output_path)]
        completed = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.DEVNULL)
        self.assertEqual(await completed.wait(), 0, log_path.read_text(encoding="utf-8", errors="replace"))
        return json.loads(output_path.read_text(encoding="utf-8"))

    async def assert_reference(self, value, label):
        run_id, info = await self.execute(value)
        direct = await self.reference(value, label)
        key = "lines" if value["dimension"] == 2 else "triangles"
        errors = []
        manifest = storage.read_json(storage.run_path(run_id) / "manifest.json")
        self.assertEqual(manifest["schemaVersion"], 5)
        self.assertIsNone(manifest["actualSeed"], "DirectionalProcess must not receive a random seed")
        self.assertEqual(manifest["plannedExecution"], [{"stepId": s["id"], "engine": "not-applicable", "seed": None} for s in value["steps"]])
        self.assertTrue(any(item["model"] == "directional" for item in info["actualExecution"]))
        for row in info["snapshots"]:
            snapshot = storage.read_json(storage.run_path(run_id) / f"{row['stepId']}.json")
            expected = direct[row["stepId"]]
            self.assertEqual(snapshot[key], expected["cells"])
            self.assertEqual(snapshot["cellData"]["materialIds"], expected["materialIds"])
            self.assertEqual(snapshot["materialIdsByLevelSetIndex"], expected["materialMap"])
            self.assertEqual(len(snapshot["points"]), len(expected["points"]))
            error = max((math.dist(a, b) for a, b in zip(snapshot["points"], expected["points"])), default=0.0)
            self.assertLessEqual(error, 1e-10)
            errors.append(error)
        comparison = {"runId": run_id, "dimension": value["dimension"], "schemaVersion": 5,
                      "nativeTopologyAndMaterialArraysEqual": True, "maxCoordinateErrors": errors,
                      "coordinateTolerance": 1e-10, "actualExecution": info["actualExecution"]}
        (EVIDENCE / f"{label}-comparison.json").write_text(json.dumps(comparison, indent=2) + "\n", encoding="utf-8")
        return run_id, info

    async def test_real_2d_etch_3d_deposit_and_visibility(self):
        two_d = project(2)
        two_d["steps"] = [directional_step(direction=[0.0, -1.0, 0.0], directional=0.3, isotropic=0.1)]
        run_2d, info_2d = await self.assert_reference(two_d, "directional-etch-2d")
        record_2d = info_2d["actualExecution"][0]
        self.assertEqual(record_2d["actualEngine"], "not-applicable")
        self.assertEqual(record_2d["actualSeed"], None)
        self.assertEqual(record_2d["actualParameters"]["nativeDirectionalVelocity"], -0.3)
        self.assertEqual(record_2d["actualParameters"]["nativeIsotropicVelocity"], -0.1)
        self.assertEqual(record_2d["actualParameters"]["raysPerPoint"], "not-applicable")
        self.assertEqual(record_2d["actualParameters"]["nativeUnitDirection"], [0.0, -1.0, 0.0])

        three_d = project(3)
        three_d["steps"] = [directional_step(operation="deposit", direction=[0.0, 0.0, -1.0], directional=0.25,
                                             isotropic=0.05, deposit_material="W")]
        run_3d, info_3d = await self.assert_reference(three_d, "directional-deposit-3d")
        parameters = info_3d["actualExecution"][0]["actualParameters"]
        self.assertEqual(parameters["nativeDirectionalVelocity"], 0.25)
        self.assertEqual(parameters["nativeIsotropicVelocity"], 0.05)
        self.assertIn(70, storage.read_json(storage.run_path(run_3d) / "manifest.json")["snapshots"][-1]["materialIds"])

        visibility_outputs = {}
        for dimension in (2, 3):
            outputs = {}
            for visibility in (True, False):
                value = project(dimension, "trench")
                value["steps"] = [directional_step(direction=[0.6, -0.8, 0.0] if dimension == 2 else [0.6, 0.0, -0.8],
                                                    directional=0.5, isotropic=0.0, visibility=visibility,
                                                    step_id=f"step_vis_{dimension}_{int(visibility)}")]
                label = f"directional-visibility-{dimension}d-{str(visibility).lower()}"
                run_id, info = await self.assert_reference(value, label)
                record = info["actualExecution"][0]
                self.assertEqual(record["actualParameters"]["calculateVisibility"], visibility)
                self.assertEqual(record["actualParameters"]["maskMaterials"], ["Mask"])
                snapshot_store.ensure_materialized(storage.run_path(run_id), info["snapshots"][-1]["stepId"])
                outputs[visibility] = storage.read_json(storage.run_path(run_id) / info["snapshots"][-1]["snapshot"])
            key = "lines" if dimension == 2 else "triangles"
            self.assertNotEqual(outputs[True][key], outputs[False][key], f"visibility toggle did not affect {dimension}D geometry")
            visibility_outputs[str(dimension)] = {"onPointCount": len(outputs[True]["points"]), "offPointCount": len(outputs[False]["points"])}
        (EVIDENCE / "visibility-effect.json").write_text(json.dumps(visibility_outputs, indent=2) + "\n", encoding="utf-8")

    async def test_save_reopen_project_export_replay_history_and_zero_noop(self):
        value = project(2)
        value["steps"] = [directional_step(directional=0.2, isotropic=0.0),
                          directional_step(directional=0.0, isotropic=0.0, step_id="step_directional_zero")]
        # The second step is a true no-op and should not duplicate/modify a material level set.
        run_id, info = await self.assert_reference(value, "directional-save-replay-2d")
        created = await asgi_request("/api/projects", method="POST", payload=value)
        self.assertEqual(created[0], 201, created[2])
        project_id = json_body(created)["id"]
        reopened = json_body(await asgi_request(f"/api/projects/{project_id}"))["project"]
        self.assertEqual(reopened, value)
        validated = await asgi_request("/api/projects/validate", method="POST", payload=reopened)
        self.assertEqual(validated[0], 200, validated[2])
        self.assertEqual(json_body(validated)["normalizedProject"], value)

        replay = json_body(await asgi_request(f"/api/runs/{run_id}/replay-project"))
        self.assertEqual(replay["schemaVersion"], 5)
        self.assertEqual(replay["executionRequirements"]["steps"], [])
        self.assertEqual(replay["normalizedProject"]["steps"][1]["directionalRateMagnitude"], 0.0)
        submitted = await asgi_request("/api/runs/replay", method="POST", payload=replay)
        self.assertEqual(submitted[0], 202, submitted[2])
        replay_id = json_body(submitted)["runId"]
        replay_info = await self.wait(replay_id)
        for step in ("initial", "step_directional", "step_directional_zero"):
            left = storage.read_json(storage.run_path(run_id) / f"{step}.json")
            right = storage.read_json(storage.run_path(replay_id) / f"{step}.json")
            self.assertEqual(left["points"], right["points"])
            self.assertEqual(left.get("lines"), right.get("lines"))
            self.assertEqual(left.get("triangles"), right.get("triangles"))
            self.assertEqual(left["cellData"]["materialIds"], right["cellData"]["materialIds"])
        self.assertTrue(storage.read_json(storage.run_path(run_id) / "step_directional_zero.json")["noOp"])
        self.assertTrue(replay_info["actualExecution"][-1]["noOp"])
        self.assertIn("not-applicable", replay_info["actualExecution"][-1]["actualParameters"]["calculateVisibility"])

        archive = await asgi_request(f"/api/runs/{run_id}/download")
        self.assertEqual(archive[0], 200)
        with zipfile.ZipFile(io.BytesIO(archive[2])) as zipped:
            self.assertIn("replay-project.json", zipped.namelist())
            self.assertIn("step_directional.json", zipped.namelist())
        history = json_body(await asgi_request(f"/api/runs/{run_id}"))
        self.assertEqual(history["actualExecution"][0]["actualParameters"]["nativeDirectionalVelocity"], -0.2)
        self.assertEqual(history["actualExecution"][0]["actualParameters"]["randomSeed"], "not-applicable")
