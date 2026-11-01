"""Direct parity tests for the worker-side v2 isotropic bridge.

Each test builds two identical level-set domains with the installed
``viennaps`` module, runs the historical worker level-set path on one and
:func:`worker.process_models_v2.run_isotropic_v2` on the other, and compares
them through :func:`worker.process_models_v2.compare_isotropic_surfaces`.
No server, worker subprocess, or data directory is involved.
"""

from __future__ import annotations

import unittest
from typing import Any

import viennaps as ps

import worker.simulate as simulate
from worker.process_models_v2 import (
    build_isotropic_params,
    compare_isotropic_surfaces,
    material_order,
    run_isotropic_v2,
)

GRID_DELTA = 0.2
X_EXTENT = 4.0
VIENNAPS_VERSION = str(ps.__version__)


def plane_project(dimension: int = 2) -> dict[str, Any]:
    """Minimal schema-5 project with a plane and no steps."""
    domain = {"gridDelta": GRID_DELTA, "xExtent": X_EXTENT}
    if dimension == 2:
        domain["xBoundary"] = "reflective"
    else:
        domain["yExtent"] = X_EXTENT
        domain["boundaryXY"] = "reflective"
    return {
        "schemaVersion": 5,
        "name": f"v2 bridge {dimension}D",
        "dimension": dimension,
        "units": {"length": "um", "time": "s", "rate": "um/s", "angle": "deg"},
        "domain": domain,
        "geometry": {"kind": "plane", "height": 0.0, "substrateMaterial": "Si"},
        "steps": [],
        "solver": {"timeStepRatio": 0.25, "raysPerPoint": 32, "cpuThreads": 1},
    }


def trench_project() -> dict[str, Any]:
    """Minimal schema-5 project with a masked trench."""
    project = plane_project(2)
    project["geometry"] = {
        "kind": "trench",
        "width": 1.6,
        "depth": 1.2,
        "taperAngle": 0.0,
        "maskHeight": 0.6,
        "maskTaperAngle": 0.0,
        "substrateMaterial": "Si",
        "maskMaterial": "Mask",
    }
    return project


def isotropic_step(
    operation: str = "etch",
    *,
    rate: float = 0.3,
    duration: float = 1.0,
    ratio: float | None = None,
) -> dict[str, Any]:
    step: dict[str, Any] = {
        "id": f"step_iso_{operation}",
        "name": "各向同性",
        "enabled": True,
        "operation": operation,
        "model": "isotropic",
        "rateMagnitude": rate,
        "duration": duration,
        "rateMode": "uniform",
    }
    if operation == "deposit":
        step["depositMaterial"] = "SiO2"
    if ratio is not None:
        step["isotropicTimeStepRatio"] = ratio
    return step


def plane_domain(dimension: int = 2) -> Any:
    ps.setDimension(dimension)
    if dimension == 2:
        domain = ps.Domain(gridDelta=GRID_DELTA, xExtent=X_EXTENT, boundary=ps.BoundaryType.REFLECTIVE_BOUNDARY)
    else:
        domain = ps.Domain(
            gridDelta=GRID_DELTA,
            xExtent=X_EXTENT,
            yExtent=X_EXTENT,
            boundary=ps.BoundaryType.REFLECTIVE_BOUNDARY,
        )
    ps.MakePlane(domain, height=0.0, material=ps.Material.Si, addToExisting=False).apply()
    return domain


def trench_domain() -> Any:
    ps.setDimension(2)
    domain = ps.Domain(gridDelta=GRID_DELTA, xExtent=X_EXTENT, boundary=ps.BoundaryType.REFLECTIVE_BOUNDARY)
    ps.MakeTrench(
        domain=domain,
        trenchWidth=1.6,
        trenchDepth=1.2,
        trenchTaperAngle=0.0,
        maskHeight=0.6,
        maskTaperAngle=0.0,
        halfTrench=False,
        material=ps.Material.Si,
        maskMaterial=ps.Material.Mask,
    ).apply()
    return domain


