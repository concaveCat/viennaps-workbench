"""Uploaded CSV rate tables and native ``ps.CSVFileProcess`` (schema 7).

The installed ViennaPS 4.6.2 binding reads a rate table with exactly D numeric
columns per row (2D ``x,rate``; 3D ``x,y,rate``) and auto-detects LINEAR/IDW.
A failed load is only logged by the engine, so the workbench validates the table
before it copies it into a run directory and requires an explicit interpolation
mode. These tests pin the verified native semantics:

* positive rates with a downward direction move the surface outward (deposit),
  negative rates etch — the native model has no separate etch/deposit flag;
* LINEAR requires a strictly increasing x column (2D) or a complete rectangular
  x/y grid (3D), otherwise the native interpolator silently returns bad values;
* custom Python interpolators are unreachable.
"""
from __future__ import annotations

import asyncio
import copy
import json
import math
import shutil
import unittest
from pathlib import Path
from urllib.parse import quote

from backend import snapshots as snapshot_store
from backend import storage
from backend.csv_assets import RateCsvError, inspect_rate_csv
from backend.hashing import requested_input_hash
from backend.main import app
from backend.validation import ProjectInputError, validate_project
from tests.test_api_integration import asgi_request, json_body


ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DIR = storage.VAR / "test-csv-reference"


def base_project(dimension: int, geometry: dict | None = None) -> dict:
    value = {
        "schemaVersion": 7, "name": f"CSV 速率表 {dimension}D", "dimension": dimension,
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
                           "visibleMaterialIds": [0, 10, 30], "opacityByMaterial": {"0": 1.0, "10": 1.0, "30": 1.0},
                           "clip": {"enabled": False, "axis": "x", "position": 0.0, "keep": "positive"},
                           "section": {"enabled": False, "axis": "y", "position": 0.0}}
    return value


CSV_2D = b"# x,rate\n-2,0.2\n-1,0.3\n0,0.4\n1,0.5\n2,0.6\n"
CSV_3D = ("\n".join(f"{x},{y},{0.2 + 0.02 * (x + 2) + 0.01 * (y + 2)}"
                    for y in (-2, 0, 2) for x in (-2, 0, 2)) + "\n").encode("ascii")


def csv_step(asset: dict, dimension: int, *, step_id: str = "step_csv_0001", **overrides) -> dict:
    step = {
        "id": step_id, "name": "CSV 速率表", "enabled": True, "operation": "native", "model": "csvProcess",
        "duration": 1.0, "assetId": asset["assetId"], "fileName": asset["fileName"],
        "direction": [0, -1, 0] if dimension == 2 else [0, 0, -1],
        "offset": [0, 0], "isotropicComponent": 0.0, "directionalComponent": 1.0,
        "interpolationMode": "LINEAR", "calculateVisibility": True,
    }
    step.update(overrides)
    return step


async def upload_csv(dimension: int, body: bytes, name: str = "rates.csv") -> tuple[int, bytes]:
    status, _headers, payload = await asgi_request(
        f"/api/csv-assets?file_name={quote(name)}&dimension={dimension}", method="POST", payload=body,
        headers=[(b"content-type", b"text/csv")])
    return status, payload


class CsvParserTests(unittest.TestCase):
    def test_verified_column_schemas(self):
        summary_2d = inspect_rate_csv(CSV_2D, 2)
        self.assertEqual(summary_2d["columns"], ["x", "rate"])
        self.assertTrue(summary_2d["linearCompatible"])
        summary_3d = inspect_rate_csv(CSV_3D, 3)
        self.assertEqual(summary_3d["columns"], ["x", "y", "rate"])
        self.assertTrue(summary_3d["linearCompatible"])
        self.assertEqual(summary_3d["rowCount"], 9)

    def test_rejects_failed_native_load_shapes(self):
        cases = {
            "wrong columns": (b"0,1,2\n1,1,2\n", 2),
            "non finite": (b"0,nan\n1,0.5\n", 2),
            "non numeric": (b"x,0.5\n1,0.5\n", 2),
            "blank line": (b"0,0.5\n\n1,0.5\n", 2),
            "one row": (b"0,0.5\n", 2),
            "hex float": (b"0,0x1p0\n1,0.5\n", 2),
        }
        for label, (body, dimension) in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(RateCsvError):
                    inspect_rate_csv(body, dimension)

    def test_linear_compatibility_is_stricter_than_idw(self):
        self.assertFalse(inspect_rate_csv(b"1,0.5\n0,0.4\n", 2)["linearCompatible"])
        incomplete = b"0,0,0.5\n0,2,0.5\n2,0,0.5\n"
        summary = inspect_rate_csv(incomplete, 3)
        self.assertFalse(summary["linearCompatible"])
        self.assertTrue(summary["idwCompatible"])


class CsvAssetApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.life = app.router.lifespan_context(app)
        await self.life.__aenter__()

    async def asyncTearDown(self):
        await self.life.__aexit__(None, None, None)

    async def test_upload_is_content_addressed_and_capped(self):
        status, body = await upload_csv(2, CSV_2D, "rates.csv")
        self.assertEqual(status, 201, body)
        asset = json.loads(body)
        self.assertEqual(asset["assetId"], storage.file_sha256(storage.csv_asset_path(asset["assetId"])))
        self.assertEqual(asset["dimension"], 2)
        self.assertEqual(asset["columns"], ["x", "rate"])
        self.assertTrue(storage.resolve_csv_asset(asset["assetId"]).is_file())
        self.assertTrue(storage.csv_asset_metadata_path(asset["assetId"]).is_file())

        repeated = json.loads((await upload_csv(2, CSV_2D, "rates.csv"))[1])
        self.assertEqual(repeated["assetId"], asset["assetId"])

        changed = json.loads((await upload_csv(2, b"-2,0.2\n2,0.7\n", "rates.csv"))[1])
        self.assertNotEqual(changed["assetId"], asset["assetId"])

        fetched = json_body(await asgi_request(f"/api/csv-assets/{asset['assetId']}"))
        self.assertEqual(fetched["rowCount"], 5)
        self.assertEqual(fetched["fileName"], "rates.csv")

    async def test_upload_rejects_bad_names_schemas_and_oversize(self):
        self.assertEqual((await upload_csv(2, CSV_2D, "../rates.csv"))[0], 422)
        self.assertEqual((await upload_csv(2, CSV_3D))[0], 422)
        self.assertEqual((await upload_csv(2, b"0,nan\n1,0.5\n"))[0], 422)
        self.assertEqual((await upload_csv(3, CSV_3D))[0], 201)

        too_large = b"0,1\n" * ((16 * 1024 * 1024) // 4 + 1)
        status, _body = await upload_csv(2, too_large)
        self.assertEqual(status, 413)


class CsvProjectValidationTests(unittest.TestCase):
    def setUp(self):
        storage.initialize()

    def asset(self, dimension: int, body: bytes, name: str = "rates.csv") -> dict:
        import hashlib
        asset_id = hashlib.sha256(body).hexdigest()
        destination = storage.csv_asset_path(asset_id)
        if not destination.exists():
            destination.write_bytes(body)
        metadata = {"assetId": asset_id, "fileName": name, **inspect_rate_csv(body, dimension)}
        storage.atomic_json(storage.csv_asset_metadata_path(asset_id), metadata)
        return metadata

    def test_valid_step_requires_an_explicit_interpolation_mode(self):
        asset = self.asset(2, CSV_2D)
        project = base_project(2)
        project["steps"] = [csv_step(asset, 2)]
        parsed, warnings = validate_project(project, kind="simulate")
        self.assertEqual(parsed.steps[0].interpolationMode, "LINEAR")
        self.assertFalse(any("interpolation" in warning for warning in warnings))

        missing = copy.deepcopy(project)
        del missing["steps"][0]["interpolationMode"]
        with self.assertRaises(ProjectInputError) as ctx:
            validate_project(missing, kind="simulate")
        self.assertTrue(any("interpolationMode" in issue.path for issue in ctx.exception.issues))

        custom = copy.deepcopy(project)
        custom["steps"][0]["interpolationMode"] = "CUSTOM"
        with self.assertRaises(ProjectInputError):
            validate_project(custom, kind="simulate")

        extra = copy.deepcopy(project)
        extra["steps"][0]["customInterpolator"] = "lambda coordinate: 1.0"
        with self.assertRaises(ProjectInputError):
            validate_project(extra, kind="simulate")

    def test_direction_offset_and_component_bounds(self):
        asset = self.asset(2, CSV_2D)
        project = base_project(2)
        for label, overrides in {
            "zero direction": {"direction": [0, 0, 0]},
            "out of range direction": {"direction": [0, 2, 0]},
            "2D vertical direction": {"direction": [0, 0, 1]},
            "2D second offset": {"offset": [0, 0.5]},
            "offset budget": {"offset": [1e7, 0]},
            "zero components": {"isotropicComponent": 0.0, "directionalComponent": 0.0},
            "idw without neighbors": {"interpolationMode": "IDW"},
            "linear with neighbors": {"interpolationMode": "LINEAR", "idwNeighbors": 4},
        }.items():
            with self.subTest(label=label):
                project["steps"] = [csv_step(asset, 2, **overrides)]
                with self.assertRaises(ProjectInputError):
                    validate_project(project, kind="simulate")

    def test_asset_dimension_linear_mode_and_idw_bounds_are_enforced(self):
        asset_3d = self.asset(3, CSV_3D, "rates3.csv")
        project = base_project(2)
        project["steps"] = [csv_step(asset_3d, 2)]
        with self.assertRaises(ProjectInputError) as ctx:
            validate_project(project, kind="simulate")
        self.assertTrue(any("uploaded for 3D" in issue.message for issue in ctx.exception.issues))

        unsorted = self.asset(2, b"1,0.5\n0,0.4\n2,0.3\n", "unsorted.csv")
        project = base_project(2)
        project["steps"] = [csv_step(unsorted, 2)]
        with self.assertRaises(ProjectInputError) as ctx:
            validate_project(project, kind="simulate")
        self.assertTrue(any(issue.path.endswith("interpolationMode") for issue in ctx.exception.issues))

        incomplete = self.asset(3, b"0,0,0.5\n0,2,0.5\n2,0,0.5\n", "incomplete.csv")
        project = base_project(3)
        project["steps"] = [csv_step(incomplete, 3)]
        with self.assertRaises(ProjectInputError) as ctx:
            validate_project(project, kind="simulate")
        self.assertTrue(any("complete rectangular" in issue.message for issue in ctx.exception.issues))

        project = base_project(2)
        project["steps"] = [csv_step(unsorted, 2, interpolationMode="IDW", idwNeighbors=4)]
        with self.assertRaises(ProjectInputError) as ctx:
            validate_project(project, kind="simulate")
        self.assertTrue(any(issue.path.endswith("idwNeighbors") for issue in ctx.exception.issues))

    def test_missing_asset_and_hash_change(self):
        asset_a = self.asset(2, CSV_2D)
        asset_b = self.asset(2, b"-2,0.2\n-1,0.3\n0,0.4\n1,0.5\n2,0.7\n", "rates.csv")
        first = base_project(2)
        first["steps"] = [csv_step(asset_a, 2)]
        second = copy.deepcopy(first)
        second["steps"][0]["assetId"] = asset_b["assetId"]
        first_hash = requested_input_hash(validate_project(first)[0])
        self.assertNotEqual(first_hash, requested_input_hash(validate_project(second)[0]))

        missing = base_project(2)
        missing["steps"] = [csv_step({**asset_a, "assetId": "f" * 64}, 2)]
        with self.assertRaises(ProjectInputError) as ctx:
            validate_project(missing, kind="simulate")
        self.assertTrue(any(issue.path.endswith("assetId") for issue in ctx.exception.issues))

    def test_save_reopen_keeps_the_asset_reference(self):
        asset = self.asset(2, CSV_2D)
        project = base_project(2)
        project["steps"] = [csv_step(asset, 2)]
        normalized = validate_project(project)[0].model_dump(mode="json", exclude_none=True)
        record = storage.create_project(normalized)
        reopened = storage.read_json(storage.project_path(record["id"]))["project"]
        self.assertEqual(reopened["steps"][0]["assetId"], asset["assetId"])
        self.assertEqual(reopened["steps"][0]["fileName"], asset["fileName"])
        parsed, _warnings = validate_project(reopened, kind="simulate")
        self.assertEqual(parsed.steps[0].model, "csvProcess")


class CsvNativeReferenceTests(unittest.IsolatedAsyncioTestCase):
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

    async def reference(self, project: dict, asset_id: str, label: str) -> dict:
        input_path = REFERENCE_DIR / f"{label}-input.json"
        output_path = REFERENCE_DIR / f"{label}-direct-reference.json"
        guard_path = REFERENCE_DIR / f"{label}-reference-guard.json"
        log_path = REFERENCE_DIR / f"{label}-reference.log"
        input_path.write_text(json.dumps(project, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        command = [str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/bounded_child.py"),
                   "--cwd", str(ROOT), "--record", str(guard_path), "--log-path", str(log_path),
                   "--timeout-seconds", "90", "--max-rss-mib", "2048", "--max-log-bytes", "2097152", "--",
                   str(ROOT / ".venv/bin/python"), str(ROOT / "tests/fixtures/csv_direct_reference.py"),
                   str(input_path), str(storage.csv_asset_path(asset_id)), str(output_path)]
        completed = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.DEVNULL)
        self.assertEqual(await completed.wait(), 0, log_path.read_text(encoding="utf-8", errors="replace"))
        return json.loads(output_path.read_text(encoding="utf-8"))

    async def snapshot(self, run_id: str, step_id: str) -> dict:
        snapshot_store.ensure_materialized(storage.run_path(run_id), step_id)
        return json_body(await asgi_request(f"/api/runs/{run_id}/snapshots/{step_id}"))

    async def test_2d_and_3d_runs_match_a_direct_native_reference_and_replay(self):
        for dimension, body in ((2, CSV_2D), (3, CSV_3D)):
            with self.subTest(dimension=dimension):
                asset = json.loads((await upload_csv(dimension, body, f"rates{dimension}.csv"))[1])
                project = base_project(dimension)
                project["steps"] = [csv_step(asset, dimension)]
                run_id, info = await self.execute(project)

                manifest = storage.read_json(storage.run_path(run_id) / "manifest.json")
                self.assertEqual(manifest["plannedExecution"],
                                 [{"stepId": "step_csv_0001", "engine": "not-applicable", "seed": None}])
                self.assertEqual(manifest["csvAssetIds"], [asset["assetId"]])
                execution = manifest["actualExecution"][-1]["actualParameters"]["csvProcess"]
                self.assertEqual(execution["nativeClass"], "CSVFileProcess")
                self.assertEqual(execution["assetSha256"], asset["assetId"])
                self.assertEqual(execution["interpolationMode"], "LINEAR")
                self.assertEqual(execution["dimension"], dimension)

                direct = await self.reference(project, asset["assetId"], f"csv-{dimension}d")
                snapshot = await self.snapshot(run_id, "step_csv_0001")
                expected = direct["step_csv_0001"]
                key = "lines" if dimension == 2 else "triangles"
                self.assertEqual(snapshot[key], expected["cells"])
                self.assertEqual(snapshot["cellData"]["materialIds"], expected["materialIds"])
                self.assertEqual(len(snapshot["points"]), len(expected["points"]))
                error = max((math.dist(a, b) for a, b in zip(snapshot["points"], expected["points"])), default=0.0)
                self.assertLessEqual(error, 1e-6, f"{dimension}D maximum coordinate error {error}")
                self.assertGreater(max(point[1 if dimension == 2 else 2] for point in snapshot["points"]), 0.2)

                replay = json_body(await asgi_request(f"/api/runs/{run_id}/replay-project"))
                replay_response = await asgi_request("/api/runs/replay", method="POST", payload=replay)
                self.assertEqual(replay_response[0], 202, replay_response[2])
                replay_id = json_body(replay_response)["runId"]
                await self.wait(replay_id)
                replayed = await self.snapshot(replay_id, "step_csv_0001")
                for field in ("points", "lines" if dimension == 2 else "triangles", "cellData", "materialIdsByLevelSetIndex"):
                    self.assertEqual(snapshot[field], replayed[field], field)

                # The content-addressed store is the replay input: if the table
                # disappears, replay must fail before a worker starts.
                asset_path = storage.csv_asset_path(asset["assetId"])
                backup = asset_path.with_suffix(".csv.replay-test")
                shutil.move(asset_path, backup)
                try:
                    rejected = await asgi_request("/api/runs/replay", method="POST", payload=replay)
                    self.assertEqual(rejected[0], 422, rejected[2])
                finally:
                    shutil.move(backup, asset_path)

    async def test_2d_idw_mode_matches_a_direct_native_reference(self):
        asset = json.loads((await upload_csv(2, b"1,0.5\n0,0.3\n2,0.2\n", "idw.csv"))[1])
        self.assertFalse(asset["linearCompatible"])
        project = base_project(2)
        project["steps"] = [csv_step(asset, 2, interpolationMode="IDW", idwNeighbors=3)]
        run_id, _info = await self.execute(project)
        direct = await self.reference(project, asset["assetId"], "csv-2d-idw")
        snapshot = await self.snapshot(run_id, "step_csv_0001")
        expected = direct["step_csv_0001"]
        self.assertEqual(snapshot["lines"], expected["cells"])
        error = max((math.dist(a, b) for a, b in zip(snapshot["points"], expected["points"])), default=0.0)
        self.assertLessEqual(error, 1e-6)
        execution = storage.read_json(storage.run_path(run_id) / "manifest.json")["actualExecution"][-1]["actualParameters"]["csvProcess"]
        self.assertEqual(execution["interpolationMode"], "IDW")
        self.assertEqual(execution["idwNeighbors"], 3)

    async def test_2d_mask_material_is_derived_like_ion_beam(self):
        trench = {"kind": "trench", "width": 1.5, "depth": 0.8, "taperAngle": 0.0,
                  "maskHeight": 0.5, "maskTaperAngle": 0.0, "substrateMaterial": "Si", "maskMaterial": "Mask"}
        asset = json.loads((await upload_csv(2, b"-2,0.2\n2,0.2\n", "mask.csv"))[1])
        project = base_project(2, trench)
        project["steps"] = [csv_step(asset, 2)]
        run_id, _info = await self.execute(project)
        execution = storage.read_json(storage.run_path(run_id) / "manifest.json")["actualExecution"][-1]["actualParameters"]["csvProcess"]
        self.assertEqual(execution["maskMaterials"], ["Mask"])
        self.assertEqual(execution["maskMaterialIds"], [0])
        snapshot = await self.snapshot(run_id, "step_csv_0001")
        self.assertIn(0, set(snapshot["cellData"]["materialIds"]))


if __name__ == "__main__":
    unittest.main()
