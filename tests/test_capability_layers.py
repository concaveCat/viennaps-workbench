"""Per-layer stack trenches backed by the installed MakeTrench(MaterialLayer[])."""

from __future__ import annotations

import copy
import math
import unittest

import viennaps as ps
from pydantic import ValidationError

from backend.hashing import requested_input_hash
from backend.models import StackV7, parse_project
from backend.validation import ProjectInputError, validate_project
from worker.simulate import make_v3_geometry


# Hashes recorded on the base commit (205dd7a) before the optional
# ``pattern.layers`` block existed. They prove that omitting the block keeps
# both the request text and the legacy top-layer stencil unchanged.
BASE_HASH = {
    2: "29784db671c11b7d74cc1604b49ab2ae48d43641f452f9d76c763a27d51efaf0",
    3: "31fe30193cbc6d480edc2edd6b7acde1f38ee81325ff9570740a8380be08ca44",
}


def project_value(dimension: int, pattern: dict, *, grid_delta: float = 0.2) -> dict:
    value = {
        "schemaVersion": 3,
        "name": "per-layer trench",
        "dimension": dimension,
        "units": {"length": "um", "time": "s", "rate": "um/s", "angle": "deg"},
        "domain": {"gridDelta": grid_delta, "xExtent": 4.0},
        "geometry": {
            "kind": "stack",
            "baseHeight": 0.0,
            "substrateMaterial": "Si",
            "layers": [
                {"id": "oxide_layer", "name": "氧化层", "material": "SiO2",
                 "thickness": 0.6, "role": "film"},
                {"id": "nitride_layer", "name": "掩膜", "material": "Si3N4",
                 "thickness": 0.6, "role": "mask"},
            ],
            "pattern": pattern,
        },
        "steps": [],
        "solver": {"timeStepRatio": 0.25, "raysPerPoint": 32, "cpuThreads": 1,
                   "random": {"mode": "fixed", "seed": 314159},
                   "enginePreference": "cpu", "timeoutSeconds": 30},
    }
    if dimension == 2:
        value["domain"]["xBoundary"] = "reflective"
        value["view"] = {"panX": 0.0, "panH": 0.0, "zoom": 1.0, "showGrid": True}
    else:
        value["domain"].update({"yExtent": 4.0, "boundaryXY": "reflective"})
        value["view3D"] = {
            "projection": "parallel", "camera": None, "displayMode": "surface",
            "visibleMaterialIds": [], "opacityByMaterial": {},
            "clip": {"enabled": False, "axis": "x", "position": 0.0, "keep": "positive"},
            "section": {"enabled": False, "axis": "y", "position": 0.0},
        }
    return value


def per_layer_pattern(*, film_mask: bool = True) -> dict:
    return {"kind": "trench", "layers": [
        {"layerIndex": 0, "width": 1.6, "taperAngle": 0.0, "isMask": film_mask},
        {"layerIndex": 1, "width": 0.8, "taperAngle": 0.0, "isMask": True},
    ]}


def new_domain(dimension: int, grid_delta: float = 0.2):
    boundary = ps.BoundaryType.REFLECTIVE_BOUNDARY
    ps.setDimension(dimension)
    if dimension == 3:
        return ps.Domain(grid_delta, 4.0, 4.0, boundary)
    return ps.Domain(grid_delta, 4.0, boundary=boundary)


def capture(domain, dimension: int) -> dict:
    mesh = domain.getSurfaceMesh(addInterfaces=True, sharpCorners=False,
                                 minNodeDistanceFactor=0.01)
    cell_data = mesh.getCellData()
    material_ids = None
    for index in range(cell_data.getScalarDataSize()):
        if cell_data.getScalarDataLabel(index) == "MaterialIds":
            material_ids = [int(value) for value in cell_data.getScalarData(index)]
    material_map = domain.getMaterialMap()
    return {
        "points": mesh.getNodes(),
        "materialIds": material_ids,
        "materialMap": [int(material_map.getMaterialIdAtIdx(index))
                        for index in range(material_map.size())],
    }