def _old_reference(ps_module: Any, project: dict[str, Any], step: dict[str, Any], domain: Any) -> None:
    """Mirror the worker's isotropic level-set path in place."""
    if step["operation"] == "deposit":
        domain.duplicateTopLevelSet(simulate.material_handle(ps_module, step["depositMaterial"]))
    rate, _signed = simulate.signed_rates(step)
    model = ps_module.IsotropicProcess(
        rate=rate,
        maskMaterials=[
            simulate.material_handle(ps_module, name) for name in simulate.protected_material_names(project, step)
        ],
    )
    process = ps_module.Process(domain, model, float(step["duration"]))
    process.setFluxEngineType(ps_module.FluxEngineType.CPU_DISK)
    advection, _actual = simulate.configure_advection(
        ps_module,
        project,
        time_step_ratio=step.get("isotropicTimeStepRatio"),
    )
    process.setParameters(advection)
    process.apply()


def _top(domain: Any, dimension: int) -> float:
    mesh = domain.getSurfaceMesh(addInterfaces=True, sharpCorners=False, minNodeDistanceFactor=0.01)
    axis = 1 if dimension == 2 else 2
    return max(float(node[axis]) for node in mesh.getNodes())


class MaterialOrderTests(unittest.TestCase):
    def setUp(self) -> None:
        ps.setNumThreads(1)
        ps.Length.setUnit("um")

    def test_material_order_matches_simulate_material_map(self) -> None:
        for label, domain in (
            ("plane", plane_domain(2)),
            ("trench", trench_domain()),
        ):
            with self.subTest(geometry=label):
                self.assertEqual(list(material_order(domain)), simulate.material_map(domain))
                self.assertEqual(material_order(domain), tuple(simulate.material_map(domain)))

        deposited = plane_domain(2)
        deposited.duplicateTopLevelSet(ps.Material.SiO2)
        self.assertEqual(list(material_order(deposited)), simulate.material_map(deposited))
        self.assertEqual(material_order(deposited), (int(ps.Material.Si), int(ps.Material.SiO2)))


class BuildParamsTests(unittest.TestCase):
    def test_uniform_deposit_params(self) -> None:
        step = isotropic_step("deposit", rate=0.3, duration=1.0, ratio=0.25)
        params = build_isotropic_params(step)
        self.assertEqual(params.operation, "deposit")
        self.assertEqual(params.rateMode, "uniform")
        self.assertEqual(params.rateMagnitude, 0.3)
        self.assertEqual(params.duration, 1.0)
        self.assertEqual(params.depositMaterial, "SiO2")
        self.assertEqual(params.maskMaterials, [])
        self.assertEqual(params.materialRates, {})
        self.assertEqual(params.isotropicTimeStepRatio, 0.25)

        without_ratio = build_isotropic_params(isotropic_step("etch"))
        self.assertIsNone(without_ratio.isotropicTimeStepRatio)
        self.assertEqual(without_ratio.operation, "etch")

    def test_material_rate_mode_is_rejected(self) -> None:
        step = isotropic_step("etch")
        step["rateMode"] = "material"
        step["materialRates"] = {"Si": 0.2}
        with self.assertRaises(ValueError) as raised:
            build_isotropic_params(step)
        self.assertIn("rateMode", str(raised.exception))
        self.assertIn("material", str(raised.exception))


