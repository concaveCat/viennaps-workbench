"""FaradayCageEtching and GeometricTrenchDeposition in the schema-7 allowlist.

Both models are constructed with the real ViennaPS 4.6.2 bindings through
``worker.specialized.build_specialized_model`` and executed with a real native
``ps.Process`` in the only dimension their installed binding survives:
FaradayCageEtching in 3D and GeometricTrenchDeposition in 2D. Controlled probes
showed the 2D FaradayCage path never returning and the 3D
GeometricTrenchDeposition path SIGSEGV, so validation rejects those
dimensions. The tests also pin the native semantics that the catalog cannot
change:

* ``FaradayCageEtching`` wraps ``IBEParameters`` in ``FaradayCageParameters``
  and builds a two-direction periodic cage source. Mask materials are forced
  to a zero plane-wafer rate by the native constructor.
* ``GeometricTrenchDeposition`` is a ViennaLS geometric-advection distribution.
  It grows the selectable ``depositMaterial`` top level set, ignores
  ``trenchWidth`` and the process duration, and evaluates its local radius from
  absolute z with the trench top assumed at z=0.

The two models are marked ``experimental`` in the catalog: no Stage 8/9
direct-reference acceptance row has been archived, so validation emits a
warning instead of claiming verified coverage.
"""
from __future__ import annotations

import asyncio
import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "fixtures"))

import viennaps as ps  # noqa: E402

from backend import storage  # noqa: E402
from backend.main import app  # noqa: E402
from backend.hashing import requested_input_hash  # noqa: E402
from backend.specialized_models import CATALOG, model_spec  # noqa: E402
from backend.validation import ProjectInputError, select_engine, validate_project  # noqa: E402
from stage8_project_factory import make_project  # noqa: E402
from tests.test_api_integration import asgi_request, json_body  # noqa: E402
from worker.specialized import build_specialized_model  # noqa: E402

FARADAY_FIELDS = {
    "cageAngle", "meanEnergy", "sigmaEnergy", "thresholdEnergy", "thetaRMin", "thetaRMax",
    "minAngle", "inflectAngle", "exponent", "n_l", "tiltAngle", "planeWaferRate",
    "redepositionRate", "redepositionThreshold", "cos4YieldA1", "cos4YieldA2",
    "cos4YieldA3", "cos4YieldA4", "materialPlaneWaferRateByMaterial",
}
TRENCH_FIELDS = {"trenchWidth", "trenchDepth", "depositionRate", "bottomMed", "a", "b", "n"}


def material_handle(name: str):
    return getattr(ps.Material, name)


def set_boundary(project: dict, boundary: str) -> None:
    if project["dimension"] == 2:
        project["domain"]["xBoundary"] = boundary
    else:
        project["domain"]["boundaryXY"] = boundary


def faraday_project(dimension: int, *, periodic: bool = True) -> dict:
    """Mask below the top film so the cage etches the exposed SiO2."""
    project = make_project("faradayCage", dimension)
    project["geometry"]["layers"] = [
        {"id": "nitride", "name": "Nitride", "material": "Si3N4", "thickness": 0.4, "role": "mask"},
        {"id": "oxide", "name": "Oxide", "material": "SiO2", "thickness": 0.4, "role": "film"},
    ]
    project["steps"][0]["duration"] = 1.0
    set_boundary(project, "periodic" if periodic else "reflective")
    return project


def geometric_project(dimension: int) -> dict:
    project = make_project("geometricTrenchDeposition", dimension)
    project["geometry"] = {"kind": "plane", "height": 0.0, "substrateMaterial": "Si"}
    project["steps"][0]["depositMaterial"] = "Al2O3"
    return project


