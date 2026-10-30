"""Strict ``nativeParameters.advanced`` support for the four plasma models.

The installed 4.6.2 bindings expose ``PlasmaEtchingParameters`` overloads for
SF6O2Etching/HBrO2Etching/SF6C4F8Etching and ``CF4O2Parameters`` for
CF4O2Etching. The workbench previously used only the flat float overloads, so
the nested Ions/Mask/Passivation/Polymer/Substrate (plus CF4O2 Si/SiGe and
gamma_*) families were unreachable.

These tests pin the contract:

* omitting ``advanced`` keeps the historical request hashes and native defaults;
* an empty ``advanced`` object still exercises the parameter-object overload;
* the catalog schema matches every exposed scalar of the installed classes;
* model-specific native defaults (HBrO2 Substrate, SF6C4F8 passivationFlux and
  the per-material map values) are preserved, not replaced by generic defaults;
* a 2D and a 3D golden run with two changed sub-parameters match an independent
  native reference within 1e-6 um;
* unknown keys, cross-model blocks, wrong types, and out-of-range values are
  rejected before the worker starts.
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
from backend.hashing import requested_input_hash  # noqa: E402
from backend.main import app  # noqa: E402
from backend.materials import material_catalog  # noqa: E402
from backend.specialized_models import advanced_spec, model_spec  # noqa: E402
from backend.validation import ProjectInputError, validate_project  # noqa: E402
from stage8_project_factory import make_project  # noqa: E402
from tests.test_api_integration import asgi_request, json_body  # noqa: E402
from worker.specialized import build_specialized_model  # noqa: E402

PLASMA_MODELS = ("sf6o2", "cf4o2", "hbro2", "sf6c4f8")

# Request hashes computed on the merge base (d2d81a0) before ``advanced`` was
# accepted. They must never change when the optional object is omitted.
BASE_REQUEST_HASHES = {
    "sf6o2": {"2": "f06dd19066dc3c41f283406dcfc3c0bdae54b1d2772aa0f19dc2dbf1ad7c2670",
              "3": "343958f3456b764bab861058cf4b45f631ac81bf863d4d7b1c946222c1219daf"},
    "cf4o2": {"2": "fda6f3fdfe1ea5fdd41c01b16109f2ebbc3863e732883a481316ab356c26e6ae",
              "3": "f588f868298a41a28e854e04143f0de65110fda1a259a620b1ae10aef7d544e9"},
    "hbro2": {"2": "6efc908d90ea789a1487871259401a426bc409f79c3197fe34edb6982e44430a",
              "3": "ea4f559b824d09289f3a01b96d86212d59e6a55955b7dea4cb07894146827fc3"},
    "sf6c4f8": {"2": "194b2f67abe33bfacdc861443e707ee85c4b2ea726a6706b4f7eb65218a4a308",
                "3": "609e1beafba3767f4aa895f053a575ed5b16d0cf5b347b250ad055171df6012b"},
}

EXPECTED_BLOCKS = {
    "sf6o2": {"ions", "mask", "passivation", "polymer", "substrate"},
    "hbro2": {"ions", "mask", "passivation", "polymer", "substrate"},
    "sf6c4f8": {"ions", "mask", "passivation", "polymer", "substrate"},
    "cf4o2": {"ions", "mask", "passivation", "si", "siGe"},
}
EXPECTED_SCALARS = {
    "sf6o2": {"passivationFlux"}, "hbro2": {"passivationFlux"}, "sf6c4f8": {"passivationFlux"},
    "cf4o2": {"fluxIncludeSticking"},
}
EXPECTED_MAPS = {
    "sf6o2": {"beta_E", "beta_P", "rateFactors"},
    "hbro2": {"beta_E", "beta_P", "rateFactors"},
    "sf6c4f8": {"beta_E", "beta_P", "rateFactors"},
    "cf4o2": {"gamma_C", "gamma_C_oxidized", "gamma_F", "gamma_F_oxidized",
              "gamma_O", "gamma_O_passivated"},
}


def plasma_project(model_id: str, dimension: int = 2, advanced: dict | None = None) -> dict:
    project = make_project(model_id, dimension)
    project["steps"][0]["duration"] = 0.1
    if advanced is not None:
        project["steps"][0]["nativeParameters"]["advanced"] = copy.deepcopy(advanced)
    return project


def native_handle(name: str):
    return getattr(ps.Material, name)


def native_plane(dimension: int):
    ps.setDimension(dimension)
    ps.setNumThreads(1)
    ps.Length.setUnit("um")
    ps.Time.setUnit("s")
    boundary = ps.BoundaryType.REFLECTIVE_BOUNDARY
    domain = (ps.Domain(0.2, 4.0, boundary) if dimension == 2
              else ps.Domain(0.2, 4.0, 4.0, boundary))
    ps.MakePlane(domain, height=0.0, material=ps.Material.Si, addToExisting=False).apply()
    ps.MakePlane(domain, height=0.4, material=ps.Material.SiO2, addToExisting=True).apply()
    return domain


def surface_top(domain, dimension: int) -> float:
    mesh = domain.getSurfaceMesh(addInterfaces=True, sharpCorners=False, minNodeDistanceFactor=0.01)
    vertical = 1 if dimension == 2 else 2
    return max(float(point[vertical]) for point in mesh.getNodes())


def run_model(model, duration: float, extra: list, dimension: int) -> float:
    domain = native_plane(dimension)
    process = ps.Process(domain, model, duration)
    process.setFluxEngineType(ps.FluxEngineType.CPU_DISK)
    rays = ps.RayTracingParameters()
    rays.raysPerPoint = 32
    rays.rngSeed = 271828
    rays.useRandomSeeds = False
    process.setParameters(rays)
    advection = ps.AdvectionParameters()
    advection.timeStepRatio = 0.25
    process.setParameters(advection)
    for item in extra:
        process.setParameters(item)
    process.apply()
    return surface_top(domain, dimension)


# Arguments that select the installed flat overload's own native defaults.
# They are the parameter-class defaults, not a project's edited values.
_NATIVE_FLAT_DEFAULTS = {
    "sf6o2": (12.0, 1800.0, 100.0, 100.0, 10.0, 500.0, 3.0, -1.7976931348623157e308),
    "hbro2": (12.0, 1800.0, 100.0, 100.0, 10.0, 500.0, 3.0, -1.7976931348623157e308),
    "sf6c4f8": (12.0, 1800.0, 100.0, 10.0, 300.0, -1.7976931348623157e308),
    "cf4o2": (12.0, 1800.0, 100.0, 100.0, 100.0, 10.0, 500.0, 3.0, 3.0, -1.7976931348623157e308),
}


def native_default_parameters(model_id: str):
    """A flat model built with the binding's own defaults, plus its params."""
    ps.setDimension(2)
    ps.Length.setUnit("um")
    ps.Time.setUnit("s")
    model = getattr(ps, model_spec(model_id)["nativeClass"])(*_NATIVE_FLAT_DEFAULTS[model_id])
    return model, model.getParameters()