class IsotropicParityTests(unittest.TestCase):
    def setUp(self) -> None:
        ps.setNumThreads(1)
        ps.Length.setUnit("um")

    def _assert_parity(
        self,
        reference: Any,
        candidate: Any,
        dimension: int,
    ) -> dict[str, Any]:
        result = compare_isotropic_surfaces(reference, candidate, dimension)
        self.assertTrue(result["matched"], result["reasons"])
        self.assertEqual(result["reasons"], [])
        self.assertEqual(result["oldDigest"], result["newDigest"])
        self.assertEqual(result["oldMaterialMap"], result["newMaterialMap"])
        self.assertEqual(result["oldMaterialMap"], simulate.material_map(candidate))
        self.assertGreater(result["oldPointCount"], 0)
        self.assertGreater(result["oldCellCount"], 0)
        self.assertEqual(result["oldPointCount"], result["newPointCount"])
        self.assertEqual(result["oldCellCount"], result["newCellCount"])
        return result

    def test_etch_matches_the_worker_level_set_path(self) -> None:
        project = plane_project(2)
        step = isotropic_step("etch", rate=0.3, duration=1.0, ratio=0.25)
        reference = plane_domain(2)
        candidate = plane_domain(2)
        before = _top(candidate, 2)

        _old_reference(ps, project, step, reference)
        outcome = run_isotropic_v2(ps, project, step, candidate, ps_version=VIENNAPS_VERSION)

        self._assert_parity(reference, candidate, 2)
        self.assertAlmostEqual(_top(candidate, 2) - before, -0.3, delta=1e-6)

        self.assertIs(outcome.delta.geometry.handle, candidate)
        self.assertIs(outcome.state.geometry.handle, candidate)
        self.assertEqual(outcome.delta.geometry.dimension, 2)
        self.assertEqual(outcome.delta.geometry.material_order, material_order(candidate))
        self.assertEqual(outcome.metadata["nativeClass"], "ps.IsotropicProcess")
        self.assertEqual(outcome.metadata["operation"], "etch")
        self.assertEqual(outcome.metadata["timeStepRatio"], 0.25)

        identity = outcome.plan.identity()
        self.assertEqual(identity["process_id"], "isotropic")
        self.assertEqual(identity["variant_id"], "native")
        self.assertEqual(identity["engine_id"], "viennaps")
        self.assertEqual(identity["engine_version"], VIENNAPS_VERSION)
        self.assertEqual(identity["engine_fingerprint"], "worker-v2-isotropic")

    def test_deposit_matches_the_worker_level_set_path(self) -> None:
        project = plane_project(2)
        step = isotropic_step("deposit", rate=0.3, duration=1.0)
        reference = plane_domain(2)
        candidate = plane_domain(2)
        before = _top(candidate, 2)

        _old_reference(ps, project, step, reference)
        outcome = run_isotropic_v2(
            ps,
            project,
            step,
            candidate,
            ps_version=VIENNAPS_VERSION,
            engine_fingerprint="worker-v2-test",
        )

        self._assert_parity(reference, candidate, 2)
        self.assertAlmostEqual(_top(candidate, 2) - before, 0.3, delta=1e-6)
        self.assertIn(int(ps.Material.SiO2), simulate.material_map(candidate))
        self.assertEqual(outcome.metadata["depositMaterial"], "SiO2")
        self.assertEqual(outcome.plan.identity()["engine_fingerprint"], "worker-v2-test")

    def test_masked_trench_matches_the_worker_level_set_path(self) -> None:
        project = trench_project()
        step = isotropic_step("etch", rate=0.2, duration=0.5)
        reference = trench_domain()
        candidate = trench_domain()
        self.assertEqual(simulate.protected_material_names(project, step), ["Mask"])

        _old_reference(ps, project, step, reference)
        outcome = run_isotropic_v2(ps, project, step, candidate, ps_version=VIENNAPS_VERSION)

        self._assert_parity(reference, candidate, 2)
        self.assertEqual(outcome.metadata["maskMaterials"], ["Mask"])
        self.assertIn(int(ps.Material.Mask), simulate.material_map(candidate))

    def test_three_dimensional_plane_smoke(self) -> None:
        project = plane_project(3)
        step = isotropic_step("etch", rate=0.2, duration=0.5)
        candidate = plane_domain(3)
        before = _top(candidate, 3)

        outcome = run_isotropic_v2(ps, project, step, candidate, ps_version=VIENNAPS_VERSION)

        self.assertAlmostEqual(_top(candidate, 3) - before, -0.1, delta=1e-6)
        self.assertEqual(outcome.delta.geometry.dimension, 3)
        self.assertEqual(outcome.delta.geometry.material_order, material_order(candidate))
        self.assertEqual(outcome.state.geometry.dimension, 3)
        self.assertEqual(outcome.metadata["dimension"], 3)
        surface = simulate.surface_state(candidate, 3)
        self.assertGreater(surface["pointCount"], 0)
        self.assertEqual(material_order(candidate), (int(ps.Material.Si),))


class CompareMismatchTests(unittest.TestCase):
    def setUp(self) -> None:
        ps.setNumThreads(1)
        ps.Length.setUnit("um")

    def test_compare_reports_mismatch_without_raising(self) -> None:
        etched = plane_domain(2)
        untouched = plane_domain(2)
        _old_reference(ps, plane_project(2), isotropic_step("etch", rate=0.3, duration=1.0), etched)

        result = compare_isotropic_surfaces(etched, untouched, 2)

        self.assertFalse(result["matched"])
        self.assertNotEqual(result["oldDigest"], result["newDigest"])
        self.assertEqual(result["oldMaterialMap"], result["newMaterialMap"])
        self.assertEqual(len(result["reasons"]), 1)
        self.assertIn("digest", result["reasons"][0])


if __name__ == "__main__":
    unittest.main()