def native_stack(dimension: int, boundary: str = "periodic"):
    ps.setDimension(dimension)
    ps.setNumThreads(1)
    ps.Length.setUnit("um")
    ps.Time.setUnit("s")
    delta, extent = 0.2, 2.0
    boundary_type = (ps.BoundaryType.PERIODIC_BOUNDARY if boundary == "periodic"
                     else ps.BoundaryType.REFLECTIVE_BOUNDARY)
    domain = (ps.Domain(delta, extent, boundary_type) if dimension == 2
              else ps.Domain(delta, extent, extent, boundary_type))
    ps.MakePlane(domain, height=0.0, material=ps.Material.Si, addToExisting=False).apply()
    ps.MakePlane(domain, height=0.4, material=ps.Material.Si3N4, addToExisting=True).apply()
    ps.MakePlane(domain, height=0.8, material=ps.Material.SiO2, addToExisting=True).apply()
    return domain


def native_plane(dimension: int):
    ps.setDimension(dimension)
    ps.setNumThreads(1)
    ps.Length.setUnit("um")
    ps.Time.setUnit("s")
    delta, extent = 0.2, 2.0
    boundary = ps.BoundaryType.REFLECTIVE_BOUNDARY
    domain = (ps.Domain(delta, extent, boundary) if dimension == 2
              else ps.Domain(delta, extent, extent, boundary))
    ps.MakePlane(domain, height=0.0, material=ps.Material.Si, addToExisting=False).apply()
    return domain


def surface_top(domain, dimension: int) -> float:
    mesh = domain.getSurfaceMesh(addInterfaces=True, sharpCorners=False, minNodeDistanceFactor=0.01)
    vertical = 1 if dimension == 2 else 2
    return max(float(point[vertical]) for point in mesh.getNodes())


def material_ids(domain) -> list[int]:
    return [int(domain.getMaterialMap().getMaterialIdAtIdx(index))
            for index in range(domain.getMaterialMap().size())]


def run_specialized(project: dict, domain) -> tuple[object, dict]:
    parsed, _warnings = validate_project(project, kind="simulate")
    normalized = parsed.model_dump(mode="json", exclude_none=True)
    step = normalized["steps"][0]
    model, extra, actual, duration = build_specialized_model(
        ps, domain, normalized, step, material_handle, {})
    process = ps.Process(domain, model, duration)
    if step["nativeModel"] == "faradayCage":
        process.setFluxEngineType(ps.FluxEngineType.CPU_DISK)
        rays = ps.RayTracingParameters()
        rays.raysPerPoint = 32
        rays.rngSeed = 271828
        process.setParameters(rays)
    advection = ps.AdvectionParameters()
    advection.timeStepRatio = 0.25
    process.setParameters(advection)
    for item in extra:
        process.setParameters(item)
    process.apply()
    return model, actual