def material_ids() -> dict[str, int]:
    return {row["name"]: int(row["id"]) for row in material_catalog()["materials"]}


def build(project: dict, dimension: int = 2):
    parsed, _warnings = validate_project(project, kind="simulate")
    normalized = parsed.model_dump(mode="json", exclude_none=True)
    domain = native_plane(dimension)
    model, extra, actual, duration = build_specialized_model(
        ps, domain, normalized, normalized["steps"][0], native_handle, material_ids())
    return model, extra, actual, duration


class AdvancedSchemaContract(unittest.TestCase):
    def test_catalog_exposes_exactly_the_introspected_families(self):
        for model_id in PLASMA_MODELS:
            with self.subTest(model=model_id):
                spec = advanced_spec(model_id)
                self.assertIsNotNone(spec)
                self.assertEqual(set(spec["blocks"]), EXPECTED_BLOCKS[model_id])
                self.assertEqual({field["key"] for field in spec["scalars"]}, EXPECTED_SCALARS[model_id])
                self.assertEqual({row["key"] for row in spec["maps"]}, EXPECTED_MAPS[model_id])
                self.assertEqual(set(spec["flatMapping"]),
                                 {field["key"] for field in model_spec(model_id)["fields"]})

                parameter_class = getattr(ps, spec["parameterClass"])
                instance = parameter_class()
                native_blocks = {name for name in dir(instance)
                                 if type(getattr(instance, name)).__name__.startswith(spec["parameterClass"])}
                self.assertEqual(native_blocks, {block["native"] for block in spec["blocks"].values()})
                native_maps = {name for name in dir(instance)
                               if type(getattr(instance, name)).__name__ == "MaterialValueMap"}
                self.assertEqual(native_maps, {row["native"] for row in spec["maps"]})
                flat_top_level = {path for path in spec["flatMapping"].values() if "." not in path}
                native_scalars = {name for name in dir(instance) if not name.startswith("_")
                                  and isinstance(getattr(instance, name), (float, bool))}
                self.assertEqual(native_scalars,
                                 {field["key"] for field in spec["scalars"]} | flat_top_level)

                for block in spec["blocks"].values():
                    native = getattr(instance, block["native"])
                    native_fields = {name for name in dir(native) if not name.startswith("_")
                                     and isinstance(getattr(native, name), (float, bool))}
                    self.assertEqual(native_fields, {field["key"] for field in block["fields"]},
                                     f"{spec['parameterClass']}.{block['native']}")

    def test_catalog_defaults_match_the_installed_flat_native_defaults(self):
        for model_id in PLASMA_MODELS:
            with self.subTest(model=model_id):
                spec = advanced_spec(model_id)
                owner, params = native_default_parameters(model_id)
                for name, block in spec["blocks"].items():
                    native = getattr(params, block["native"])
                    for field in block["fields"]:
                        self.assertEqual(field["default"], getattr(native, field["key"]),
                                         f"{model_id}.{name}.{field['key']}")
                for field in spec["scalars"]:
                    self.assertEqual(field["default"], getattr(params, field["key"]),
                                     f"{model_id}.{field['key']}")
                for row in spec["maps"]:
                    self.assertEqual(row["default"], getattr(params, row["native"]).getDefault(),
                                     f"{model_id}.{row['key']}.default")
                self.assertEqual(type(owner).__name__, model_spec(model_id)["nativeClass"])

    def test_model_specific_defaults_are_not_the_generic_parameter_class_defaults(self):
        hbro2 = advanced_spec("hbro2")
        substrate = {field["key"]: field["default"] for field in hbro2["blocks"]["substrate"]["fields"]}
        self.assertEqual((substrate["A_ie"], substrate["A_sp"], substrate["Eth_sp"]), (5.0, 0.5, 30.0))
        sf6c4f8 = advanced_spec("sf6c4f8")
        self.assertEqual({field["key"]: field["default"] for field in sf6c4f8["scalars"]}["passivationFlux"], 0.0)
        sf6o2 = advanced_spec("sf6o2")
        self.assertEqual({field["key"]: field["default"] for field in sf6o2["scalars"]}["passivationFlux"], 100.0)