def native_material_layers(project: dict):
    geometry = project["geometry"]
    stack = geometry["layers"]
    cutouts = {}
    for entry in geometry["pattern"]["layers"]:
        index = entry.get("layerIndex")
        if index is None:
            index = next(i for i, layer in enumerate(stack)
                         if layer["id"] == entry["layerId"])
        cutouts[index] = entry
    layers = [ps.MakeTrench.MaterialLayer(
        height=float(geometry.get("baseHeight", 0.0)) + 1e-4,
        width=0.0, taperAngle=0.0,
        material=ps.MaterialRegistry.instance().getMaterial(
            geometry["substrateMaterial"]), isMask=False)]
    for index, layer in enumerate(stack):
        entry = cutouts.get(index)
        layers.append(ps.MakeTrench.MaterialLayer(
            height=float(layer["thickness"]),
            width=float(entry["width"]) if entry else 0.0,
            taperAngle=float(entry["taperAngle"]) if entry else 0.0,
            material=ps.MaterialRegistry.instance().getMaterial(layer["material"]),
            isMask=bool(entry["isMask"]) if entry else False))
    return layers


def legacy_geometry(ps_module, domain, project: dict) -> None:
    """The pre-feature stack builder, kept here as the artifact baseline."""
    geometry = project["geometry"]
    dimension = project["dimension"]
    height = float(geometry.get("baseHeight", 0.0))
    for layer in geometry["layers"]:
        height += float(layer["thickness"])
        ps_module.MakePlane(
            domain, height=height,
            material=ps_module.MaterialRegistry.instance().getMaterial(layer["material"]),
            addToExisting=True).apply()
    pattern = geometry["pattern"]
    top = geometry["layers"][-1]
    thickness = float(top["thickness"])
    eps = float(project["domain"]["gridDelta"]) * 1e-3
    position = [0.0] * dimension
    position[-1] = height - thickness - eps
    factory = ps_module.GeometryFactory(domain.getSetup(), "LegacyReference")
    stencil = factory.makeBoxStencil(position, float(pattern["width"]),
                                     thickness + 2 * eps,
                                     -float(pattern["taperAngle"]))
    domain.applyBooleanOperation(stencil,
                                 ps_module.ls.BooleanOperationEnum.RELATIVE_COMPLEMENT,
                                 False)