class SpecializedCatalogEntries(unittest.TestCase):
    def test_catalog_exposes_the_two_native_constructors(self):
        specs = {row["id"]: row for row in CATALOG}
        faraday = specs["faradayCage"]
        self.assertEqual(faraday["nativeClass"], "FaradayCageEtching")
        self.assertEqual(faraday["operation"], "etch")
        self.assertTrue(faraday["usesFlux"])
        self.assertTrue(faraday["experimental"])
        self.assertEqual(faraday["dimensions"], [3])
        self.assertEqual({field["key"] for field in faraday["fields"]}, FARADAY_FIELDS)
        cage_angle = next(field for field in faraday["fields"] if field["key"] == "cageAngle")
        self.assertEqual(cage_angle["default"], 0)

        trench = specs["geometricTrenchDeposition"]
        self.assertEqual(trench["nativeClass"], "GeometricTrenchDeposition")
        self.assertEqual(trench["operation"], "deposit")
        self.assertFalse(trench["usesFlux"])
        self.assertTrue(trench["experimental"])
        self.assertEqual(trench["dimensions"], [2])
        self.assertTrue(trench["depositMaterialSelectable"])
        self.assertEqual({field["key"] for field in trench["fields"]}, TRENCH_FIELDS)
        defaults = {field["key"]: field["default"] for field in trench["fields"]}
        self.assertEqual(defaults, {"trenchWidth": 1.0, "trenchDepth": 4.0, "depositionRate": 0.5,
                                    "bottomMed": 0.2, "a": 0.3, "b": 0.1, "n": 1.0})
        self.assertNotIn("depositMaterial", trench)

    def test_both_models_validate_only_in_their_supported_dimension(self):
        supported = ((faraday_project, 3, "CPU_DISK"), (geometric_project, 2, "not-applicable"))
        for factory, dimension, engine in supported:
            with self.subTest(model=factory.__name__, dimension=dimension):
                parsed, warnings = validate_project(factory(dimension), kind="simulate")
                self.assertEqual(parsed.schemaVersion, 7)
                self.assertTrue(any("no archived Stage 8/9 direct-reference acceptance" in warning
                                    for warning in warnings))
                normalized = parsed.model_dump(mode="json", exclude_none=True)
                self.assertEqual(select_engine(normalized, normalized["steps"][0]), engine)
        # The 2D FaradayCage binding never returns and the 3D
        # GeometricTrenchDeposition binding SIGSEGVs in controlled probes.
        unsupported = ((faraday_project, 2, "3D"), (geometric_project, 3, "2D"))
        for factory, dimension, available in unsupported:
            with self.subTest(model=factory.__name__, dimension=dimension):
                with self.assertRaises(ProjectInputError) as ctx:
                    validate_project(factory(dimension), kind="simulate")
                messages = [issue.message for issue in ctx.exception.issues
                            if issue.path == "steps[0].nativeModel"]
                self.assertTrue(messages, [issue.path for issue in ctx.exception.issues])
                self.assertIn(f"only available in {available}", messages[0])


class SpecializedValidationNegatives(unittest.TestCase):
    def test_unknown_native_field_is_rejected(self):
        for factory, dimension in ((faraday_project, 3), (geometric_project, 2)):
            project = factory(dimension)
            project["steps"][0]["nativeParameters"]["notANativeField"] = 1
            with self.assertRaises(ProjectInputError) as ctx:
                validate_project(project)
            self.assertTrue(any("nativeParameters" in issue.message for issue in ctx.exception.issues))

    def test_non_finite_and_out_of_range_values_are_rejected(self):
        project = faraday_project(3)
        project["steps"][0]["nativeParameters"]["cageAngle"] = float("nan")
        with self.assertRaises(ProjectInputError):
            validate_project(project)
        project = faraday_project(3)
        project["steps"][0]["nativeParameters"]["cageAngle"] = 200
        with self.assertRaises(ProjectInputError):
            validate_project(project)
        project = geometric_project(2)
        project["steps"][0]["nativeParameters"]["trenchDepth"] = 0
        with self.assertRaises(ProjectInputError):
            validate_project(project)

    def test_geometric_trench_rejects_fractional_n_and_zero_geometry(self):
        for key, value in (("n", 0.5), ("trenchWidth", 0), ("depositionRate", 0)):
            with self.subTest(key=key):
                project = geometric_project(2)
                project["steps"][0]["nativeParameters"][key] = value
                with self.assertRaises(ProjectInputError) as ctx:
                    validate_project(project)
                self.assertTrue(any(key in issue.path for issue in ctx.exception.issues),
                                [issue.path for issue in ctx.exception.issues])

    def test_geometric_deposition_must_be_the_first_enabled_step(self):
        project = geometric_project(2)
        project["steps"].insert(0, {
            "id": "pre_deposition", "name": "pre", "enabled": True, "operation": "deposit",
            "model": "isotropic", "rateMagnitude": 0.1, "duration": 1.0,
            "depositMaterial": "SiO2",
        })
        with self.assertRaises(ProjectInputError) as ctx:
            validate_project(project)
        self.assertTrue(any(issue.path == "steps[1].nativeModel" for issue in ctx.exception.issues),
                        [issue.path for issue in ctx.exception.issues])

    def test_geometric_trench_depth_covers_the_initial_vertical_span(self):
        project = geometric_project(2)
        project["geometry"] = {"kind": "plane", "height": 8.0, "substrateMaterial": "Si"}
        project["steps"][0]["nativeParameters"].update({"trenchDepth": 0.000001, "n": 100})
        with self.assertRaises(ProjectInputError) as ctx:
            validate_project(project)
        self.assertTrue(any("trenchDepth" in issue.path for issue in ctx.exception.issues),
                        [issue.path for issue in ctx.exception.issues])

    def test_geometric_deposition_requires_a_deposit_material(self):
        project = geometric_project(2)
        project["steps"][0].pop("depositMaterial")
        with self.assertRaises(ProjectInputError):
            validate_project(project)
        project = geometric_project(2)
        project["steps"][0]["depositMaterial"] = "not-a-material"
        with self.assertRaises(ProjectInputError):
            validate_project(project)

    def test_catalogue_operation_is_enforced(self):
        project = faraday_project(3)
        project["steps"][0]["operation"] = "deposit"
        project["steps"][0]["depositMaterial"] = "SiO2"
        with self.assertRaises(ProjectInputError):
            validate_project(project)

    def test_material_table_scoping_matches_ion_beam(self):
        project = faraday_project(3)
        del project["geometry"]["layers"][0]  # no Si3N4 mask at this step
        project["steps"][0]["nativeParameters"]["materialPlaneWaferRateByMaterial"] = {"Si3N4": 0.01}
        with self.assertRaises(ProjectInputError) as ctx:
            validate_project(project, kind="simulate")
        paths = [issue.path for issue in ctx.exception.issues]
        self.assertIn("steps[0].nativeParameters.materialPlaneWaferRateByMaterial", paths)