class AdvancedHashStability(unittest.TestCase):
    def test_omitting_advanced_keeps_the_base_request_hashes(self):
        for model_id in PLASMA_MODELS:
            for dimension in (2, 3):
                with self.subTest(model=model_id, dimension=dimension):
                    parsed, _warnings = validate_project(make_project(model_id, dimension))
                    self.assertEqual(requested_input_hash(parsed), BASE_REQUEST_HASHES[model_id][str(dimension)])

    def test_an_advanced_value_changes_the_request_hash(self):
        for model_id in PLASMA_MODELS:
            with self.subTest(model=model_id):
                first = requested_input_hash(validate_project(plasma_project(model_id))[0])
                changed = requested_input_hash(validate_project(
                    plasma_project(model_id, advanced={"ions": {"meanEnergy": 90}}))[0])
                self.assertNotEqual(first, changed)


class AdvancedConstructionPath(unittest.TestCase):
    def test_empty_advanced_uses_the_parameter_object_overload_with_identical_values(self):
        for dimension in (2, 3):
            for model_id in PLASMA_MODELS:
                with self.subTest(model=model_id, dimension=dimension):
                    plain_model, _extra, plain, _duration = build(plasma_project(model_id, dimension), dimension)
                    advanced_model, _extra, advanced, _duration = build(
                        plasma_project(model_id, dimension, advanced={}), dimension)
                    self.assertEqual(type(plain_model).__name__, type(advanced_model).__name__)
                    self.assertNotIn("nativeAdvancedParameters", plain)
                    metadata = advanced["nativeAdvancedParameters"]
                    self.assertEqual(metadata["parameterClass"], advanced_spec(model_id)["parameterClass"])
                    self.assertEqual(metadata["constructor"], model_spec(model_id)["nativeClass"])
                    self.assertIn("parameter-object", metadata["constructorOverload"])
                    self.assertEqual(metadata["requested"], {})
                    self.assertEqual(metadata["defaults"], metadata["effective"])
                    self.assertEqual(metadata["flatMapping"], advanced_spec(model_id)["flatMapping"])
                    # The parameter-object path must preserve the historical
                    # flat model's defaults, including model-specific drift.
                    spec = advanced_spec(model_id)
                    plain_params = plain_model.getParameters()
                    for name, block in spec["blocks"].items():
                        native = getattr(plain_params, block["native"])
                        self.assertEqual(metadata["defaults"]["blocks"][name],
                                         {field["key"]: float(getattr(native, field["key"]))
                                          for field in block["fields"]})
                    for field in spec["scalars"]:
                        value = getattr(plain_params, field["key"])
                        self.assertEqual(metadata["defaults"]["scalars"][field["key"]],
                                         value if field["kind"] == "boolean" else float(value))
                    for row in spec["maps"]:
                        self.assertEqual(metadata["defaults"]["maps"][row["key"]]["default"],
                                         float(getattr(plain_params, row["native"]).getDefault()))

    def test_advanced_overrides_are_applied_on_top_of_the_flat_values(self):
        for model_id in ("sf6o2", "hbro2", "sf6c4f8", "cf4o2"):
            with self.subTest(model=model_id):
                substrate_block = "si" if model_id == "cf4o2" else "substrate"
                advanced = {"ions": {"meanEnergy": 75.0, "sigmaEnergy": 12.0},
                            substrate_block: {"A_ie": 6.0}}
                model, _extra, actual, _duration = build(plasma_project(model_id, 2, advanced), 2)
                metadata = actual["nativeAdvancedParameters"]
                self.assertEqual(metadata["effective"]["blocks"]["ions"]["meanEnergy"], 75.0)
                self.assertEqual(metadata["effective"]["blocks"]["ions"]["sigmaEnergy"], 12.0)
                self.assertEqual(metadata["effective"]["blocks"][substrate_block]["A_ie"], 6.0)
                # Untouched fields keep their installed flat defaults.
                self.assertEqual(metadata["defaults"]["blocks"]["ions"]["n_l"], 10.0)
                self.assertEqual(type(model).__name__, model_spec(model_id)["nativeClass"])

    def test_model_specific_defaults_survive_the_parameter_object_path(self):
        _model, _extra, actual, _duration = build(
            plasma_project("hbro2", 2, {"ions": {"meanEnergy": 75.0}}), 2)
        defaults = actual["nativeAdvancedParameters"]["defaults"]["blocks"]["substrate"]
        self.assertEqual((defaults["A_ie"], defaults["A_sp"], defaults["Eth_sp"]), (5.0, 0.5, 30.0))

        _model, _extra, actual, _duration = build(
            plasma_project("sf6c4f8", 2, {"ions": {"meanEnergy": 75.0}}), 2)
        self.assertEqual(actual["nativeAdvancedParameters"]["defaults"]["scalars"]["passivationFlux"], 0.0)

        _model, _extra, actual, _duration = build(
            plasma_project("sf6o2", 2, {"ions": {"meanEnergy": 75.0}}), 2)
        self.assertEqual(actual["nativeAdvancedParameters"]["defaults"]["scalars"]["passivationFlux"], 0.2)

    def test_maps_record_effective_and_native_per_material_values(self):
        model, _extra, actual, _duration = build(
            plasma_project("cf4o2", 2, {"gamma_F": {"default": 0.5, "byMaterial": {"Si": 0.2}}}), 2)
        self.assertEqual(type(model).__name__, "CF4O2Etching")
        map_values = actual["nativeAdvancedParameters"]["effective"]["maps"]["gamma_F"]
        self.assertEqual(map_values["default"], 0.5)
        self.assertEqual(map_values["byMaterial"]["Si"], 0.2)
        self.assertEqual(map_values["byMaterial"]["Mask"], 0.7)
        defaults = actual["nativeAdvancedParameters"]["defaults"]["maps"]["gamma_F"]
        self.assertEqual(defaults["default"], 1.0)
        self.assertEqual(defaults["byMaterial"]["Si"], 0.7)

    def test_flat_bool_scalar_is_recorded(self):
        _model, _extra, actual, _duration = build(
            plasma_project("cf4o2", 2, {"fluxIncludeSticking": True}), 2)
        self.assertIs(actual["nativeAdvancedParameters"]["effective"]["scalars"]["fluxIncludeSticking"], True)


