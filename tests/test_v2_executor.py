"""Tests for the v2 multi-step flow executor.

Every test builds a small real 2D ViennaPS plane, loads the discovered
isotropic plugin through :func:`load_registry`, and drives it through a
:class:`FlowExecutor` with a :class:`StateStore` rooted in a temporary
directory. No server, worker, or workbench data directory is involved.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

import numpy as np
import viennaps as ps

from plugins.isotropic import IsotropicParams
from process_models.plugins import load_registry
from process_models.v2.contract import ProcessContext
from process_models.v2.engine import EngineCapabilities, StaticEngine, StaticEngineProvider
from process_models.v2.executor import FlowExecutor, FlowRun, FlowStep
from process_models.v2.resolver import ExecutionResolutionError
from process_models.v2.state import (
    CellSetState,
    Field,
    GridSpec,
    Layer,
    LayerState,
    LevelSetState,
    MaterialState,
    OutputSet,
    ProcessState,
)
from process_models.v2.storage import StateStore

GRID_DELTA = 0.2
X_EXTENT = 4.0
FINGERPRINT = "test-flow-executor-fingerprint"
VIENNAPS_VERSION = str(ps.version)
SI = int(ps.Material.Si)
SIO2 = int(ps.Material.SiO2)


def _plane() -> Any:
    """Return a fresh 2D reflective plane of Si at height 0."""
    ps.setDimension(2)
    domain = ps.Domain(
        gridDelta=GRID_DELTA,
        xExtent=X_EXTENT,
        boundary=ps.BoundaryType.REFLECTIVE_BOUNDARY,
    )
    ps.MakePlane(domain, height=0.0, material=ps.Material.Si, addToExisting=False).apply()
    return domain


def _material_order(domain: Any) -> tuple[int, ...]:
    mapping = domain.getMaterialMap()
    return tuple(int(mapping.getMaterialIdAtIdx(index)) for index in range(mapping.size()))


def _top(domain: Any) -> float:
    """Return the highest surface-mesh y coordinate of a 2D domain."""
    mesh = domain.getSurfaceMesh(addInterfaces=True, minNodeDistanceFactor=0.01)
    points = [[float(value) for value in node] for node in mesh.getNodes()]
    if not points:
        raise AssertionError("domain has no surface nodes")
    return float(np.max(np.asarray(points, dtype=float)[:, 1]))


def _read_geometry(path: Path) -> Any:
    """Native replay loader: a fresh domain plus ViennaPS ``Reader``."""
    domain = ps.Domain(
        gridDelta=GRID_DELTA,
        xExtent=X_EXTENT,
        boundary=ps.BoundaryType.REFLECTIVE_BOUNDARY,
    )
    ps.Reader(domain, str(path)).apply()
    return domain


def _layers() -> LayerState:
    return LayerState(
        (
            Layer(
                layer_id="substrate",
                material_id=SI,
                name="Si",
                source_step="initial",
                thickness=0.1,
                metadata={"role": "substrate"},
            ),
        )
    )


def _fields() -> CellSetState:
    grid = GridSpec(dimension=2, origin=(-1.0, -1.0), spacing=0.1, shape=(4, 5))
    values = np.arange(20, dtype=np.float64).reshape(4, 5) / 20.0
    return CellSetState(
        grid=grid,
        material_ids=np.full((4, 5), SI, dtype=np.int32),
        fields={
            "P_total": Field(
                name="P_total",
                unit="1/cm3",
                association="cell",
                dtype="float64",
                shape=(4, 5),
                data=values,
            )
        },
        provenance={"producer": "test-v2-executor"},
    )


def _state(domain: Any) -> ProcessState:
    order = _material_order(domain)
    return ProcessState(
        geometry=LevelSetState(
            handle=domain,
            dimension=2,
            grid_delta=GRID_DELTA,
            material_order=order,
        ),
        materials=MaterialState(order=order, names={SI: "Si"}),
        layers=_layers(),
        fields=_fields(),
    )


def _context() -> ProcessContext:
    return ProcessContext(
        dimension=2,
        grid_delta=GRID_DELTA,
        project={
            "schemaVersion": 3,
            "dimension": 2,
            "geometry": {"kind": "plane", "substrateMaterial": "Si"},
            "solver": {"timeStepRatio": 0.25},
        },
    )


def _provider(handle: Any = ps) -> StaticEngineProvider:
    capabilities = EngineCapabilities(
        engine_id="viennaps",
        version=VIENNAPS_VERSION,
        kind="native",
        backends=("cpu",),
    )
    return StaticEngineProvider([StaticEngine(capabilities=capabilities, handle=handle, fingerprint=FINGERPRINT)])


def _deposit_step() -> FlowStep:
    return FlowStep(
        step_id="deposit",
        process_id="isotropic",
        variant_id="native",
        params=IsotropicParams(
            operation="deposit",
            rateMagnitude=0.3,
            duration=1.0,
            depositMaterial="SiO2",
        ),
        context=_context(),
    )


def _etch_step() -> FlowStep:
    return FlowStep(
        step_id="etch",
        process_id="isotropic",
        variant_id="native",
        params=IsotropicParams(operation="etch", rateMagnitude=0.2, duration=1.0),
        context=_context(),
    )


class _HandleProxy:
    """Module proxy that hides one ViennaPS attribute (``None`` on getattr)."""

    def __init__(self, module: Any, hidden: str) -> None:
        self._module = module
        self._hidden = hidden

    def __getattr__(self, name: str) -> Any:
        if name == self._hidden:
            raise AttributeError(f"{self._hidden!r} is hidden by the test proxy")
        return getattr(self._module, name)


class FlowExecutorTest(unittest.TestCase):
    def setUp(self) -> None:
        ps.setNumThreads(1)
        ps.setDimension(2)
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name) / "run"
        self.store = StateStore(self.root)
        self.registry = load_registry()
        self.provider = _provider()
        self.executor = FlowExecutor(self.registry, self.provider, self.store)

    def _run_two_step_flow(self) -> tuple[Any, ProcessState, FlowRun]:
        domain = _plane()
        state = _state(domain)
        run = self.executor.run(state, [_deposit_step(), _etch_step()])
        return domain, state, run

    # -- execution and persistence ----------------------------------------

    def test_two_step_flow_executes_threads_and_persists(self) -> None:
        domain = _plane()
        initial_state = _state(domain)
        initial_top = _top(domain)

        run = self.executor.run(initial_state, [_deposit_step(), _etch_step()])

        self.assertIsInstance(run, FlowRun)
        self.assertEqual(run.run_dir, self.root)
        self.assertEqual(len(run.outcomes), 2)
        self.assertIs(run.state, run.outcomes[-1].state)

        # The initial native geometry was checkpointed, never executed in place.
        self.assertIs(initial_state.geometry.handle, domain)
        self.assertAlmostEqual(_top(domain), initial_top, delta=1e-12)
        self.assertEqual(domain.getNumberOfLevelSets(), 1)

        first, second = run.outcomes
        self.assertIsNot(first.state.geometry.handle, domain)
        self.assertIsNot(second.state.geometry.handle, domain)
        self.assertIsNot(first.plan.request.state, initial_state)
        self.assertIsNot(second.plan.request.state, first.state)

        # deposit +0.3 then uniform etch -0.2 leaves the surface at +0.1.
        self.assertAlmostEqual(_top(run.state.geometry.handle) - initial_top, 0.1, delta=1e-6)
        self.assertEqual(run.state.geometry.material_order, (SI, SIO2))
        self.assertEqual(run.state.materials.order, (SI, SIO2))

        # Layers and fields survive across steps even though isotropic only writes geometry.
        self.assertIsNotNone(run.state.layers)
        self.assertIsNotNone(run.state.fields)

        self._assert_step_files("deposit")
        self._assert_step_files("etch")

        flow_path = self.root / "flow.json"
        self.assertTrue(flow_path.is_file())
        flow = json.loads(flow_path.read_text(encoding="utf-8"))
        self.assertEqual(flow["schemaVersion"], 1)
        self.assertEqual(flow["runDir"], str(self.root))
        self.assertEqual(flow["steps"], [outcome.plan.identity() for outcome in run.outcomes])
        self.assertEqual(flow["versions"], {"viennaps": VIENNAPS_VERSION})

    def _assert_step_files(self, step_id: str) -> None:
        directory = self.store.step_dir(step_id)
        for name in ("state.json", "outputs.json", "plan.json", "geometry.vpsd", "fields.npz"):
            with self.subTest(step=step_id, file=name):
                path = directory / name
                self.assertTrue(path.is_file(), f"{path} is missing")
                self.assertGreater(path.stat().st_size, 0, f"{path} is empty")

        payload = json.loads((directory / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["schemaVersion"], 1)
        self.assertEqual(payload["stepId"], step_id)
        self.assertEqual(payload["geometry"]["ref"], "geometry.vpsd")
        self.assertEqual(payload["geometry"]["dimension"], 2)
        self.assertEqual(payload["geometry"]["gridDelta"], GRID_DELTA)

        plan = json.loads((directory / "plan.json").read_text(encoding="utf-8"))
        self.assertEqual(plan["process_id"], "isotropic")
        self.assertEqual(plan["variant_id"], "native")
        self.assertEqual(plan["engine_id"], "viennaps")
        self.assertEqual(plan["engine_version"], VIENNAPS_VERSION)
        self.assertEqual(plan["engine_fingerprint"], FINGERPRINT)

    def test_state_json_metadata_and_outputs_round_trip(self) -> None:
        _domain, _state_input, run = self._run_two_step_flow()

        payload = json.loads((self.store.step_dir("etch") / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["materials"]["order"], [SI, SIO2])
        self.assertEqual(payload["materials"]["names"], {str(SI): "Si"})
        self.assertEqual(payload["geometry"]["materialOrder"], [SI, SIO2])
        self.assertEqual(
            payload["layers"],
            {
                "layers": [
                    {
                        "layerId": "substrate",
                        "materialId": SI,
                        "name": "Si",
                        "sourceStep": "initial",
                        "thickness": 0.1,
                        "metadata": {"role": "substrate"},
                    }
                ]
            },
        )
        self.assertEqual(
            payload["fields"]["grid"],
            {"dimension": 2, "origin": [-1.0, -1.0], "spacing": 0.1, "shape": [4, 5]},
        )
        self.assertEqual(
            payload["fields"]["fields"]["P_total"],
            {"unit": "1/cm3", "association": "cell", "dtype": "float64", "shape": [4, 5], "components": 1},
        )
        self.assertEqual(payload["fields"]["materialIds"], {"dtype": "int32", "shape": [4, 5]})

        self.assertEqual(self.store.load_outputs("deposit"), OutputSet())
        self.assertEqual(self.store.load_outputs("etch"), OutputSet())
        self.assertTrue(run.outcomes[0].outputs == OutputSet())

    def test_load_state_with_geometry_loader_restores_the_saved_state(self) -> None:
        _domain, _state_input, run = self._run_two_step_flow()
        final = run.state

        loaded = self.store.load_state(
            "etch",
            geometry_loader=lambda reference: _read_geometry(self.store.step_dir("etch") / reference),
        )

        self.assertIsNotNone(loaded.geometry)
        self.assertEqual(loaded.geometry.dimension, 2)
        self.assertEqual(loaded.geometry.grid_delta, GRID_DELTA)
        self.assertEqual(loaded.geometry.material_order, final.geometry.material_order)
        self.assertAlmostEqual(_top(loaded.geometry.handle), _top(final.geometry.handle), delta=1e-6)

        self.assertEqual(loaded.materials, final.materials)
        self.assertEqual(loaded.layers, final.layers)
        self.assertIsNotNone(loaded.fields)
        self.assertEqual(loaded.fields.grid, final.fields.grid)
        self.assertEqual(
            set(loaded.fields.fields),
            set(final.fields.fields),
        )
        np.testing.assert_array_equal(
            loaded.fields.fields["P_total"].data,
            final.fields.fields["P_total"].data,
        )
        np.testing.assert_array_equal(loaded.fields.material_ids, final.fields.material_ids)
        self.assertEqual(loaded.fields.provenance, final.fields.provenance)

    def test_second_run_continues_from_the_final_state(self) -> None:
        _domain, _state_input, first_run = self._run_two_step_flow()
        final = first_run.state
        final_top = _top(final.geometry.handle)

        second_root = Path(self._temporary.name) / "second-run"
        second_store = StateStore(second_root)
        second_executor = FlowExecutor(self.registry, self.provider, second_store)
        second_run = second_executor.run(final, [_etch_step()])

        self.assertEqual(second_run.run_dir, second_root)
        self.assertEqual(len(second_run.outcomes), 1)
        self.assertIsNot(second_run.state, final)
        self.assertIsNot(second_run.outcomes[0].plan.request.state, final)

        # The first run's final geometry is checkpointed, not mutated.
        self.assertAlmostEqual(_top(final.geometry.handle), final_top, delta=1e-12)
        self.assertAlmostEqual(_top(second_run.state.geometry.handle), final_top - 0.2, delta=1e-6)
        self.assertEqual(second_run.state.materials.order, (SI, SIO2))

        flow = json.loads((second_root / "flow.json").read_text(encoding="utf-8"))
        self.assertEqual(flow["steps"], [second_run.outcomes[0].plan.identity()])
        self.assertEqual(flow["versions"], {"viennaps": VIENNAPS_VERSION})
        self.assertTrue((second_store.step_dir("etch") / "state.json").is_file())

    # -- engines without checkpoint/writer support ------------------------

    def test_engine_without_domain_cannot_checkpoint(self) -> None:
        root = Path(self._temporary.name) / "no-domain"
        store = StateStore(root)
        executor = FlowExecutor(self.registry, _provider(_HandleProxy(ps, "Domain")), store)

        with self.assertRaises(ExecutionResolutionError) as raised:
            executor.run(_state(_plane()), [_deposit_step()])
        message = str(raised.exception)
        self.assertIn("mutates_input", message)
        self.assertIn("Domain", message)
        self.assertFalse((root / "flow.json").exists())
        self.assertFalse(store.step_dir("deposit").exists())

    def test_engine_without_writer_records_no_geometry_ref(self) -> None:
        executor = FlowExecutor(self.registry, _provider(_HandleProxy(ps, "Writer")), self.store)
        run = executor.run(_state(_plane()), [_etch_step()])

        directory = self.store.step_dir("etch")
        self.assertFalse((directory / "geometry.vpsd").exists())
        payload = json.loads((directory / "state.json").read_text(encoding="utf-8"))
        self.assertIsNone(payload["geometry"]["ref"])
        self.assertIsNotNone(run.state.geometry)


if __name__ == "__main__":
    unittest.main()
