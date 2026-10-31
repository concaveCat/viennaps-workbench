"""Cell-set regeneration / conservative remap for the 4.7.0 field steps.

The native ``DenseCellSet`` is generated from the level sets. A surface-moving
step between two field steps therefore invalidates it. The worker regenerates a
native cell set on the captured grid and remaps every scalar field by absolute
lattice position (no interpolation, no silent redistribution).

This module pins

* the pure-Python mapping (same-grid copy, integer-shift overlap, mass
  accounting, refusals) without needing a native runtime;
* real 2D/3D ``implant → etch → anneal → analysis`` runs that record the remap;
* a deposition that keeps the grid and the dopant mass;
* a deep etch that has to grow the grid downwards (conservative overlap);
* replay determinism of the remap record and the field digests.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import unittest
from array import array
from itertools import product

from backend.field_remap import (CellGrid, GridRemapError,  # noqa: E402
                                 check_captured_grid, remap_cell_fields)
from backend.main import app  # noqa: E402
from backend.validation import validate_project  # noqa: E402
from tests.test_api_integration import asgi_request, json_body  # noqa: E402
from tests.test_implant import (anneal_step, analysis_step,  # noqa: E402
                                implant_step, project)
from backend import storage  # noqa: E402
import backend.snapshots as snapshot_store  # noqa: E402


def make_grid(*, dimension: int = 2, delta: float = 0.5,
              origin: tuple[float, float, float] = (0.0, 0.0, 0.0),
              shape: tuple[int, int, int] = (4, 4, 1)) -> CellGrid:
    return CellGrid(dimension=dimension, delta=delta, origin=origin, shape=shape)


def grid_nodes(grid: CellGrid) -> list[list[float]]:
    axes = range(int(grid.dimension))
    ranges = [range(grid.shape[axis] + 1) for axis in axes]
    points = []
    for index in product(*ranges):
        point = [0.0, 0.0, 0.0]
        for axis in axes:
            point[axis] = grid.origin[axis] + index[axis] * grid.delta
        points.append(point)
    return points


def solid_material(grid: CellGrid, material: int = 10) -> list[float]:
    return [float(material)] * grid.cell_count


def surface_step(kind: str, *, rate: float, duration: float, **extra) -> dict:
    step = {
        "id": f"{kind}_step_0001", "name": kind, "enabled": True,
        "operation": kind, "model": "isotropic", "rateMagnitude": rate,
        "duration": duration, "rateMode": "uniform",
    }
    if kind == "deposit":
        step["depositMaterial"] = "SiO2"
    step.update(extra)
    return step


class FieldRemapUnitTests(unittest.TestCase):
    def test_grid_round_trips_through_native_node_points(self) -> None:
        grid = make_grid(dimension=2, delta=0.25, origin=(-0.5, -0.75, 0.0),
                         shape=(5, 4, 1))
        parsed = CellGrid.from_points(grid_nodes(grid), dimension=2, delta=0.25)
        self.assertEqual(parsed.signature(), grid.signature())
        self.assertEqual(parsed.cell_count, 20)
        self.assertEqual(parsed.index(1, 2, 0), 1 + 5 * 2)
        self.assertEqual(parsed.coordinates(1 + 5 * 2), (1, 2, 0))

    def test_from_points_rejects_a_gap_in_the_lattice(self) -> None:
        points = grid_nodes(make_grid(shape=(3, 3, 1)))
        points = [point for point in points if point[0] != 0.5]
        with self.assertRaises(GridRemapError):
            CellGrid.from_points(points, dimension=2, delta=0.5)

    def test_same_grid_is_an_exact_ordered_copy(self) -> None:
        grid = make_grid()
        values = [float(index) for index in range(grid.cell_count)]
        result = remap_cell_fields(
            grid, grid, old_values={"dopant": values},
            old_material=solid_material(grid), new_material=solid_material(grid),
            cover_material_id=3, labels=["dopant"], source_step_id="etch_1")
        self.assertEqual(result.labels["dopant"], values)
        record = result.record
        self.assertEqual(record["method"], "same-grid-index-copy")
        self.assertTrue(record["sameGrid"])
        self.assertEqual(record["remappedCells"], grid.cell_count)
        self.assertEqual(record["newCells"], 0)
        self.assertEqual(record["removedCells"], 0)
        self.assertEqual(record["mass"]["dopant"]["before"], sum(values) * 0.25)
        self.assertEqual(record["mass"]["dopant"]["after"], sum(values) * 0.25)
        self.assertEqual(record["mass"]["dopant"]["lost"], 0.0)
        self.assertEqual(record["labelsPreserved"], True)
        self.assertEqual(record["oldGrid"]["shape"], [4, 4, 1])

    def test_integer_shift_copies_by_absolute_position(self) -> None:
        old = make_grid(shape=(4, 4, 1))
        # New grid starts one cell higher: new row k maps to old row k + 1.
        new = make_grid(origin=(0.0, 0.5, 0.0), shape=(4, 4, 1))
        values = [float(index) for index in range(old.cell_count)]
        result = remap_cell_fields(
            old, new, old_values={"dopant": values},
            old_material=solid_material(old), new_material=solid_material(new),
            cover_material_id=3, labels=["dopant"])
        nx = old.shape[0]
        remapped = result.labels["dopant"]
        self.assertEqual(result.record["method"],
                         "conservative-axis-aligned-integer-overlap")
        self.assertEqual(result.record["cellOffset"], {"x": 0, "y": 1, "z": 0})
        self.assertEqual(result.record["remappedCells"], 12)
        self.assertEqual(result.record["removedByGrid"], 4)
        self.assertEqual(result.record["newCells"], 4)
        for k in range(3):
            self.assertEqual(remapped[nx * k:nx * (k + 1)],
                             values[nx * (k + 1):nx * (k + 2)])
        self.assertEqual(remapped[nx * 3:], [0.0] * nx)
        self.assertEqual(result.record["mass"]["dopant"]["lost"], sum(values[:nx]) * 0.25)

    def test_removed_material_records_mass_loss_without_redistribution(self) -> None:
        grid = make_grid(shape=(4, 4, 1))
        values = [1.0] * grid.cell_count
        new_material = solid_material(grid)
        cover = 3
        nx = grid.shape[0]
        for index in range(grid.index(0, 3, 0), grid.index(nx - 1, 3, 0) + 1):
            new_material[index] = float(cover)
        result = remap_cell_fields(
            grid, grid, old_values={"dopant": values},
            old_material=solid_material(grid), new_material=new_material,
            cover_material_id=cover, labels=["dopant"])
        self.assertEqual(result.labels["dopant"][grid.index(0, 3, 0)], 0.0)
        self.assertEqual(result.labels["dopant"][grid.index(0, 2, 0)], 1.0)
        self.assertEqual(result.record["removedByMaterial"], nx)
        self.assertEqual(result.record["removedCells"], nx)
        self.assertEqual(result.record["mass"]["dopant"]["lost"], nx * 0.25)
        self.assertEqual(
            result.record["mass"]["dopant"]["after"],
            result.record["mass"]["dopant"]["before"] - nx * 0.25)

    def test_reactivated_cells_start_from_the_preserved_value(self) -> None:
        grid = make_grid(shape=(4, 4, 1))
        values = [2.0] * grid.cell_count
        old_material = solid_material(grid)
        nx = grid.shape[0]
        for index in range(grid.index(0, 3, 0), grid.index(nx - 1, 3, 0) + 1):
            old_material[index] = 3.0
        result = remap_cell_fields(
            grid, grid, old_values={"dopant": values},
            old_material=old_material, new_material=solid_material(grid),
            cover_material_id=3, labels=["dopant"])
        self.assertEqual(result.record["reactivatedCells"], nx)
        self.assertEqual(result.record["removedCells"], 0)
        self.assertEqual(result.labels["dopant"][grid.index(0, 3, 0)], 2.0)

    def test_non_integer_shift_is_refused(self) -> None:
        old = make_grid()
        new = make_grid(origin=(0.0, 0.2, 0.0))
        with self.assertRaises(GridRemapError) as context:
            remap_cell_fields(
                old, new, old_values={"dopant": [1.0] * old.cell_count},
                old_material=solid_material(old), new_material=solid_material(new),
                cover_material_id=3, labels=["dopant"])
        self.assertIn("conservative grid remap not implemented", str(context.exception))

    def test_lateral_grid_change_is_refused(self) -> None:
        old = make_grid()
        for changed in (make_grid(origin=(0.5, 0.0, 0.0)),
                        make_grid(shape=(5, 4, 1)),
                        make_grid(delta=0.25, shape=(8, 8, 1))):
            with self.subTest(changed=changed.signature()):
                with self.assertRaises(GridRemapError) as context:
                    check_captured_grid(old, changed)
                self.assertIn("conservative grid remap not implemented",
                              str(context.exception))

    def test_grid_growth_is_conservative_and_new_cells_are_zero(self) -> None:
        old = make_grid(shape=(4, 3, 1))
        new = make_grid(origin=(0.0, -0.5, 0.0), shape=(4, 5, 1))
        values = [float(index + 1) for index in range(old.cell_count)]
        result = remap_cell_fields(
            old, new, old_values={"dopant": values},
            old_material=solid_material(old), new_material=solid_material(new),
            cover_material_id=3, labels=["dopant"])
        nx = old.shape[0]
        self.assertEqual(result.record["cellOffset"], {"x": 0, "y": -1, "z": 0})
        self.assertEqual(result.record["newCells"], 2 * nx)
        self.assertEqual(result.record["removedCells"], 0)
        self.assertEqual(result.labels["dopant"][:nx], [0.0] * nx)
        self.assertEqual(result.labels["dopant"][nx:4 * nx], values)
        self.assertEqual(result.labels["dopant"][4 * nx:], [0.0] * nx)
        self.assertEqual(result.record["mass"]["dopant"]["lost"], 0.0)

    def test_remap_record_is_deterministic(self) -> None:
        grid = make_grid()
        values = [float(index % 5) for index in range(grid.cell_count)]
        materials = solid_material(grid)
        first = remap_cell_fields(
            grid, grid, old_values={"dopant": values}, old_material=materials,
            new_material=materials, cover_material_id=3, labels=["dopant"])
        second = remap_cell_fields(
            grid, grid, old_values={"dopant": values}, old_material=materials,
            new_material=materials, cover_material_id=3, labels=["dopant"])
        self.assertEqual(json.dumps(first.record, sort_keys=True),
                         json.dumps(second.record, sort_keys=True))
        self.assertEqual(first.record["fieldDigests"],
                         {"dopant": hashlib.sha256(
                             array("d", values).tobytes()).hexdigest()})


class FieldRemapWorkflow(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.lifespan = app.router.lifespan_context(app)
        await self.lifespan.__aenter__()

    async def asyncTearDown(self) -> None:
        await self.lifespan.__aexit__(None, None, None)

    async def wait(self, run_id: str) -> dict:
        for _ in range(2400):
            result = json_body(await asgi_request("/api/runs/" + run_id))
            if result["status"] in ("succeeded", "failed", "cancelled", "timed_out",
                                    "interrupted"):
                return result
            await asyncio.sleep(0.05)
        self.fail("native child exceeded acceptance timeout")

    async def run_project(self, value: dict) -> tuple[str, dict]:
        response = await asgi_request("/api/runs", method="POST",
                                      payload={"kind": "simulate", "project": value})
        self.assertEqual(response[0], 202, response[2])
        run_id = json_body(response)["runId"]
        result = await self.wait(run_id)
        self.assertEqual(result["status"], "succeeded", result.get("failureReason"))
        return run_id, storage.read_json(storage.run_path(run_id) / "manifest.json")

    def summary(self, run_id: str, step_id: str = "analysis_step_01") -> dict:
        return storage.read_json(storage.run_path(run_id) / "artifacts" /
                                 f"{step_id}-cell-set-fields.json")

    def snapshot(self, run_id: str, step_id: str) -> dict:
        snapshot_store.ensure_materialized(storage.run_path(run_id), step_id)
        return storage.read_json(storage.run_path(run_id) / f"{step_id}.json")

    def assert_mass_balance(self, record: dict) -> None:
        for label, mass in record["mass"].items():
            with self.subTest(label=label):
                self.assertAlmostEqual(mass["before"] - mass["after"], mass["lost"],
                                       places=6)
                self.assertAlmostEqual(
                    record["mass"][label]["after"],
                    record["mass"][label]["before"] - record["mass"][label]["lost"],
                    places=6)

    async def test_implant_etch_anneal_analysis_2d_records_the_remap(self) -> None:
        steps = [implant_step(), surface_step("etch", rate=0.5, duration=0.1),
                 anneal_step(), analysis_step()]
        run_id, _manifest = await self.run_project(project(2, steps=steps))
        payload = self.summary(run_id)
        setup = payload["cellSetSetup"]
        self.assertEqual(setup["remapCount"], 1)
        record = setup["remaps"][0]
        self.assertEqual(record["sourceStepId"], "etch_step_0001")
        self.assertEqual(record["remapOfStepId"], "implant_step_01")
        self.assertEqual(record["method"], "same-grid-index-copy")
        self.assertTrue(record["exact"])
        self.assertEqual(record["oldCellCount"], record["newCellCount"])
        self.assertEqual(record["remappedCells"], record["oldCellCount"])
        self.assertEqual(record["newCells"], 0)
        self.assertGreater(record["removedCells"], 0)
        self.assertEqual(record["removedByGrid"], 0)
        self.assertGreater(record["mass"]["P_total"]["lost"], 0.0)
        self.assertLess(record["mass"]["P_total"]["after"],
                        record["mass"]["P_total"]["before"])
        self.assert_mass_balance(record)
        self.assertEqual(record["labels"], ["P_total", "P_damage", "P_damage_last"])
        self.assertEqual(record["oldGrid"], record["capturedGrid"])
        # The remapped labels are still present and the analysis still runs.
        self.assertIn("P_active", payload["fieldDigests"])
        self.assertIn("net_doping", payload["fieldDigests"])
        self.assertGreater(payload["process"]["analysis"]["sheetResistance"][0]
                           ["sheetResistanceOhmPerSquare"], 0.0)
        # The surface snapshot itself is not part of the field grid.
        snapshot = self.snapshot(run_id, "etch_step_0001")
        self.assertEqual(snapshot["stepId"], "etch_step_0001")
        self.assertNotIn("cellSetProcess", snapshot)
        # Some dopant survived the etch: mass is not silently redistributed.
        self.assertGreater(record["mass"]["P_total"]["after"], 0.0)

    async def test_three_dimensional_etch_remap_uses_the_same_native_path(self) -> None:
        steps = []
        for step in (implant_step(), surface_step("etch", rate=0.5, duration=0.1),
                     anneal_step(), analysis_step()):
            step = copy.deepcopy(step)
            if "cellSet" in step:
                step["cellSet"]["height"] = 0.2
                step["cellSet"]["substrateDepth"] = 0.3
            steps.append(step)
        value = project(3, steps=steps)
        value["domain"] = {"gridDelta": 0.02, "xExtent": 0.4, "yExtent": 0.4,
                           "boundaryXY": "reflective"}
        run_id, _manifest = await self.run_project(value)
        payload = self.summary(run_id)
        record = payload["cellSetSetup"]["remaps"][0]
        self.assertEqual(record["sourceStepId"], "etch_step_0001")
        self.assertEqual(record["method"], "same-grid-index-copy")
        self.assertEqual(record["oldCellCount"], record["newCellCount"])
        self.assertGreater(record["removedCells"], 0)
        self.assert_mass_balance(record)
        self.assertEqual(payload["dimension"], 3)
        self.assertEqual(payload["grid"]["nativeElementType"], "hexahedron")
        self.assertEqual(record["cellOffset"], {"x": 0, "y": 0, "z": 0})
        analysis = self.snapshot(run_id, "analysis_step_01")["cellSetProcess"]
        self.assertEqual(analysis["cellSet"]["remapCount"], 1)
        self.assertEqual(analysis["cellSet"]["periodicBoundary"], [False, False, False])

    async def test_implant_deposit_anneal_preserves_the_dopant_mass(self) -> None:
        steps = [implant_step(),
                 surface_step("deposit", rate=0.5, duration=0.1),
                 anneal_step(), analysis_step()]
        run_id, _manifest = await self.run_project(project(2, steps=steps))
        payload = self.summary(run_id)
        record = payload["cellSetSetup"]["remaps"][0]
        self.assertEqual(record["sourceStepId"], "deposit_step_0001")
        self.assertEqual(record["method"], "same-grid-index-copy")
        self.assertEqual(record["removedCells"], 0)
        self.assertEqual(record["newCells"], 0)
        self.assertEqual(record["mass"]["P_total"]["lost"], 0.0)
        self.assertEqual(record["mass"]["P_total"]["after"],
                         record["mass"]["P_total"]["before"])
        self.assertEqual(record["mass"]["P_damage"]["lost"], 0.0)
        self.assertEqual(payload["cellSetSetup"]["coverMaterial"], "GAS")
        self.assertIn("P_active", payload["fieldDigests"])

    async def test_deep_etch_grows_the_grid_conservatively(self) -> None:
        steps = [implant_step(cellSet={"height": 0.5, "substrateDepth": 0.2,
                                       "coverMaterial": "GAS", "periodic": False,
                                       "format": "both"}),
                 surface_step("etch", rate=1.0, duration=0.3),
                 anneal_step(cellSet={"height": 0.5, "substrateDepth": 0.2,
                                      "coverMaterial": "GAS", "periodic": False,
                                      "format": "both"}),
                 analysis_step(cellSet={"height": 0.5, "substrateDepth": 0.2,
                                        "coverMaterial": "GAS", "periodic": False,
                                        "format": "both"})]
        run_id, _manifest = await self.run_project(project(2, steps=steps))
        payload = self.summary(run_id)
        record = payload["cellSetSetup"]["remaps"][0]
        self.assertEqual(record["method"],
                         "conservative-axis-aligned-integer-overlap")
        self.assertFalse(record["sameGrid"])
        self.assertGreater(record["newCellCount"], record["oldCellCount"])
        self.assertGreater(record["newCells"], 0)
        self.assertEqual(record["removedByGrid"], 0)
        self.assertLess(record["newGrid"]["bottom"], record["capturedGrid"]["bottom"])
        self.assertEqual(record["newGrid"]["top"], record["capturedGrid"]["top"])
        self.assert_mass_balance(record)

    async def test_replay_reproduces_the_remap_record(self) -> None:
        steps = [implant_step(), surface_step("etch", rate=0.5, duration=0.1),
                 anneal_step(), analysis_step()]
        run_id, _manifest = await self.run_project(project(2, steps=steps))
        original = self.summary(run_id)
        replay = json_body(await asgi_request(f"/api/runs/{run_id}/replay-project"))
        submit = await asgi_request("/api/runs/replay", method="POST", payload=replay)
        self.assertEqual(submit[0], 202, submit[2])
        replay_id = json_body(submit)["runId"]
        result = await self.wait(replay_id)
        self.assertEqual(result["status"], "succeeded", result.get("failureReason"))
        replayed = self.summary(replay_id)
        self.assertEqual(original["cellSetSetup"]["remaps"],
                         replayed["cellSetSetup"]["remaps"])
        self.assertEqual(original["fieldDigests"], replayed["fieldDigests"])
        self.assertEqual(original["process"]["analysis"]["sheetResistance"],
                         replayed["process"]["analysis"]["sheetResistance"])


class FieldRemapSchemaTests(unittest.TestCase):
    def test_a_surface_step_between_field_steps_is_accepted(self) -> None:
        steps = [implant_step(), surface_step("etch", rate=0.5, duration=0.1),
                 anneal_step(), analysis_step()]
        parsed, warnings = validate_project(project(2, steps=steps))
        self.assertEqual([step["model"] for step in
                          parsed.model_dump(mode="json")["steps"]],
                         ["implant", "isotropic", "anneal", "analysis"])
        self.assertEqual(warnings, [])

    def test_the_shared_grid_parameters_are_still_validated(self) -> None:
        mismatched = [implant_step(),
                      surface_step("etch", rate=0.5, duration=0.1),
                      anneal_step(cellSet={"height": 0.3, "substrateDepth": 0.4,
                                           "coverMaterial": "GAS", "periodic": False,
                                           "format": "both"})]
        with self.assertRaises(Exception) as context:
            validate_project(project(2, steps=mismatched))
        self.assertIn("height must match", str(context.exception))

    def test_analysis_before_an_implant_is_still_refused(self) -> None:
        steps = [analysis_step(), surface_step("etch", rate=0.5, duration=0.1),
                 implant_step()]
        with self.assertRaises(Exception) as context:
            validate_project(project(2, steps=steps))
        self.assertIn("earlier", str(context.exception))


if __name__ == "__main__":
    unittest.main()
