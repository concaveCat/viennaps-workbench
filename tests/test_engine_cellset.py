"""Generic native DenseCellSet output (outputSettings.cellSet).

The installed ViennaPS 4.6.2/viennacs pair only exposes
``Domain.generateCellSet(position, coverMaterial, isAboveSurface)``; the third
argument is *not* a periodic flag. The worker therefore builds a fresh
``DenseCellSet`` from the domain level sets with ``isAboveSurface=true`` and
applies the real ``setPeriodicBoundary`` separately. These tests pin the
request contract, the pre-write limits, real 2D/3D artifacts, determinism and
the unchanged historical hash when the block is omitted.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from backend import preflight, storage
from backend.hashing import requested_input, requested_input_hash
from backend.main import app
from backend.models import parse_project
from backend.validation import ProjectInputError, validate_project
from backend import snapshots as snapshot_store
from tests.test_api_integration import asgi_request, json_body

ROOT = Path(__file__).resolve().parents[1]

#: Hash of the pre-cell-set schema-7 project below. ``cellSet`` is optional and
#: excluded from the canonical request when omitted; this value must not move.
HISTORICAL_HASH = "a53475d600ac07cd0358513b16cb0ab84758c848f5c2399ae6b5cf987ca5602a"


def solver() -> dict:
    return {
        "timeStepRatio": 0.25, "raysPerPoint": 16, "cpuThreads": 1,
        "random": {"mode": "fixed", "seed": 1234}, "enginePreference": "cpu", "timeoutSeconds": 60,
        "advection": {"spatialScheme": "ENGQUIST_OSHER_1ST_ORDER", "temporalScheme": "FORWARD_EULER",
                      "adaptiveTimeStepping": False, "adaptiveTimeStepSubdivisions": 20,
                      "calculateIntermediateVelocities": False, "checkDissipation": True,
                      "dissipationAlpha": 1.0, "ignoreVoids": False, "velocityOutput": False},
        "rayTracing": {"diskRadius": 0.0, "ignoreFluxBoundaries": False, "maxBoundaryHits": 80,
                       "maxReflections": 3, "minNodeDistanceFactor": 0.05,
                       "normalizationType": "SOURCE", "smoothingNeighbors": 1},
    }


def project(dimension: int = 2, *, cell_set: dict | None = None, boundary: str = "reflective",
            grid_delta: float = 0.1, x_extent: float = 2.0, y_extent: float = 1.0,
            steps: int = 1) -> dict:
    value = {
        "schemaVersion": 7, "name": f"cell set {dimension}D", "dimension": dimension,
        "units": {"length": "um", "time": "s", "rate": "um/s", "angle": "deg"},
        "domain": {"gridDelta": grid_delta, "xExtent": x_extent,
                   **({"xBoundary": boundary} if dimension == 2
                      else {"yExtent": y_extent, "boundaryXY": boundary})},
        "geometry": {"kind": "plane", "height": 0.2, "substrateMaterial": "Si"},
        "steps": [{"id": f"cellset_etch_{index + 1:02d}", "name": "etch", "enabled": True,
                   "operation": "etch", "model": "isotropic", "rateMagnitude": 0.1,
                   "duration": 0.1, "rateMode": "uniform"} for index in range(steps)],
        "solver": solver(),
        "outputSettings": {"levelSets": False, "levelSetMeshes": False, "surfaceMeshVtk": False,
                           "volumeMesh": False, "nativeMetadata": False},
    }
    if cell_set is not None:
        value["outputSettings"]["cellSet"] = cell_set
    if dimension == 2:
        value["view"] = {"panX": 0.0, "panH": 0.0, "zoom": 1.0, "showGrid": True}
    else:
        value["view3D"] = {"projection": "parallel", "camera": None, "displayMode": "surface",
                           "visibleMaterialIds": [], "opacityByMaterial": {},
                           "clip": {"enabled": False, "axis": "x", "position": 0.0, "keep": "positive"},
                           "section": {"enabled": False, "axis": "y", "position": 0.0}}
    return value


def enabled(**overrides) -> dict:
    value = {"enabled": True, "height": 0.5, "coverMaterial": "GAS", "periodic": False,
             "format": "both"}
    value.update(overrides)
    return value


def normalized(value: dict) -> dict:
    return parse_project(value).model_dump(mode="json", exclude_none=True)


def vtu_counts(path: Path) -> tuple[str, int, int, set[str]]:
    root = ET.fromstring(path.read_text(encoding="utf-8"))
    piece = root.find("UnstructuredGrid/Piece")
    assert piece is not None
    labels = {str(node.attrib.get("Name")) for node in piece.findall("CellData/DataArray")}
    return root.attrib.get("type", ""), int(piece.attrib["NumberOfPoints"]), int(piece.attrib["NumberOfCells"]), labels


class CellSetSchema(unittest.TestCase):
    def test_omitted_block_keeps_the_historical_hash(self) -> None:
        parsed = parse_project(project(2))
        self.assertEqual(requested_input_hash(parsed), HISTORICAL_HASH)
        self.assertNotIn("cellSet", requested_input(parsed)["outputSettings"])
        # Explicitly disabled is a real input and must not collide with omitted.
        disabled = parse_project(project(2, cell_set={"enabled": False}))
        self.assertIn("cellSet", requested_input(disabled)["outputSettings"])
        self.assertNotEqual(requested_input_hash(disabled), HISTORICAL_HASH)
        configured = parse_project(project(2, cell_set=enabled()))
        self.assertNotEqual(requested_input_hash(configured), HISTORICAL_HASH)
        self.assertNotEqual(requested_input_hash(configured), requested_input_hash(disabled))

    def test_strict_fields_and_native_limits(self) -> None:
        cases = [
            ({"enabled": True}, "height"),
            (enabled(height=0.05), "grid interval"),
            (enabled(periodic=True), "periodic"),
            (enabled(scalarLabels=[]), "non-empty"),
            (enabled(scalarLabels=["Material", "Material"]), "duplicates"),
            (enabled(scalarLabels=["Material", "Byproduct"]), "scalarlabels"),
            (enabled(format="csv"), "format"),
            (enabled(extra=True), "extra"),
            (enabled(height=10000.0), "2000000"),
        ]
        for cell_set, fragment in cases:
            with self.subTest(fragment=fragment):
                with self.assertRaises(Exception) as raised:
                    parse_project(project(2, cell_set=cell_set))
                self.assertIn(fragment.lower(), str(raised.exception).lower().replace(",", ""))
        # Built-in catalog materials and the native GAS sentinel are accepted;
        # unknown names are rejected by the same catalog check as step materials.
        for cover in ("GAS", "SiO2", "Si"):
            validate_project(project(2, cell_set=enabled(coverMaterial=cover)), kind="simulate")
        with self.assertRaises(ProjectInputError):
            validate_project(project(2, cell_set=enabled(coverMaterial="NotAMaterial")), kind="simulate")
        # A periodic flag is only meaningful on a periodic domain.
        validate_project(project(2, cell_set=enabled(periodic=True), boundary="periodic"), kind="simulate")
        with self.assertRaises(ProjectInputError):
            validate_project(project(3, cell_set=enabled(periodic=True), boundary="reflective"), kind="simulate")
        validate_project(project(3, cell_set=enabled(periodic=True), boundary="periodic"), kind="simulate")

    def test_preflight_sizes_the_cell_set_and_never_downgrades_it(self) -> None:
        base = normalized(project(2, cell_set=enabled()))
        estimate = preflight.estimate_artifacts(base)
        self.assertGreater(estimate["cellSetCellsUpperBound"], 0)
        optional = [row for row in estimate["contributors"] if row["kind"] == "optionalOutputs"]
        self.assertEqual(len(optional), 1)
        self.assertIn("cellSet", optional[0]["detail"])
        # A low-confidence sibling output must not turn an over-cap cell set
        # into a warning: the cell set is calibrated and is written eagerly.
        heavy = normalized(project(3, cell_set=enabled(height=10.0), grid_delta=0.05,
                                   x_extent=4.0, y_extent=4.0, steps=2))
        heavy["outputSettings"]["levelSets"] = True
        with patch.dict(os.environ, {"WORKBENCH_MAX_ARTIFACT_MIB": "64"}):
            rejected = preflight.estimate_artifacts(heavy)
        self.assertEqual(rejected["confidence"], "low")
        self.assertEqual(rejected["risk"], "reject")
        self.assertFalse(rejected["rejectionDowngraded"])
        self.assertTrue(preflight.should_reject(rejected))
        direct = preflight.rejection_detail(rejected)
        self.assertEqual(direct["code"], "artifact-budget-preflight")
        self.assertIn("64 MiB", direct["message"])

    def test_oversized_cell_set_is_rejected_before_the_worker_starts(self) -> None:
        huge = project(3, cell_set=enabled(height=10000.0), grid_delta=0.1,
                       x_extent=4.0, y_extent=4.0)
        with self.assertRaises(Exception) as raised:
            parse_project(huge)
        self.assertIn("2,000,000", str(raised.exception))


class CellSetWorkflow(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.lifespan = app.router.lifespan_context(app)
        await self.lifespan.__aenter__()

    async def asyncTearDown(self) -> None:
        await self.lifespan.__aexit__(None, None, None)

    async def wait(self, run_id: str) -> dict:
        for _ in range(1200):
            result = json_body(await asgi_request("/api/runs/" + run_id))
            if result["status"] in ("succeeded", "failed", "cancelled", "timed_out", "interrupted"):
                return result
            await asyncio.sleep(0.05)
        self.fail("native child exceeded acceptance timeout")

    async def run_project(self, value: dict) -> tuple[str, dict]:
        response = await asgi_request("/api/runs", method="POST", payload={"kind": "simulate", "project": value})
        self.assertEqual(response[0], 202, response[2])
        run_id = json_body(response)["runId"]
        result = await self.wait(run_id)
        self.assertEqual(result["status"], "succeeded", result.get("failureReason"))
        return run_id, storage.read_json(storage.run_path(run_id) / "manifest.json")

    def snapshot(self, run_id: str, step_id: str) -> dict:
        snapshot_store.ensure_materialized(storage.run_path(run_id), step_id)
        return storage.read_json(storage.run_path(run_id) / (step_id + ".json"))

    def outputs(self, manifest: dict, step_id: str) -> list[dict]:
        row = next(item for item in manifest["snapshots"] if item["stepId"] == step_id)
        return row.get("outputs") or []

    async def test_two_dimensional_artifacts_fields_and_isolation(self) -> None:
        configured = project(2, cell_set=enabled(scalarLabels=["Material"]))
        run_id, manifest = await self.run_project(configured)
        for step_id in ("initial", "cellset_etch_01"):
            rows = self.outputs(manifest, step_id)
            vtu = [row for row in rows if row["kind"] == "cellSet"]
            summary_rows = [row for row in rows if row["kind"] == "cellSetSummary"]
            self.assertEqual(len(vtu), 1, rows)
            self.assertEqual(len(summary_rows), 1, rows)
            vtu_path = storage.run_path(run_id) / vtu[0]["fileName"]
            json_path = storage.run_path(run_id) / summary_rows[0]["fileName"]
            self.assertTrue(vtu_path.is_file() and vtu_path.stat().st_size > 0)
            self.assertTrue(json_path.is_file())
            kind, points, cells, labels = vtu_counts(vtu_path)
            self.assertEqual(kind, "UnstructuredGrid")
            self.assertEqual(labels, {"Material", "FillingFraction"})
            summary = storage.read_json(json_path)
            self.assertEqual(summary["schemaVersion"], 1)
            self.assertEqual(summary["nativeType"], "DenseCellSet")
            self.assertEqual(summary["dimension"], 2)
            self.assertTrue(summary["setup"]["isAboveSurface"])
            self.assertEqual(summary["setup"]["heightAboveSurface"], 0.5)
            self.assertEqual(summary["setup"]["coverMaterial"], "GAS")
            self.assertFalse(summary["setup"]["periodic"])
            self.assertEqual(summary["setup"]["periodicBoundary"], [False, False])
            self.assertEqual(summary["setup"]["scalarLabels"], ["Material"])
            self.assertEqual(summary["numberOfCells"], cells)
            self.assertEqual(summary["numberOfNodes"], points)
            self.assertEqual(summary["grid"]["elements"], cells)
            self.assertEqual(summary["grid"]["elementNodes"], 4)
            self.assertEqual(summary["grid"]["nativeElementType"], "tetra")
            self.assertEqual(summary["grid"]["logicalCellShape"], "quad")
            self.assertEqual(set(summary["fields"]), {"Material"})
            material = summary["fields"]["Material"]
            self.assertEqual(material["count"], cells)
            self.assertEqual(material["materialNames"].get("10"), "Si")
            self.assertEqual(material["materialNames"].get("3"), "GAS")
            self.assertEqual(sum(material["materialCounts"].values()), cells)
            download = await asgi_request(
                f"/api/runs/{run_id}/artifacts/{step_id}?fileName={vtu[0]['fileName']}")
            self.assertEqual(download[0], 200)
            self.assertEqual(download[1].get("content-type"), "application/vnd.vtk+xml")
            download_json = await asgi_request(
                f"/api/runs/{run_id}/artifacts/{step_id}?fileName={summary_rows[0]['fileName']}")
            self.assertEqual(download_json[0], 200)
            self.assertEqual(download_json[1].get("content-type"), "application/json")

        # Optional output must not change the surface payload of the same step.
        baseline_id, baseline_manifest = await self.run_project(project(2))
        self.assertFalse(any(row["kind"] in {"cellSet", "cellSetSummary"}
                             for row in self.outputs(baseline_manifest, "cellset_etch_01")))
        produced = self.snapshot(run_id, "cellset_etch_01")
        baseline = self.snapshot(baseline_id, "cellset_etch_01")
        for key in ("points", "cellData", "surfaceBounds", "materialTops", "measuredRemoval", "measuredAdded"):
            self.assertEqual(produced.get(key), baseline.get(key), key)

    async def test_three_dimensional_vtu_only_and_deterministic_repeat(self) -> None:
        configured = project(3, cell_set=enabled(format="vtu", scalarLabels=["FillingFraction"]))
        first_id, first_manifest = await self.run_project(configured)
        rows = self.outputs(first_manifest, "cellset_etch_01")
        self.assertEqual([row["kind"] for row in rows], ["cellSet"])
        vtu_path = storage.run_path(first_id) / rows[0]["fileName"]
        kind, points, cells, labels = vtu_counts(vtu_path)
        self.assertEqual(kind, "UnstructuredGrid")
        self.assertEqual(labels, {"Material", "FillingFraction"})
        self.assertGreater(cells, 0)
        # 20 x 10 x 6 cells on this 2 x 1 um, 0.1 um plane plus 0.5 um cover.
        self.assertEqual(cells % 200, 0)
        self.assertFalse((storage.run_path(first_id) / "artifacts" / "cellset_etch_01-cell-set.json").exists())
        first_sha = hashlib.sha256(vtu_path.read_bytes()).hexdigest()

        replay = json_body(await asgi_request(f"/api/runs/{first_id}/replay-project"))
        response = await asgi_request("/api/runs/replay", method="POST", payload=replay)
        self.assertEqual(response[0], 202, response[2])
        second_id = json_body(response)["runId"]
        replayed = await self.wait(second_id)
        self.assertEqual(replayed["status"], "succeeded", replayed.get("failureReason"))
        second_manifest = storage.read_json(storage.run_path(second_id) / "manifest.json")
        second_rows = self.outputs(second_manifest, "cellset_etch_01")
        second_path = storage.run_path(second_id) / second_rows[0]["fileName"]
        self.assertEqual(hashlib.sha256(second_path.read_bytes()).hexdigest(), first_sha)
        self.assertEqual(second_rows[0]["numberOfCells"], rows[0]["numberOfCells"])

    async def test_oversized_or_mismatched_requests_are_rejected_before_a_worker_starts(self) -> None:
        cases = [
            project(2, cell_set=enabled(height=0.05)),          # thinner than one cell
            project(3, cell_set=enabled(height=10000.0), grid_delta=0.1,
                    x_extent=4.0, y_extent=4.0),                # above the hard cell limit
            project(2, cell_set=enabled(periodic=True), boundary="reflective"),
            project(2, cell_set=enabled(coverMaterial="NotAMaterial")),
        ]
        for value in cases:
            response = await asgi_request("/api/runs", method="POST",
                                          payload={"kind": "simulate", "project": value})
            self.assertEqual(response[0], 422, response[2])

    async def test_periodic_domain_uses_the_real_cell_set_boundary(self) -> None:
        configured = project(2, cell_set=enabled(periodic=True, format="json"), boundary="periodic")
        run_id, manifest = await self.run_project(configured)
        rows = self.outputs(manifest, "cellset_etch_01")
        self.assertEqual([row["kind"] for row in rows], ["cellSetSummary"])
        summary = storage.read_json(storage.run_path(run_id) / rows[0]["fileName"])
        self.assertTrue(summary["setup"]["periodic"])
        self.assertEqual(summary["setup"]["periodicBoundary"], [True, False])
        self.assertGreater(summary["numberOfCells"], 0)


if __name__ == "__main__":
    unittest.main()