class PerLayerTrenchNativeReference(unittest.TestCase):
    def test_2d_and_3d_match_independent_native_material_layers(self):
        for dimension in (2, 3):
            with self.subTest(dimension=dimension):
                project = project_value(dimension, per_layer_pattern())
                normalized, _ = validate_project(project)
                value = normalized.model_dump(mode="json", exclude_none=True)

                domain = new_domain(dimension)
                readback = make_v3_geometry(ps, domain, value)
                actual = capture(domain, dimension)

                reference_domain = new_domain(dimension)
                ps.MakeTrench(domain=reference_domain,
                              materialLayers=native_material_layers(value),
                              halfTrench=False).apply()
                expected = capture(reference_domain, dimension)

                self.assertEqual(actual["points"], expected["points"])
                self.assertEqual(actual["materialIds"], expected["materialIds"])
                self.assertEqual(actual["materialMap"], expected["materialMap"])
                self.assertEqual(
                    readback["perLayerPattern"]["nativeConstructor"],
                    "viennaps.MakeTrench(domain, materialLayers=MaterialLayer[], halfTrench=False)")
                self.assertEqual(
                    [row["height"] for row in readback["perLayerPattern"]["layers"]],
                    [1e-4, 0.6, 0.6])
                self.assertEqual(
                    [row["width"] for row in readback["perLayerPattern"]["layers"]],
                    [0.0, 1.6, 0.8])
                self.assertEqual(
                    [row["isMask"] for row in readback["perLayerPattern"]["layers"]],
                    [False, True, True])

    def test_per_layer_width_taper_and_mask_are_applied(self):
        for dimension in (2, 3):
            with self.subTest(dimension=dimension):
                project = project_value(dimension, per_layer_pattern(), grid_delta=0.05)
                normalized, _ = validate_project(project)
                value = normalized.model_dump(mode="json", exclude_none=True)
                domain = new_domain(dimension, 0.05)
                make_v3_geometry(ps, domain, value)
                points = capture(domain, dimension)["points"]
                vertical = 1 if dimension == 2 else 2
                interior = [point for point in points if abs(point[0]) < 1.9]

                def wall(lo: float, hi: float):
                    wall_points = [point[0] for point in interior
                                   if lo < point[vertical] < hi]
                    return (min(wall_points), max(wall_points)) if wall_points else None

                # The film is cut at its own 1.6 um width, the top mask at 0.8 um.
                self.assertAlmostEqual(wall(0.05, 0.55)[0], -0.8, places=6)
                self.assertAlmostEqual(wall(0.05, 0.55)[1], 0.8, places=6)
                self.assertAlmostEqual(wall(0.65, 1.15)[0], -0.4, places=6)
                self.assertAlmostEqual(wall(0.65, 1.15)[1], 0.4, places=6)

                # isMask=false on the film leaves it intact; only the top mask
                # is opened, so there is no wall inside the film band.
                blind = project_value(dimension, per_layer_pattern(film_mask=False),
                                      grid_delta=0.05)
                normalized, _ = validate_project(blind)
                blind_value = normalized.model_dump(mode="json", exclude_none=True)
                blind_domain = new_domain(dimension, 0.05)
                make_v3_geometry(ps, blind_domain, blind_value)
                blind_points = capture(blind_domain, dimension)["points"]
                blind_interior = [point for point in blind_points if abs(point[0]) < 1.9]
                self.assertEqual(
                    [point[0] for point in blind_interior
                     if 0.05 < point[vertical] < 0.55], [])

                # A positive per-layer taper narrows the cut towards its bottom.
                tapered = per_layer_pattern()
                tapered["layers"][0]["taperAngle"] = 10.0
                tapered_project = project_value(dimension, tapered, grid_delta=0.05)
                normalized, _ = validate_project(tapered_project)
                tapered_value = normalized.model_dump(mode="json", exclude_none=True)
                tapered_domain = new_domain(dimension, 0.05)
                make_v3_geometry(ps, tapered_domain, tapered_value)
                tapered_points = [point for point in capture(tapered_domain, dimension)["points"]
                                  if abs(point[0]) < 1.9]
                near_top = max(point[0] for point in tapered_points
                               if 0.05 < point[vertical] < 0.15)
                near_bottom = max(point[0] for point in tapered_points
                                  if 0.45 < point[vertical] < 0.55)
                # Native per-layer cutouts anchor width at the layer top, so a
                # positive angle widens the lower part of the same layer.
                self.assertGreater(near_bottom, near_top + 0.05)

    def test_legacy_top_pair_is_accepted_when_consistent(self):
        pattern = per_layer_pattern()
        pattern.update({"width": 0.8, "taperAngle": 0.0})
        normalized, _ = validate_project(project_value(2, pattern))
        self.assertEqual(normalized.geometry["pattern"]["width"], 0.8)
        self.assertEqual(normalized.geometry["pattern"]["taperAngle"], 0.0)

    def test_replay_is_deterministic(self):
        for dimension in (2, 3):
            with self.subTest(dimension=dimension):
                project = project_value(dimension, per_layer_pattern())
                normalized, _ = validate_project(project)
                value = normalized.model_dump(mode="json", exclude_none=True)
                signatures = []
                for _ in range(2):
                    domain = new_domain(dimension)
                    make_v3_geometry(ps, domain, value)
                    signatures.append(capture(domain, dimension))
                self.assertEqual(signatures[0], signatures[1])
                self.assertEqual(requested_input_hash(parse_project(project)),
                                 requested_input_hash(normalized))

                renamed = copy.deepcopy(project)
                renamed["geometry"]["layers"][0]["id"] = "renamed_film"
                renamed["geometry"]["layers"][0]["name"] = "renamed film"
                renamed["geometry"]["pattern"]["layers"][0] = {
                    "layerId": "renamed_film", "width": 1.6,
                    "taperAngle": 0.0, "isMask": True}
                self.assertEqual(requested_input_hash(validate_project(renamed)[0]),
                                 requested_input_hash(normalized))


class LegacyStackStability(unittest.TestCase):
    def test_omitted_block_keeps_hash_and_legacy_artifact(self):
        for dimension in (2, 3):
            with self.subTest(dimension=dimension):
                project = project_value(
                    dimension, {"kind": "trench", "width": 1.6, "taperAngle": 0.0})
                normalized, _ = validate_project(project)
                self.assertEqual(requested_input_hash(normalized), BASE_HASH[dimension])
                self.assertNotIn("layers", normalized.model_dump(
                    mode="json", exclude_none=True)["geometry"]["pattern"])

                value = normalized.model_dump(mode="json", exclude_none=True)
                domain = new_domain(dimension)
                readback = make_v3_geometry(ps, domain, value)
                actual = capture(domain, dimension)
                self.assertIsNone(readback)

                reference_domain = new_domain(dimension)
                ps.MakePlane(
                    reference_domain, height=0.0,
                    material=ps.MaterialRegistry.instance().getMaterial("Si"),
                    addToExisting=False).apply()
                legacy_geometry(ps, reference_domain, value)
                expected = capture(reference_domain, dimension)
                self.assertEqual(actual, expected)


