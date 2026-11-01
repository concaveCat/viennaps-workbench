"""Parity tests for the shared native GeometryEngine.

The v1 worker geometry operation is the reference. Both paths start from an
identical copy of the same native domain and are compared by exposed surface
digest and material map.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import viennaps as ps

from plugins.isotropic import register as register_isotropic
from process_models.v2.contract import ProcessContext
from process_models.v2.engine import EngineCapabilities, StaticEngine, StaticEngineProvider
from process_models.v2.executor import FlowExecutor, FlowStep
from process_models.v2.geometry import (
    BooleanParams,
    GeometryEngine,
    GeometryEngineError,
    ParticleShapeParams,
    PlanarizeParams,
    parse_planarize,
    register_geometry,
)
from process_models.v2.registry import ProcessRegistry
from process_models.v2.state import LevelSetState, MaterialState, ProcessState
from process_models.v2.state_backend import HandleStateBackend
from process_models.v2.storage import StateStore
from worker.process_models_v2 import compare_domains, run_v2_step

ROOT = Path(__file__).resolve().parents[1]
GRID = 0.05


def _domain(dimension: int) -> Any:
    ps.setDimension(dimension)
    ps.setNumThreads(1)
    ps.Length.setUnit("um")
    ps.Time.setUnit("s")
    if dimension == 2:
        return ps.Domain(gridDelta=GRID, xExtent=1.0, boundary=ps.BoundaryType.REFLECTIVE_BOUNDARY)
    return ps.Domain(GRID, 0.4, 0.4, boundary=ps.BoundaryType.REFLECTIVE_BOUNDARY)


def _plane(dimension: int, *, layered: bool = False) -> Any:
    domain = _domain(dimension)
    ps.MakePlane(domain, height=0.0, material=ps.Material.Si, addToExisting=False).apply()
    if layered:
        ps.MakePlane(domain, height=0.2, material=ps.Material.SiO2, addToExisting=True).apply()
    return domain


def _trench(dimension: int, *, mask_height: float = 0.0) -> Any:
    domain = _domain(dimension)
    width = 0.4 if dimension == 2 else 0.2
    ps.MakeTrench(
        domain,
        trenchWidth=width,
        trenchDepth=0.2,
        maskHeight=mask_height,
    ).apply()
    return domain


def _project(dimension: int, *, kind: str = "plane", mask_height: float = 0.0) -> dict[str, Any]:
    domain = {
        "gridDelta": GRID,
        "xExtent": 1.0 if dimension == 2 else 0.4,
        "xBoundary": "reflective",
    }
    if dimension == 3:
        domain.update({"yExtent": 0.4, "boundaryXY": "reflective"})
    geometry = (
        {"kind": "plane", "height": 0.0, "substrateMaterial": "Si"}
        if kind == "plane"
        else {
            "kind": "trench",
            "width": 0.4 if dimension == 2 else 0.2,
            "depth": 0.2,
            "taperAngle": 0.0,
            "maskHeight": mask_height,
            "maskTaperAngle": 0.0,
            "substrateMaterial": "Si",
            "maskMaterial": "Mask",
        }
    )
    return {
        "schemaVersion": 7,
        "dimension": dimension,
        "domain": domain,
        "geometry": geometry,
        "solver": {"timeStepRatio": 0.25, "advection": {}},
    }


def _material_order(domain: Any) -> tuple[int, ...]:
    mapping = domain.getMaterialMap()
    return tuple(int(mapping.getMaterialIdAtIdx(index)) for index in range(mapping.size()))


def _state(domain: Any, dimension: int) -> ProcessState:
    order = _material_order(domain)
    names = {
        int(ps.Material.Si): "Si",
        int(ps.Material.SiO2): "SiO2",
        int(ps.Material.Mask): "Mask",
    }
    return ProcessState(
        geometry=LevelSetState(handle=domain, dimension=dimension, grid_delta=GRID),
        materials=MaterialState(order=order, names={key: value for key, value in names.items() if key in order}),
    )


def _provider() -> StaticEngineProvider:
    return StaticEngineProvider(
        [
            StaticEngine(
                capabilities=EngineCapabilities(
                    engine_id="viennaps",
                    version=str(ps.__version__),
                    kind="native",
                    backends=("cpu",),
                    kernels=("CPU_DISK",),
                ),
                handle=ps,
                fingerprint="geometry-test",
                state_backend=HandleStateBackend(
                    ps,
                    id="viennaps",
                    version=str(ps.__version__),
                ),
            )
        ]
    )


class GeometryEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.work_dir = ROOT / "var" / f"test-geometry-{self._testMethodName}"
        shutil.rmtree(self.work_dir, ignore_errors=True)
        self.work_dir.mkdir(parents=True)
        self.registry = ProcessRegistry()
        register_geometry(self.registry)

    def tearDown(self) -> None:
        shutil.rmtree(self.work_dir, ignore_errors=True)

    def _run_v2(self, raw: dict[str, Any], base: Any, dimension: int, project: dict[str, Any]) -> Any:
        spec, params = self.registry.resolve_step(project, raw)
        return (
            FlowExecutor(self.registry, _provider(), StateStore(self.work_dir / raw["id"]))
            .run(
                _state(ps.Domain(base), dimension),
                [
                    FlowStep(
                        step_id=raw["id"],
                        process_id=spec.id,
                        variant_id="native",
                        params=params,
                        context=ProcessContext(
                            dimension=dimension,
                            grid_delta=GRID,
                            project=project,
                            step=raw,
                        ),
                    )
                ],
            )
            .outcomes[-1]
        )

    @staticmethod
    def _run_v1(raw: dict[str, Any], base: Any, project: dict[str, Any]) -> Any:
        from worker import simulate

        domain = ps.Domain(base)
        simulate.apply_geometry_operation(ps, domain, project, raw, run_dir=ROOT / "var")
        return domain

    def test_planarize_cutoff_matches_the_worker_reference(self) -> None:
        for dimension in (2, 3):
            with self.subTest(dimension=dimension):
                project = _project(dimension, kind="trench")
                base = _trench(dimension)
                raw = {
                    "id": "planarize_step_01",
                    "name": "cmp",
                    "enabled": True,
                    "model": "planarize",
                    "cutoffHeight": 0.05,
                }
                reference = self._run_v1(raw, base, project)
                outcome = self._run_v2(raw, base, dimension, project)
                comparison = compare_domains(reference, outcome.state.geometry.handle, dimension)
                self.assertTrue(comparison["matched"], comparison["reasons"])
                self.assertEqual(outcome.plan.identity()["process_id"], "planarize")

    def test_particle_shape_matches_the_worker_reference(self) -> None:
        cases = (
            (
                2,
                "plane",
                {
                    "id": "shape_step_01",
                    "name": "shape",
                    "enabled": True,
                    "model": "particleShape",
                    "operation": "deposit",
                    "shape": "box",
                    "halfAxes": [0.2, 0.1],
                },
                0.0,
            ),
            (
                3,
                "plane",
                {
                    "id": "shape_step_02",
                    "name": "shape",
                    "enabled": True,
                    "model": "particleShape",
                    "operation": "deposit",
                    "shape": "sphere",
                    "radius": 0.1,
                },
                0.0,
            ),
            (
                2,
                "trench",
                {
                    "id": "shape_step_03",
                    "name": "shape",
                    "enabled": True,
                    "model": "particleShape",
                    "operation": "etch",
                    "shape": "box",
                    "halfAxes": [0.1, 0.1],
                },
                0.1,
            ),
        )
        for dimension, kind, raw, mask_height in cases:
            with self.subTest(dimension=dimension, shape=raw["shape"], operation=raw["operation"]):
                project = _project(dimension, kind=kind, mask_height=mask_height)
                base = _plane(dimension) if kind == "plane" else _trench(dimension, mask_height=mask_height)
                reference = self._run_v1(raw, base, project)
                outcome = self._run_v2(raw, base, dimension, project)
                comparison = compare_domains(reference, outcome.state.geometry.handle, dimension)
                self.assertTrue(comparison["matched"], comparison["reasons"])

    def test_boolean_box_matches_the_worker_reference(self) -> None:
        for dimension in (2, 3):
            with self.subTest(dimension=dimension):
                project = _project(dimension)
                base = _plane(dimension)
                raw = {
                    "id": "boolean_step_01",
                    "name": "boolean",
                    "enabled": True,
                    "model": "boolean",
                    "booleanOperation": "difference",
                    "position": [0.0, 0.0, 0.0],
                    "width": 0.4,
                    "height": 0.3,
                    "angle": 0.0,
                    "applyToAll": True,
                }
                if dimension == 3:
                    raw["depth"] = 0.4
                reference = self._run_v1(raw, base, project)
                outcome = self._run_v2(raw, base, dimension, project)
                comparison = compare_domains(reference, outcome.state.geometry.handle, dimension)
                self.assertTrue(comparison["matched"], comparison["reasons"])

    def test_remove_material_updates_material_state(self) -> None:
        for dimension in (2, 3):
            with self.subTest(dimension=dimension):
                project = _project(dimension)
                base = _plane(dimension, layered=True)
                raw = {
                    "id": "remove_step_01",
                    "name": "remove",
                    "enabled": True,
                    "model": "removeMaterial",
                    "material": "SiO2",
                }
                reference = self._run_v1(raw, base, project)
                outcome = self._run_v2(raw, base, dimension, project)
                comparison = compare_domains(reference, outcome.state.geometry.handle, dimension)
                self.assertTrue(comparison["matched"], comparison["reasons"])
                self.assertNotIn(int(ps.Material.SiO2), outcome.state.materials.order)

    def test_boolean_cylinder_runs_in_three_dimensions(self) -> None:
        project = _project(3)
        base = _plane(3)
        raw = {
            "id": "cylinder_step_01",
            "name": "cylinder",
            "enabled": True,
            "model": "boolean",
            "booleanOperation": "difference",
            "position": [0.0, 0.0, 0.0],
            "width": 0.3,
            "height": 0.3,
            "angle": 0.0,
            "applyToAll": True,
            "shape": "cylinder",
            "radius": 0.1,
        }
        outcome = self._run_v2(raw, base, 3, project)
        self.assertEqual(outcome.metadata["operand"], "cylinder")
        self.assertIn("RELATIVE_COMPLEMENT", outcome.metadata["nativeBooleanEnum"])

    def test_masked_deposition_is_refused_before_native(self) -> None:
        project = _project(2, kind="trench", mask_height=0.1)
        base = _trench(2, mask_height=0.1)
        raw = {
            "id": "shape_step_04",
            "name": "masked deposit",
            "enabled": True,
            "model": "particleShape",
            "operation": "deposit",
            "shape": "box",
            "halfAxes": [0.1, 0.1],
        }
        with self.assertRaises(Exception) as caught:
            self._run_v2(raw, base, 2, project)
        self.assertIn("masked particleShape deposition", str(caught.exception))

    def test_planarize_stop_material_is_declined(self) -> None:
        raw = {
            "id": "planarize_step_stop",
            "model": "planarize",
            "stopMaterial": "SiO2",
            "overPolish": 0.01,
        }
        self.assertIsNone(parse_planarize(_project(2), raw))

    def test_geometry_validation_rejects_dimension_mismatches(self) -> None:
        engine = GeometryEngine()
        context2 = ProcessContext(dimension=2, grid_delta=GRID)
        context3 = ProcessContext(dimension=3, grid_delta=GRID)
        box3_params = ParticleShapeParams(operation="deposit", shape="box", halfAxes=[0.1, 0.1, 0.1])
        issues = engine.validate("particleShape", context2, box3_params)
        self.assertTrue(any(issue.path == "halfAxes" for issue in issues))

        depth_in_2d = BooleanParams(
            booleanOperation="difference",
            position=[0.0, 0.0, 0.0],
            width=0.2,
            height=0.2,
            depth=0.2,
        )
        issues = engine.validate("boolean", context2, depth_in_2d)
        self.assertTrue(any(issue.path == "depth" for issue in issues))

        missing_depth = BooleanParams(
            booleanOperation="difference",
            position=[0.0, 0.0, 0.0],
            width=0.2,
            height=0.2,
        )
        issues = engine.validate("boolean", context3, missing_depth)
        self.assertTrue(any(issue.path == "depth" for issue in issues))

        cylinder_2d = BooleanParams(
            booleanOperation="difference",
            position=[0.0, 0.0, 0.0],
            width=0.2,
            height=0.2,
            shape="cylinder",
            radius=0.1,
        )
        issues = engine.validate("boolean", context2, cylinder_2d)
        self.assertTrue(any(issue.path == "shape" for issue in issues))

    def test_planarize_parameter_model_is_finite(self) -> None:
        with self.assertRaises(ValueError):
            PlanarizeParams(cutoffHeight=float("inf"))

    def test_worker_bridge_runs_a_geometry_step(self) -> None:
        project = _project(2, kind="trench")
        raw = {
            "id": "planarize_bridge_01",
            "name": "cmp",
            "enabled": True,
            "model": "planarize",
            "cutoffHeight": 0.05,
        }
        with tempfile.TemporaryDirectory(dir=ROOT / "var") as temporary:
            context = SimpleNamespace(
                project=project,
                ps=ps,
                run_dir=Path(temporary),
                ps_version=str(ps.__version__),
                native_engine_fingerprint="geometry-bridge-test",
                selected_gpu_device=None,
                cuda_readback=None,
                step_engines={raw["id"]: "not-applicable"},
                run_seed=None,
                config={},
            )
            outcome = run_v2_step(context, raw, _trench(2))
        self.assertEqual(outcome.plan.process.execution_kind, "geometry")
        self.assertEqual(outcome.metadata["geometryOperation"], "planarize")

    def test_unknown_operation_fails_closed(self) -> None:
        engine = GeometryEngine()
        with self.assertRaises(GeometryEngineError):
            engine.validate("etch", ProcessContext(dimension=2, grid_delta=GRID), object())

    def test_registration_is_idempotent_and_one_engine_backs_every_operation(self) -> None:
        first = register_geometry(self.registry)
        second = register_geometry(self.registry)
        self.assertEqual(first, ())
        self.assertEqual(second, ())
        engines = [
            self.registry.get_variant(process_id, "native").variant.variant._geometry
            for process_id in ("planarize", "particleShape", "boolean", "removeMaterial")
        ]
        self.assertTrue(all(engine is engines[0] for engine in engines))

    def test_mixed_process_and_geometry_flow(self) -> None:
        register_isotropic(self.registry)
        project = _project(2, kind="trench")
        project["solver"] = {
            "timeStepRatio": 0.25,
            "advection": {
                "spatialScheme": "ENGQUIST_OSHER_1ST_ORDER",
                "temporalScheme": "FORWARD_EULER",
                "adaptiveTimeStepping": False,
                "adaptiveTimeStepSubdivisions": 20,
                "calculateIntermediateVelocities": False,
                "checkDissipation": True,
                "dissipationAlpha": 1.0,
                "ignoreVoids": False,
                "velocityOutput": False,
            },
        }
        etch = {
            "id": "etch_step_0001",
            "name": "etch",
            "enabled": True,
            "operation": "etch",
            "model": "isotropic",
            "rateMagnitude": 0.1,
            "duration": 0.1,
            "rateMode": "uniform",
        }
        cmp = {
            "id": "planarize_step_02",
            "name": "cmp",
            "enabled": True,
            "model": "planarize",
            "cutoffHeight": 0.05,
        }
        specs = [self.registry.resolve_step(project, raw) for raw in (etch, cmp)]
        steps = [
            FlowStep(
                step_id=raw["id"],
                process_id=spec.id,
                variant_id="native",
                params=params,
                context=ProcessContext(dimension=2, grid_delta=GRID, project=project, step=raw),
            )
            for raw, (spec, params) in zip((etch, cmp), specs)
        ]
        run = FlowExecutor(
            self.registry,
            _provider(),
            StateStore(self.work_dir / "mixed"),
        ).run(_state(_trench(2), 2), steps)
        self.assertEqual([outcome.plan.process.id for outcome in run.outcomes], ["isotropic", "planarize"])
        self.assertEqual(run.outcomes[1].plan.process.execution_kind, "geometry")
        self.assertTrue((self.work_dir / "mixed" / "steps" / cmp["id"] / "geometry.vpsd").exists())


if __name__ == "__main__":
    unittest.main()