class AdvancedGoldenRun(unittest.TestCase):
    """Two changed sub-parameters must match a direct native reference."""

    ADVANCED = {"ions": {"meanEnergy": 60.0}, "substrate": {"A_ie": 4.0}}

    def reference_model(self, project: dict):
        parameters = project["steps"][0]["nativeParameters"]
        keys = [field["key"] for field in model_spec("sf6o2")["fields"]]
        flat_model = ps.SF6O2Etching(*(float(parameters[key]) for key in keys))
        reference = flat_model.getParameters()
        reference.Ions.meanEnergy = 60.0
        reference.Substrate.A_ie = 4.0
        return ps.SF6O2Etching(reference), flat_model

    def test_2d_and_3d_golden_runs_match_the_native_reference(self):
        for dimension in (2, 3):
            with self.subTest(dimension=dimension):
                project = plasma_project("sf6o2", dimension, self.ADVANCED)
                model, extra, actual, duration = build(project, dimension)
                workbench_top = run_model(model, duration, extra, dimension)

                reference, keep_alive = self.reference_model(project)
                coverage = ps.CoverageParameters()
                coverage.tolerance = 1e-3
                reference_top = run_model(reference, duration, [coverage], dimension)
                self.assertEqual(type(keep_alive).__name__, "SF6O2Etching")

                self.assertAlmostEqual(workbench_top, reference_top, delta=1e-6)
                metadata = actual["nativeAdvancedParameters"]
                self.assertEqual(metadata["effective"]["blocks"]["ions"]["meanEnergy"], 60.0)
                self.assertEqual(metadata["effective"]["blocks"]["substrate"]["A_ie"], 4.0)

                # The changed physics must actually move the surface; otherwise
                # the comparison above could pass for the wrong reason.
                default_model, default_extra, _default_actual, default_duration = build(
                    plasma_project("sf6o2", dimension), dimension)
                default_top = run_model(default_model, default_duration, default_extra, dimension)
                self.assertGreater(abs(default_top - workbench_top), 1e-7,
                                   (default_top, workbench_top))


