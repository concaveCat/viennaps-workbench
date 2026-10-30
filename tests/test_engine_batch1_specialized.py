"""FaradayCageEtching, GeometricTrenchDeposition and SelectiveEpitaxy.

Both experimental models are constructed with the real ViennaPS 4.6.2 bindings
through ``worker.specialized.build_specialized_model`` and executed with a real
native ``ps.Process``. Controlled probes showed the 2D FaradayCage path never
returning, so validation rejects that dimension; GeometricTrenchDeposition now
runs in 2D and 3D through the same native class (``viennaps.d{2,3}``) for the
probed parametric geometry kinds. The tests also pin the native semantics that
the catalog cannot change:

* ``FaradayCageEtching`` wraps ``IBEParameters`` in ``FaradayCageParameters``
  and builds a two-direction periodic cage source. Mask materials are forced
  to a zero plane-wafer rate by the native constructor.
* ``GeometricTrenchDeposition`` is a ViennaLS geometric-advection distribution.
  It grows the selectable ``depositMaterial`` top level set, ignores
  ``trenchWidth`` and the process duration, and evaluates its local radius from
  absolute z with the trench top assumed at z=0. Its 3D SIGSEGV audit case is
  reproduced as a parameter-reach crash (``depositionRate + 3*gridDelta`` below
  the native radius) that also occurs in 2D, so validation guards the reach
  rather than hiding the dimension.
* ``SelectiveEpitaxy`` binds a separate ``nvFactors`` constructor overload with
  a fixed-size three-vector. The native defaults are dimension-dependent, so
  omitting the key keeps the existing materialRates overload byte-for-byte.
* ``CoverageParameters.tolerance`` defaults natively to 0.0 but the worker has
  always pinned 1e-3. An explicit ``coverageTolerance`` is passed to the same
  native object and read back through ``toMetaData``.

The two experimental models are marked ``experimental`` in the catalog: no Stage
8/9 direct-reference acceptance row has been archived, so validation emits a
warning instead of claiming verified coverage.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GDS_FIXTURE = ROOT / "tests" / "fixtures" / "finfet22_workbench.gds"
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
from worker.simulate import configure_rays  # noqa: E402

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
    project["geometry"] = ({"kind": "plane", "height": 0.0, "substrateMaterial": "Si"}
                           if dimension == 2 else
                           {"kind": "trench", "width": 1.0, "depth": 2.0, "taperAngle": 0.0,
                            "maskHeight": 0.4, "maskTaperAngle": 0.0, "substrateMaterial": "Si"})
    project["steps"][0]["depositMaterial"] = "Al2O3"
    return project


def geometric_plane_project(dimension: int) -> dict:
    project = make_project("geometricTrenchDeposition", dimension)
    project["geometry"] = {"kind": "plane", "height": 0.0, "substrateMaterial": "Si"}
    project["steps"][0]["depositMaterial"] = "Al2O3"
    return project


def selective_project(dimension: int, *, nv_factors=None) -> dict:
    """Flat Si surface so the normal is exactly vertical and deterministic."""
    project = make_project("selectiveEpitaxy", dimension)
    project["geometry"] = {"kind": "plane", "height": 0.0, "substrateMaterial": "Si"}
    step = project["steps"][0]
    step["duration"] = 1.0
    step["nativeParameters"]["materialRates"] = {"Si": 0.5, "SiGe": 0.5}
    # The upstream selectiveEpitaxy example uses the stencil local Lax-Friedrichs
    # scheme; the default Engquist-Osher scheme does not grow this flat surface.
    project["solver"]["advection"]["spatialScheme"] = "STENCIL_LOCAL_LAX_FRIEDRICHS_1ST_ORDER"
    if nv_factors is not None:
        step["nativeParameters"]["nvFactors"] = (list(nv_factors)
                                                  if isinstance(nv_factors, (list, tuple))
                                                  else nv_factors)
    return project


def plasma_project(dimension: int = 2) -> dict:
    project = make_project("sf6o2", dimension)
    project["steps"][0]["duration"] = 0.02
    return project


def native_stack(dimension: int, boundary: str = "periodic"):
    ps.setDimension(dimension)
    ps.setNumThreads(1)
    ps.Length.setUnit("um")
    ps.Time.setUnit("s")
    delta, extent = 0.2, 2.0
    boundary_type = (ps.BoundaryType.PERIODIC_BOUNDARY if boundary == "periodic"
                     else ps.BoundaryType.REFLECTIVE_BOUNDARY)
    domain = (ps.Domain(delta, extent, boundary=boundary_type) if dimension == 2
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
    domain = (ps.Domain(delta, extent, boundary=boundary) if dimension == 2
              else ps.Domain(delta, extent, extent, boundary))
    ps.MakePlane(domain, height=0.0, material=ps.Material.Si, addToExisting=False).apply()
    return domain


def native_trench(dimension: int):
    ps.setDimension(dimension)
    ps.setNumThreads(1)
    ps.Length.setUnit("um")
    ps.Time.setUnit("s")
    delta, extent = 0.2, 4.0
    boundary = ps.BoundaryType.REFLECTIVE_BOUNDARY
    domain = (ps.Domain(delta, extent, boundary=boundary) if dimension == 2
              else ps.Domain(delta, extent, extent, boundary))
    ps.MakeTrench(domain=domain, trenchWidth=1.0, trenchDepth=2.0, trenchTaperAngle=0.0,
                  maskHeight=0.4, maskTaperAngle=0.0, halfTrench=False,
                  material=ps.Material.Si, maskMaterial=ps.Material.Si3N4).apply()
    return domain


def surface_signature(domain, dimension: int) -> tuple:
    mesh = domain.getSurfaceMesh(addInterfaces=True, sharpCorners=False, minNodeDistanceFactor=0.01)
    points = tuple(tuple(round(float(value), 12) for value in point[:dimension])
                   for point in mesh.getNodes())
    cells = mesh.getLines() if dimension == 2 else mesh.getTriangles()
    connectivity = tuple(tuple(int(index) for index in cell) for cell in cells)
    return points, connectivity


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
    if step["nativeModel"] == "selectiveEpitaxy":
        domain.duplicateTopLevelSet(ps.Material.SiGe)
    model, extra, actual, duration = build_specialized_model(
        ps, domain, normalized, step, material_handle, {})
    process = ps.Process(domain, model, duration)
    if step["nativeModel"] == "faradayCage":
        process.setFluxEngineType(ps.FluxEngineType.CPU_DISK)
        rays = ps.RayTracingParameters()
        rays.raysPerPoint = 32
        rays.rngSeed = 271828
        process.setParameters(rays)
    if step["nativeModel"] == "sf6o2":
        process.setFluxEngineType(ps.FluxEngineType.CPU_DISK)
        rays, _ray_actual = configure_rays(ps, normalized, 271828)
        process.setParameters(rays)
    advection = ps.AdvectionParameters()
    advection.timeStepRatio = 0.25
    if step["nativeModel"] == "selectiveEpitaxy":
        advection.spatialScheme = ps.ls.SpatialSchemeEnum.STENCIL_LOCAL_LAX_FRIEDRICHS_1ST_ORDER
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
        self.assertEqual(trench["dimensions"], [2, 3])
        self.assertTrue(trench["depositMaterialSelectable"])
        self.assertEqual({field["key"] for field in trench["fields"]}, TRENCH_FIELDS)
        defaults = {field["key"]: field["default"] for field in trench["fields"]}
        self.assertEqual(defaults, {"trenchWidth": 1.0, "trenchDepth": 4.0, "depositionRate": 0.5,
                                    "bottomMed": 0.2, "a": 0.3, "b": 0.1, "n": 1.0})
        self.assertNotIn("depositMaterial", trench)

    def test_optional_native_fields_are_strict_and_documented(self):
        specs = {row["id"]: row for row in CATALOG}
        selective = specs["selectiveEpitaxy"]
        self.assertEqual(selective["optionalKeys"], ["nvFactors"])
        nv_field = next(field for field in selective["optionalFields"] if field["key"] == "nvFactors")
        self.assertEqual(nv_field["kind"], "nvFactors")
        self.assertEqual(nv_field["length"], 3)
        self.assertEqual((nv_field["minimum"], nv_field["maximum"]), (0.0, 1.0))
        self.assertEqual(nv_field["nativeDefaultByDimension"], {"2": [0.5, 1.0, 0.0], "3": [0.5, 0.0, 1.0]})
        for model_id in ("sf6o2", "cf4o2", "hbro2", "sf6c4f8"):
            with self.subTest(model=model_id):
                self.assertIn("coverageTolerance", specs[model_id]["optionalKeys"])
                field = next(row for row in specs[model_id]["optionalFields"]
                             if row["key"] == "coverageTolerance")
                self.assertEqual(field["nativeDefault"], 0.0)
                self.assertEqual(field["workbenchHistoricalDefault"], 1e-3)
                self.assertEqual((field["minimum"], field["maximum"]), (0.0, 1.0))

    def test_both_models_validate_only_in_their_supported_dimension(self):
        for factory, dimension, engine in ((faraday_project, 3, "CPU_DISK"),
                                           (geometric_project, 2, "not-applicable"),
                                           (geometric_project, 3, "not-applicable")):
            with self.subTest(model=factory.__name__, dimension=dimension):
                parsed, warnings = validate_project(factory(dimension), kind="simulate")
                self.assertEqual(parsed.schemaVersion, 7)
                self.assertTrue(any("no archived Stage 8/9 direct-reference acceptance" in warning
                                    for warning in warnings))
                normalized = parsed.model_dump(mode="json", exclude_none=True)
                self.assertEqual(select_engine(normalized, normalized["steps"][0]), engine)
        # The 2D FaradayCage binding never returns in controlled probes.
        with self.assertRaises(ProjectInputError) as ctx:
            validate_project(faraday_project(2), kind="simulate")
        messages = [issue.message for issue in ctx.exception.issues
                    if issue.path == "steps[0].nativeModel"]
        self.assertTrue(messages, [issue.path for issue in ctx.exception.issues])
        self.assertIn("only available in 3D", messages[0])


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

    def test_geometric_deposition_rejects_gds_geometry(self):
        from unittest.mock import patch
        for dimension in (2, 3):
            with self.subTest(dimension=dimension):
                project = geometric_project(dimension)
                project["geometry"] = {
                    "kind": "gds", "assetId": hashlib.sha256(GDS_FIXTURE.read_bytes()).hexdigest(),
                    "fileName": GDS_FIXTURE.name, "structure": "FINFET22", "unitScale": 1.0,
                    "position": [0.0, 0.0, 0.0],
                    "layers": ([{"layer": 7, "material": "Si"}] if dimension == 2 else
                               [{"layer": 7, "material": "Si", "baseHeight": 0.0, "height": 0.4}]),
                }
                with patch("backend.storage.resolve_gds_asset", return_value=GDS_FIXTURE):
                    with self.assertRaises(ProjectInputError) as ctx:
                        validate_project(project)
                self.assertTrue(any("GDS geometry" in issue.message for issue in ctx.exception.issues),
                                [issue.message for issue in ctx.exception.issues])

    def test_geometric_parameter_reach_guard_rejects_the_audit_crash_case(self):
        # This is the exact 3D probe from the capability audit: a 2.1 um native
        # radius with depositionRate=0.05 and gridDelta=0.1. It SIGSEGVs in both
        # 2D and 3D because the ViennaLS candidate window is rate+3*grid wide.
        for dimension in (2, 3):
            with self.subTest(dimension=dimension):
                project = geometric_project(dimension)
                project["steps"][0]["nativeParameters"].update({
                    "trenchWidth": 0.4, "trenchDepth": 0.5, "depositionRate": 0.05,
                    "bottomMed": 0.1, "a": 0.1, "b": 2.0, "n": 1.0})
                with self.assertRaises(ProjectInputError) as ctx:
                    validate_project(project)
                messages = [issue.message for issue in ctx.exception.issues
                            if issue.path == "steps[0].nativeParameters.depositionRate"]
                self.assertTrue(messages, [issue.path for issue in ctx.exception.issues])
                self.assertIn("native radius", messages[0])

    def test_geometric_rejects_zero_radius_and_sub_grid_width_depth(self):
        cases = (
            ("a", 0.0, "a+b and bottomMed"),
            ("trenchWidth", 0.01, "at least one grid interval"),
            ("trenchDepth", 0.01, "at least one grid interval"),
        )
        for key, value, message in cases:
            with self.subTest(key=key):
                project = geometric_project(3)
                if key == "a":
                    project["steps"][0]["nativeParameters"].update({"a": 0.0, "b": 0.0, "bottomMed": 0.0})
                else:
                    project["steps"][0]["nativeParameters"][key] = value
                with self.assertRaises(ProjectInputError) as ctx:
                    validate_project(project)
                self.assertTrue(any(message in issue.message for issue in ctx.exception.issues),
                                [(issue.path, issue.message) for issue in ctx.exception.issues])

    def test_selective_nv_factors_reject_unknown_length_range_and_all_zero(self):
        for value in ([1, 1], [1, 1, 1, 1], [-0.1, 1, 1], [1.1, 1, 1],
                      [float("nan"), 1, 1], [float("inf"), 1, 1], [0, 0, 0],
                      "not-a-vector", True):
            with self.subTest(value=value):
                project = selective_project(2, nv_factors=value)
                with self.assertRaises(ProjectInputError) as ctx:
                    validate_project(project)
                paths = [issue.path for issue in ctx.exception.issues]
                self.assertTrue(any("nvFactors" in path or "nativeParameters" in path for path in paths), paths)
        project = selective_project(2)
        project["steps"][0]["nativeParameters"]["nvFactorsX"] = [1, 1, 1]
        with self.assertRaises(ProjectInputError):
            validate_project(project)

    def test_coverage_tolerance_rejects_invalid_range_and_wrong_models(self):
        for value in (-1.0, 1.5, float("nan"), float("inf"), True, "0.1"):
            with self.subTest(value=value):
                project = plasma_project(2)
                project["steps"][0]["nativeParameters"]["coverageTolerance"] = value
                with self.assertRaises(ProjectInputError) as ctx:
                    validate_project(project)
                self.assertTrue(any("coverageTolerance" in issue.path for issue in ctx.exception.issues),
                                [issue.path for issue in ctx.exception.issues])
        project = selective_project(2)
        project["steps"][0]["nativeParameters"]["coverageTolerance"] = 1e-3
        with self.assertRaises(ProjectInputError):
            validate_project(project)

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
    ARGUMENT_KEYS = ("trenchWidth", "trenchDepth", "depositionRate", "bottomMed", "a", "b", "n")

    def run_direct_native(self, domain, dimension: int, parameters: dict):
        model = ps.GeometricTrenchDeposition(
            *(float(parameters[key]) for key in self.ARGUMENT_KEYS))
        process = ps.Process(domain, model, 0.0)
        process.setFluxEngineType(ps.FluxEngineType.CPU_DISK)
        advection = ps.AdvectionParameters()
        advection.timeStepRatio = 0.25
        process.setParameters(advection)
        process.apply()
        return domain

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
                self.assertEqual(type(model).__module__, "viennaps.d2")
                # radius at z=0 is a + b = 0.4 um.
                self.assertAlmostEqual(after - before, 0.4, delta=0.02)
                self.assertEqual(material_ids(domain)[-1], int(deposit_material.legacyId()))
                semantics = actual["nativeGeometricAdvection"]
                self.assertFalse(semantics["trenchWidthUsedByNativeKernel"])
                self.assertFalse(semantics["durationScalesThickness"])
                self.assertEqual(semantics["depositMaterial"], "Al2O3")
                self.assertIsNone(semantics["nativeMask"])

    def test_real_3d_plane_cpu_run_uses_the_3d_native_class(self):
        project = geometric_plane_project(3)
        domain = native_plane(3)
        domain.duplicateTopLevelSet(ps.Material.Al2O3)
        before = surface_top(domain, 3)
        model, actual = run_specialized(project, domain)
        after = surface_top(domain, 3)
        self.assertEqual(type(model).__name__, "GeometricTrenchDeposition")
        self.assertEqual(type(model).__module__, "viennaps.d3")
        self.assertAlmostEqual(after - before, 0.4, delta=0.02)
        self.assertEqual(actual["nativeGeometricAdvection"]["depositMaterial"], "Al2O3")

    def test_3d_trench_matches_a_direct_native_reference(self):
        project = geometric_project(3)
        parameters = project["steps"][0]["nativeParameters"]
        product = native_trench(3)
        product.duplicateTopLevelSet(ps.Material.Al2O3)
        run_specialized(project, product)
        reference = native_trench(3)
        reference.duplicateTopLevelSet(ps.Material.Al2O3)
        self.run_direct_native(reference, 3, parameters)
        product_signature = surface_signature(product, 3)
        reference_signature = surface_signature(reference, 3)
        self.assertEqual(len(product_signature[0]), len(reference_signature[0]))
        for left, right in zip(product_signature[0], reference_signature[0]):
            for a, b in zip(left, right):
                self.assertAlmostEqual(a, b, delta=1e-6)
        self.assertEqual(product_signature[1], reference_signature[1])

    def test_3d_geometry_run_is_deterministic(self):
        signatures = []
        for _ in range(2):
            project = geometric_project(3)
            domain = native_trench(3)
            domain.duplicateTopLevelSet(ps.Material.Al2O3)
            run_specialized(project, domain)
            signatures.append(surface_signature(domain, 3))
        self.assertEqual(signatures[0], signatures[1])

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


def run_selective_direct(domain, dimension: int, factors, material_rates, rate111, rate100):
    if factors is None:
        model = ps.SelectiveEpitaxy(
            [(material_handle(name), float(value)) for name, value in sorted(material_rates.items())],
            float(rate111), float(rate100))
    else:
        model = ps.SelectiveEpitaxy([float(value) for value in factors],
                                    float(rate111), float(rate100))
        for name, value in sorted(material_rates.items()):
            model.setMaterialRate(material_handle(name), float(value))
    process = ps.Process(domain, model, 1.0)
    process.setFluxEngineType(ps.FluxEngineType.CPU_DISK)
    advection = ps.AdvectionParameters()
    advection.timeStepRatio = 0.25
    advection.spatialScheme = ps.ls.SpatialSchemeEnum.STENCIL_LOCAL_LAX_FRIEDRICHS_1ST_ORDER
    process.setParameters(advection)
    process.apply()
    return domain


class NativeSelectiveEpitaxyNvFactors(unittest.TestCase):
    def test_omitted_and_explicit_native_default_are_identical_2d_and_3d(self):
        defaults = {2: [0.5, 1.0, 0.0], 3: [0.5, 0.0, 1.0]}
        for dimension in (2, 3):
            with self.subTest(dimension=dimension):
                omitted_domain = native_plane(dimension)
                omitted_project = selective_project(dimension)
                run_specialized(omitted_project, omitted_domain)
                explicit_domain = native_plane(dimension)
                explicit_project = selective_project(dimension, nv_factors=defaults[dimension])
                _model, actual = run_specialized(explicit_project, explicit_domain)
                self.assertEqual(surface_signature(omitted_domain, dimension),
                                 surface_signature(explicit_domain, dimension))
                nv_actual = actual["nativeNvFactors"]
                self.assertEqual(nv_actual["requested"], defaults[dimension])
                self.assertEqual(nv_actual["effective"], defaults[dimension])
                self.assertEqual(nv_actual["nativeModule"], f"viennaps.d{dimension}")
                self.assertIsNone(nv_actual["nativeReadback"])
                self.assertEqual(nv_actual["materialRatesAppliedAfterConstruction"],
                                 {"Si": 0.5, "SiGe": 0.5})

    def test_nv_factors_can_stop_growth_along_the_dimension_vertical_axis(self):
        # 2D vertical axis is y (index 1); 3D vertical axis is z (index 2).
        for dimension, blocking in ((2, [0.5, 0.0, 1.0]), (3, [0.5, 1.0, 0.0])):
            with self.subTest(dimension=dimension):
                project = selective_project(dimension, nv_factors=blocking)
                domain = native_plane(dimension)
                before = surface_top(domain, dimension)
                run_specialized(project, domain)
                after = surface_top(domain, dimension)
                self.assertAlmostEqual(after, before, delta=1e-9)

    def test_omitted_run_grows_the_flat_surface(self):
        for dimension in (2, 3):
            with self.subTest(dimension=dimension):
                project = selective_project(dimension)
                domain = native_plane(dimension)
                before = surface_top(domain, dimension)
                run_specialized(project, domain)
                after = surface_top(domain, dimension)
                self.assertAlmostEqual(after - before, 0.5, delta=0.02)

    def test_explicit_nv_factors_match_a_direct_native_reference(self):
        for dimension, factors in ((2, [1.0, 0.5, 0.0]), (3, [1.0, 0.0, 0.5])):
            with self.subTest(dimension=dimension):
                project = selective_project(dimension, nv_factors=factors)
                parameters = project["steps"][0]["nativeParameters"]
                product = native_plane(dimension)
                run_specialized(project, product)
                reference = native_plane(dimension)
                reference.duplicateTopLevelSet(ps.Material.SiGe)
                run_selective_direct(reference, dimension, factors, parameters["materialRates"],
                                     parameters["rate111"], parameters["rate100"])
                self.assertEqual(surface_signature(product, dimension),
                                 surface_signature(reference, dimension))

    def test_explicit_nv_factors_are_deterministic(self):
        for dimension, factors in ((2, [1.0, 0.5, 0.0]), (3, [1.0, 0.0, 0.5])):
            with self.subTest(dimension=dimension):
                signatures = []
                for _ in range(2):
                    project = selective_project(dimension, nv_factors=factors)
                    domain = native_plane(dimension)
                    run_specialized(project, domain)
                    signatures.append(surface_signature(domain, dimension))
                self.assertEqual(signatures[0], signatures[1])

    def test_omitted_request_hash_is_pinned_and_explicit_factors_change_it(self):
        pinned = {2: "169d3c63db0479ce5c1a1738b74dbb86c332d9c83840a1bc0f4d2a513fa86dc1",
                  3: "64df9a40467f709e06a39b4afb0616c4ba0fd79bc720600bea4f225710e53e5f"}
        for dimension in (2, 3):
            with self.subTest(dimension=dimension):
                omitted = requested_input_hash(validate_project(selective_project(dimension))[0])
                self.assertEqual(omitted, pinned[dimension])
                explicit = requested_input_hash(validate_project(
                    selective_project(dimension, nv_factors=[0.5, 0.5, 0.5]))[0])
                self.assertNotEqual(omitted, explicit)


class NativeCoverageTolerance(unittest.TestCase):
    def test_omitted_default_is_the_historical_1e3_and_adds_no_metadata(self):
        project = plasma_project(2)
        parsed, _ = validate_project(project)
        self.assertNotIn("coverageTolerance", parsed.steps[0].nativeParameters)
        self.assertEqual(
            requested_input_hash(parsed),
            "f06dd19066dc3c41f283406dcfc3c0bdae54b1d2772aa0f19dc2dbf1ad7c2670")
        domain = native_stack(2, "reflective")
        domain.enableMetaData(ps.MetaDataLevel.FULL)
        _model, actual = run_specialized(project, domain)
        self.assertNotIn("nativeCoverageParameters", actual)
        # The worker still sends the historical 1e-3.
        self.assertEqual(domain.getMetaData()["CoverageTolerance"], [1e-3])

    def test_explicit_value_is_read_back_and_changes_the_run(self):
        signatures = {}
        readbacks = {}
        for tolerance in (0.0, 1e-3):
            with self.subTest(tolerance=tolerance):
                project = plasma_project(2)
                project["steps"][0]["nativeParameters"]["coverageTolerance"] = tolerance
                domain = native_stack(2, "reflective")
                domain.enableMetaData(ps.MetaDataLevel.FULL)
                _model, actual = run_specialized(project, domain)
                metadata = domain.getMetaData()
                self.assertEqual(metadata["CoverageTolerance"], [tolerance])
                nv = actual["nativeCoverageParameters"]
                self.assertEqual(nv["requested"], tolerance)
                self.assertEqual(nv["effective"], tolerance)
                self.assertEqual(nv["nativeReadback"]["CoverageTolerance"], [tolerance])
                signatures[tolerance] = surface_signature(domain, 2)
                readbacks[tolerance] = metadata["CoverageMaxIterations"][0]
        # tolerance 0.0 leaves maxIterations at its native default, so the
        # installed strategy applies its 10-iteration fallback.
        self.assertEqual(readbacks[0.0], 10.0)
        self.assertEqual(readbacks[1e-3], 4294967295.0)
        self.assertNotEqual(signatures[0.0], signatures[1e-3])

    def test_explicit_zero_is_accepted_and_negative_or_nonfinite_is_rejected(self):
        project = plasma_project(2)
        project["steps"][0]["nativeParameters"]["coverageTolerance"] = 0.0
        parsed, _ = validate_project(project)
        self.assertEqual(parsed.steps[0].nativeParameters["coverageTolerance"], 0.0)
        for value in (-1e-9, 1.0001, float("nan"), float("inf")):
            with self.subTest(value=value):
                project = plasma_project(2)
                project["steps"][0]["nativeParameters"]["coverageTolerance"] = value
                with self.assertRaises(ProjectInputError):
                    validate_project(project)


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
        self.fail("specialized CPU run did not finish")

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

    async def test_explicit_coverage_tolerance_cpu_run_replays(self):
        project = plasma_project(2)
        project["name"] = "Coverage tolerance CPU replay checkpoint"
        project["steps"][0]["nativeParameters"]["coverageTolerance"] = 1e-4
        project["solver"]["enginePreference"] = "cpu"
        project["solver"]["timeoutSeconds"] = 60
        response = await asgi_request("/api/runs", method="POST",
                                      payload={"kind": "simulate", "project": project})
        self.assertEqual(response[0], 202, response[2])
        run_id = json_body(response)["runId"]
        await self.wait(run_id)
        manifest = json_body(await asgi_request(f"/api/runs/{run_id}"))
        self.assertEqual(manifest["requestedInputHash"],
                         requested_input_hash(validate_project(project)[0]))
        envelope = json_body(await asgi_request(f"/api/runs/{run_id}/replay-project"))
        replay = await asgi_request("/api/runs/replay", method="POST", payload=envelope)
        self.assertEqual(replay[0], 202, replay[2])
        replay_info = await self.wait(json_body(replay)["runId"])
        self.assertEqual(replay_info["requestedInputHash"],
                         requested_input_hash(validate_project(project)[0]))


if __name__ == "__main__":
    unittest.main()