class NativeFaradayCage(unittest.TestCase):
    def test_real_3d_cpu_run_etches_the_exposed_film(self):
        for dimension in (3,):
            with self.subTest(dimension=dimension):
                project = faraday_project(dimension)
                domain = native_stack(dimension, "periodic")
                before = surface_top(domain, dimension)
                model, actual = run_specialized(project, domain)
                after = surface_top(domain, dimension)
                self.assertEqual(type(model).__name__, "FaradayCageEtching")
                self.assertNotIsInstance(model, ps.IonBeamEtching)
                self.assertLess(after, before - 1e-3)
                container = actual["nativeParameterContainer"]
                self.assertEqual(container["nativeClass"], "FaradayCageParameters")
                self.assertEqual(container["cageAngle"], 0.0)
                self.assertEqual(container["maskMaterials"], ["Si3N4"])
                self.assertEqual(container["ibeParameters"]["meanEnergy"], 100.0)
                self.assertTrue(container["maskMaterialPlaneWaferRateForcedZero"])


class NativeGeometricTrenchDeposition(unittest.TestCase):
    def test_real_2d_cpu_run_adds_the_selectable_material(self):
        for dimension in (2,):
            with self.subTest(dimension=dimension):
                project = geometric_project(dimension)
                domain = native_plane(dimension)
                deposit_material = material_handle(project["steps"][0]["depositMaterial"])
                domain.duplicateTopLevelSet(deposit_material)
                before = surface_top(domain, dimension)
                model, actual = run_specialized(project, domain)
                after = surface_top(domain, dimension)
                self.assertEqual(type(model).__name__, "GeometricTrenchDeposition")
                # radius at z=0 is a + b = 0.4 um.
                self.assertAlmostEqual(after - before, 0.4, delta=0.02)
                self.assertEqual(material_ids(domain)[-1], int(deposit_material.legacyId()))
                semantics = actual["nativeGeometricAdvection"]
                self.assertFalse(semantics["trenchWidthUsedByNativeKernel"])
                self.assertFalse(semantics["durationScalesThickness"])
                self.assertEqual(semantics["depositMaterial"], "Al2O3")
                self.assertIsNone(semantics["nativeMask"])

    def test_process_duration_does_not_scale_the_thickness(self):
        # The native geometric advection runs once; duration is recorded but
        # cannot change the distribution-driven thickness.
        heights = {}
        for duration in (0.02, 2.0):
            project = geometric_project(2)
            project["steps"][0]["duration"] = duration
            domain = native_plane(2)
            domain.duplicateTopLevelSet(ps.Material.Al2O3)
            run_specialized(project, domain)
            heights[duration] = surface_top(domain, 2)
        self.assertAlmostEqual(heights[0.02], heights[2.0], delta=1e-9)