class AdvancedValidationNegatives(unittest.TestCase):
    def assertRejected(self, project: dict, path_fragment: str | None = None):
        with self.assertRaises(ProjectInputError) as ctx:
            validate_project(project)
        if path_fragment is not None:
            paths = [issue.path for issue in ctx.exception.issues]
            self.assertTrue(any(path_fragment in path for path in paths), paths)
        return ctx.exception

    def test_unknown_top_level_and_nested_keys_are_rejected(self):
        for model_id in PLASMA_MODELS:
            with self.subTest(model=model_id, case="top"):
                self.assertRejected(plasma_project(model_id, advanced={"notANativeBlock": {}}),
                                    "nativeParameters.advanced")
            spec = advanced_spec(model_id)
            block = next(iter(spec["blocks"]))
            with self.subTest(model=model_id, case="nested", block=block):
                self.assertRejected(plasma_project(model_id, advanced={block: {"notAField": 1.0}}),
                                    f"advanced.{block}")

    def test_blocks_are_not_shared_between_parameter_classes(self):
        cases = (
            ("sf6o2", {"si": {"A_ie": 1.0}}),
            ("sf6o2", {"siGe": {"A_ie": 1.0}}),
            ("sf6o2", {"gamma_F": {"default": 0.5}}),
            ("sf6o2", {"fluxIncludeSticking": True}),
            ("cf4o2", {"polymer": {"A_sp": 0.1}}),
            ("cf4o2", {"substrate": {"A_ie": 1.0}}),
            ("cf4o2", {"beta_E": {"default": 0.5}}),
            ("cf4o2", {"passivationFlux": 1.0}),
            ("cf4o2", {"ions": {"thetaRMin": 1.0}}),
            ("cf4o2", {"mask": {"B_sp": 1.0}}),
        )
        for model_id, advanced in cases:
            with self.subTest(model=model_id, advanced=advanced):
                self.assertRejected(plasma_project(model_id, advanced=advanced), "advanced")

    def test_advanced_is_rejected_for_models_without_the_family(self):
        project = make_project("fluorocarbon", 2)
        project["steps"][0]["nativeParameters"]["advanced"] = {}
        self.assertRejected(project)

    def test_wrong_types_and_non_finite_numbers_are_rejected(self):
        cases = (
            ("sf6o2", {"ions": []}, "advanced.ions"),
            ("sf6o2", {"ions": {"meanEnergy": "100"}}, "advanced.ions.meanEnergy"),
            ("sf6o2", {"ions": {"meanEnergy": True}}, "advanced.ions.meanEnergy"),
            ("sf6o2", {"ions": {"meanEnergy": float("nan")}}, "advanced.ions.meanEnergy"),
            ("sf6o2", {"ions": {"meanEnergy": float("inf")}}, "advanced.ions.meanEnergy"),
            ("sf6o2", {"polymer": {"usePolyCosThetaYield": 1}}, "usePolyCosThetaYield"),
            ("sf6o2", {"passivationFlux": "high"}, "advanced.passivationFlux"),
            ("sf6o2", {"beta_E": []}, "advanced.beta_E"),
            ("sf6o2", {"beta_E": {"unknown": 1.0}}, "advanced.beta_E"),
        )
        for model_id, advanced, fragment in cases:
            with self.subTest(advanced=advanced):
                self.assertRejected(plasma_project(model_id, advanced=advanced), fragment)

    def test_out_of_range_values_are_rejected(self):
        cases = (
            ("sf6o2", {"ions": {"meanEnergy": -1.0}}, "meanEnergy"),
            ("sf6o2", {"ions": {"minAngle": 4.0}}, "minAngle"),
            ("sf6o2", {"mask": {"rho": 0.0}}, "rho"),
            ("sf6o2", {"substrate": {"rho": -2.0}}, "rho"),
            ("sf6o2", {"passivationFlux": -0.1}, "passivationFlux"),
            ("sf6o2", {"beta_P": {"default": -1.0}}, "beta_P"),
            ("cf4o2", {"siGe": {"x": 1.5}}, "x"),
            ("cf4o2", {"gamma_O": {"default": 1.5}}, "gamma_O"),
            ("cf4o2", {"gamma_C": {"byMaterial": {"Si": -0.1}}}, "gamma_C"),
        )
        for model_id, advanced, fragment in cases:
            with self.subTest(advanced=advanced):
                self.assertRejected(plasma_project(model_id, advanced=advanced), fragment)

    def test_inconsistent_ion_angle_blocks_are_rejected(self):
        cases = (
            ("sf6o2", {"ions": {"thetaRMin": 1.6, "thetaRMax": 1.5}}, "thetaRMin"),
            ("sf6o2", {"ions": {"minAngle": 1.7}}, "minAngle"),
            ("hbro2", {"ions": {"inflectAngle": 1.0}}, "minAngle"),
        )
        for model_id, advanced, fragment in cases:
            with self.subTest(model=model_id, advanced=advanced):
                self.assertRejected(plasma_project(model_id, advanced=advanced), fragment)

    def test_map_material_names_must_exist(self):
        exception = self.assertRejected(
            plasma_project("cf4o2", advanced={"gamma_F": {"byMaterial": {"NotAMaterial": 0.5}}}), "gamma_F")
        self.assertTrue(any("unknown" in issue.message.lower() for issue in exception.issues))

    def test_existing_flat_validation_is_unchanged(self):
        project = plasma_project("sf6o2", advanced={"ions": {"meanEnergy": 75.0}})
        project["steps"][0]["nativeParameters"]["ionFlux"] = -1.0
        self.assertRejected(project, "nativeParameters.ionFlux")