class PerLayerTrenchValidation(unittest.TestCase):
    def assert_invalid(self, project: dict):
        with self.assertRaises(ProjectInputError):
            validate_project(project)

    def test_negative_cases(self):
        cases = {}

        cases["hole"] = project_value(3, {"kind": "hole", "radius": 0.8,
                                          "taperAngle": 0.0,
                                          "layers": per_layer_pattern()["layers"]})
        cases["none"] = project_value(2, {"kind": "none",
                                          "layers": per_layer_pattern()["layers"]})

        unknown_key = per_layer_pattern()
        unknown_key["layers"][0]["unexpected"] = True
        cases["unknown entry key"] = project_value(2, unknown_key)

        both_refs = per_layer_pattern()
        both_refs["layers"][0]["layerId"] = "oxide_layer"
        cases["two references"] = project_value(2, both_refs)

        outside = per_layer_pattern()
        outside["layers"][0]["layerIndex"] = 9
        cases["index outside stack"] = project_value(2, outside)

        unknown_id = per_layer_pattern()
        unknown_id["layers"][0] = {"layerId": "missing", "width": 1.6,
                                   "taperAngle": 0.0, "isMask": True}
        cases["unknown id"] = project_value(2, unknown_id)

        duplicate = per_layer_pattern()
        duplicate["layers"][1] = {"layerIndex": 0, "width": 0.8,
                                  "taperAngle": 0.0, "isMask": True}
        cases["duplicate"] = project_value(2, duplicate)

        reversed_order = per_layer_pattern()
        reversed_order["layers"].reverse()
        cases["reversed order"] = project_value(2, reversed_order)

        missing_top = per_layer_pattern()
        missing_top["layers"] = missing_top["layers"][:1]
        cases["missing top"] = project_value(2, missing_top)

        top_intact = per_layer_pattern()
        top_intact["layers"][1]["isMask"] = False
        cases["top intact"] = project_value(2, top_intact)

        negative_base = project_value(2, per_layer_pattern())
        negative_base["geometry"]["baseHeight"] = -0.2
        cases["negative baseHeight"] = negative_base

        mismatch = project_value(2, {"kind": "trench", "width": 2.0,
                                     "taperAngle": 0.0,
                                     "layers": per_layer_pattern()["layers"]})
        cases["legacy mismatch"] = mismatch

        for name, project in cases.items():
            with self.subTest(case=name):
                self.assert_invalid(project)

        numeric = {
            "zero width": 0.0,
            "angle": 46.0,
            "nan": float("nan"),
            "bool": True,
        }
        for name, width_or_angle in numeric.items():
            with self.subTest(case=name):
                pattern = per_layer_pattern()
                key = "width" if name in {"zero width", "nan", "bool"} else "taperAngle"
                pattern["layers"][0][key] = width_or_angle
                self.assert_invalid(project_value(2, pattern))

    def test_incompatible_geometry_and_domain_fit(self):
        too_wide = per_layer_pattern()
        too_wide["layers"][0]["width"] = 4.0
        self.assert_invalid(project_value(2, too_wide))

        closes = per_layer_pattern()
        closes["layers"][0]["width"] = 0.5
        closes["layers"][0]["taperAngle"] = -45.0
        closes["layers"][1]["width"] = 0.5
        self.assert_invalid(project_value(2, closes))

        gds_layer = project_value(2, per_layer_pattern())
        gds_layer["geometry"]["layers"][-1]["source"] = "gds-mask"
        self.assert_invalid(gds_layer)

        # The schema-7 GDS overlay is inserted after the native stack build;
        # combining it with the native per-layer constructor is ambiguous.
        value = project_value(3, per_layer_pattern())
        value["schemaVersion"] = 7
        value["geometry"]["gdsMask"] = {
            "assetId": "a" * 64, "fileName": "mask.gds", "structure": "TOP",
            "layer": 1, "datatype": 0,
        }
        with self.assertRaises(ValidationError):
            StackV7.model_validate(value["geometry"])


if __name__ == "__main__":
    unittest.main()