class SpecializedHashAndPersistence(unittest.TestCase):
    def test_native_parameters_change_the_request_hash(self):
        project = faraday_project(3)
        first = requested_input_hash(validate_project(project)[0])
        edited = copy.deepcopy(project)
        edited["steps"][0]["nativeParameters"]["cageAngle"] = 12.5
        self.assertNotEqual(first, requested_input_hash(validate_project(edited)[0]))

        project = geometric_project(2)
        first = requested_input_hash(validate_project(project)[0])
        edited = copy.deepcopy(project)
        edited["steps"][0]["nativeParameters"]["b"] = 0.2
        self.assertNotEqual(first, requested_input_hash(validate_project(edited)[0]))

    def test_projects_save_and_reopen_with_native_parameters(self):
        storage.initialize()
        for factory, dimension in ((faraday_project, 3), (geometric_project, 2)):
            with self.subTest(model=factory.__name__, dimension=dimension):
                normalized = validate_project(factory(dimension))[0].model_dump(mode="json", exclude_none=True)
                record = storage.create_project(normalized)
                reopened = storage.read_json(storage.project_path(record["id"]))["project"]
                self.assertEqual(reopened["steps"][0]["nativeModel"],
                                 normalized["steps"][0]["nativeModel"])
                self.assertEqual(reopened["steps"][0]["nativeParameters"],
                                 normalized["steps"][0]["nativeParameters"])
                parsed, _warnings = validate_project(reopened)
                self.assertEqual(parsed.steps[0].nativeModel, normalized["steps"][0]["nativeModel"])


class ModelSpecGuard(unittest.TestCase):
    def test_new_ids_are_looked_up_by_the_allowlist(self):
        self.assertEqual(model_spec("faradayCage")["nativeClass"], "FaradayCageEtching")
        self.assertEqual(model_spec("geometricTrenchDeposition")["nativeClass"],
                         "GeometricTrenchDeposition")
        self.assertIsNone(model_spec("FaradayCageEtching"))


class FaradayCpuReplay(unittest.IsolatedAsyncioTestCase):
    """A real experimental CPU run must remain replayable (codex checkpoint)."""

    async def asyncSetUp(self):
        self.life = app.router.lifespan_context(app)
        await self.life.__aenter__()

    async def asyncTearDown(self):
        await self.life.__aexit__(None, None, None)

    async def wait(self, run_id: str) -> dict:
        for _ in range(1200):
            info = json_body(await asgi_request(f"/api/runs/{run_id}"))
            if info["status"] in {"succeeded", "failed", "timed_out", "cancelled", "interrupted"}:
                self.assertEqual(info["status"], "succeeded", info.get("failureReason"))
                return info
            await asyncio.sleep(0.05)
        self.fail("FaradayCage run did not finish")

    async def test_faraday_cage_cpu_run_replays(self):
        project = faraday_project(3)
        project["name"] = "FaradayCage CPU replay checkpoint"
        project["solver"]["enginePreference"] = "cpu"
        project["solver"]["timeoutSeconds"] = 60
        response = await asgi_request("/api/runs", method="POST",
                                      payload={"kind": "simulate", "project": project})
        self.assertEqual(response[0], 202, response[2])
        run_id = json_body(response)["runId"]
        await self.wait(run_id)
        envelope = json_body(await asgi_request(f"/api/runs/{run_id}/replay-project"))
        replay = await asgi_request("/api/runs/replay", method="POST", payload=envelope)
        self.assertEqual(replay[0], 202, replay[2])
        await self.wait(json_body(replay)["runId"])


if __name__ == "__main__":
    unittest.main()