class AdvancedPersistenceAndReplay(unittest.IsolatedAsyncioTestCase):
    ADVANCED = {"ions": {"meanEnergy": 60.0}, "substrate": {"A_ie": 4.0}}

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
        self.fail("plasma advanced run did not finish")

    def test_project_saves_and_reopens_with_advanced_parameters(self):
        storage.initialize()
        project = plasma_project("sf6o2", 2, self.ADVANCED)
        parsed, _warnings = validate_project(project)
        normalized = parsed.model_dump(mode="json", exclude_none=True)
        run_hash = requested_input_hash(parsed)
        record = storage.create_project(normalized)
        reopened = storage.read_json(storage.project_path(record["id"]))["project"]
        self.assertEqual(reopened["steps"][0]["nativeParameters"]["advanced"], self.ADVANCED)
        reparsed, _warnings = validate_project(reopened)
        self.assertEqual(requested_input_hash(reparsed), run_hash)

    async def test_cpu_run_records_metadata_and_replays(self):
        storage.initialize()
        project = plasma_project("sf6o2", 2, self.ADVANCED)
        project["name"] = "plasma advanced CPU replay"
        project["solver"]["enginePreference"] = "cpu"
        project["solver"]["timeoutSeconds"] = 90
        response = await asgi_request("/api/runs", method="POST",
                                      payload={"kind": "simulate", "project": project})
        self.assertEqual(response[0], 202, response[2])
        run_id = json_body(response)["runId"]
        await self.wait(run_id)

        manifest = storage.read_json(storage.run_path(run_id) / "manifest.json")
        execution = manifest["actualExecution"][0]
        metadata = execution["actualParameters"]["nativeModel"]["nativeAdvancedParameters"]
        self.assertEqual(metadata["requested"], self.ADVANCED)
        self.assertEqual(metadata["effective"]["blocks"]["ions"]["meanEnergy"], 60.0)
        self.assertEqual(metadata["effective"]["blocks"]["substrate"]["A_ie"], 4.0)

        envelope = json_body(await asgi_request(f"/api/runs/{run_id}/replay-project"))
        self.assertEqual(envelope["normalizedProject"]["steps"][0]["nativeParameters"]["advanced"], self.ADVANCED)
        replay = await asgi_request("/api/runs/replay", method="POST", payload=envelope)
        self.assertEqual(replay[0], 202, replay[2])
        replay_id = json_body(replay)["runId"]
        await self.wait(replay_id)
        replay_manifest = storage.read_json(storage.run_path(replay_id) / "manifest.json")
        replay_metadata = replay_manifest["actualExecution"][0]["actualParameters"]["nativeModel"][
            "nativeAdvancedParameters"]
        self.assertEqual(replay_manifest["requestedInputHash"], manifest["requestedInputHash"])
        self.assertEqual(replay_metadata["effective"], metadata["effective"])


if __name__ == "__main__":
    unittest.main()
