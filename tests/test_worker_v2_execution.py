"""Opt-in worker execution path: v2 state bridge and claimed-step cutover."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import viennals
import viennaps as ps

from tests.test_worker_v2_isotropic import TEST_ROOT, plane_domain, trench_domain
from worker.steps.cellset import run_cell_set_step
from worker.steps.context import RunContext
from worker.steps.geometry import run_geometry_step
from worker.steps.process import run_process_step
from worker.v2_state import WorkerV2State


def _project(geometry: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schemaVersion": 5,
        "name": "v2 execution",
        "dimension": 2,
        "units": {"length": "um", "time": "s", "rate": "um/s", "angle": "deg"},
        "domain": {"gridDelta": 0.2, "xExtent": 4.0, "xBoundary": "reflective"},
        "geometry": geometry or {"kind": "plane", "height": 0.0, "substrateMaterial": "Si"},
        "steps": [],
        "solver": {
            "timeStepRatio": 0.25,
            "raysPerPoint": 32,
            "cpuThreads": 1,
            "random": {"mode": "fixed", "seed": 314159},
            "enginePreference": "cpu",
            "timeoutSeconds": 30,
        },
        "view": {"panX": 0.0, "panH": 0.0, "zoom": 1.0, "showGrid": True},
    }


def _isotropic() -> dict[str, Any]:
    return {
        "id": "isotropic_step_01",
        "name": "etch",
        "enabled": True,
        "operation": "etch",
        "model": "isotropic",
        "rateMagnitude": 0.3,
        "duration": 1.0,
        "rateMode": "uniform",
    }


def _planarize() -> dict[str, Any]:
    return {
        "id": "planarize_step_01",
        "name": "cmp",
        "enabled": True,
        "model": "planarize",
        "cutoffHeight": 0.1,
    }


def _events(run_dir: Path, event_type: str) -> list[dict[str, Any]]:
    path = run_dir / "events.ndjson"
    if not path.is_file():
        return []
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [record for record in records if record.get("type") == event_type]


@contextlib.contextmanager
def _execution_flag():
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("VIENNAPS_WORKBENCH_V2_PLUGIN_SHADOW", None)
        os.environ.pop("VIENNAPS_WORKBENCH_V2_PLUGIN_APPLY", None)
        os.environ["VIENNAPS_WORKBENCH_V2_EXECUTION"] = "1"
        yield


class WorkerV2ExecutionTest(unittest.TestCase):
    def setUp(self) -> None:
        ps.setNumThreads(1)
        ps.Length.setUnit("um")
        ps.setDimension(2)
        self.work_dir = TEST_ROOT / f"execution-{self._testMethodName}"
        shutil.rmtree(self.work_dir, ignore_errors=True)
        self.work_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.work_dir, ignore_errors=True)

    def _context(self, name: str, project: dict[str, Any], step: dict[str, Any], domain: Any) -> RunContext:
        run_dir = self.work_dir / name
        run_dir.mkdir(parents=True, exist_ok=True)
        return RunContext(
            project=project,
            ps=ps,
            domain=domain,
            run_dir=run_dir,
            run_id=f"run-{name}",
            config={},
            actual_steps=[step],
            step_engines={},
            engine_preference="cpu",
            run_seed=None,
            selected_gpu_device=None,
            cuda_readback=None,
            gpu_sample=None,
            native_engine_fingerprint=None,
            ps_version=str(ps.__version__),
            ls_version=str(viennals.__version__),
            snapshots=[{"stepId": "initial", "materialTops": None}],
            field_state={},
            actual_execution=[],
            intermediate_outputs=[],
        )

    def test_process_step_runs_through_v2_and_commits_state(self) -> None:
        step = _isotropic()
        ctx = self._context("process", _project(), step, plane_domain(2))
        with _execution_flag():
            result = run_process_step(ctx, 1, step)
        self.assertIsNotNone(ctx.v2_state)
        assert ctx.v2_state is not None
        self.assertIs(ctx.v2_state.geometry.handle, ctx.domain)
        self.assertEqual(ctx.v2_state.materials.order, (int(ps.Material.Si),))
        self.assertIn("v2Plan", result.metadata)
        configured = _events(ctx.run_dir, "step-configured")
        self.assertEqual(len(configured), 1, configured)
        self.assertIn("v2Plan", configured[0])

    def test_field_sequence_uses_v2_cell_set_and_persists_at_its_end(self) -> None:
        cell = {
            "height": 0.5,
            "substrateDepth": 0.4,
            "coverMaterial": "GAS",
            "periodic": False,
            "format": "json",
        }
        implant = {
            "id": "implant_step_01",
            "name": "implant",
            "enabled": True,
            "model": "implant",
            "species": "P",
            "material": "Si",
            "energyKeV": 10.0,
            "doseCm2": 1.0e15,
            "tiltDeg": 7.0,
            "rotationDeg": 0.0,
            "doseControl": "WaferDose",
            "profile": {
                "source": "manual",
                "kind": "pearsonIV",
                "head": {"mu": 0.03, "sigma": 0.012, "beta": 1.15, "gamma": 6.2},
                "lateralMu": 0.0,
                "lateralSigma": 0.005,
            },
            "cellSet": cell,
        }
        anneal = {
            "id": "anneal_step_01",
            "name": "anneal",
            "enabled": True,
            "model": "anneal",
            "mode": "GaussSeidel",
            "schedule": {"kind": "isothermal", "temperatureK": 1273.15, "durationSeconds": 1.0},
            "diffusivity": {"kind": "arrhenius", "D0": 1.23e13, "Ea_eV": 3.566},
            "diffusionMaterials": ["Si"],
            "blockingMaterials": [],
            "speciesLabel": "P_total",
            "activeLabel": "P_active",
            "solidActivation": {"solubilityC0": 1.521e2, "solubilityEa_eV": 0.7102},
            "cellSet": cell,
        }
        project = _project()
        ctx = self._context("field", project, implant, plane_domain(2))
        ctx.actual_steps = [implant, anneal]
        with _execution_flag():
            run_cell_set_step(ctx, 1, implant)
            self.assertIsNotNone(ctx.v2_state)
            assert ctx.v2_state is not None
            self.assertIsNotNone(ctx.v2_state.fields)
            first_store = ctx.run_dir / "v2" / implant["id"] / "steps" / implant["id"]
            self.assertFalse((first_store / "fields.npz").exists())
            run_cell_set_step(ctx, 2, anneal)
        self.assertIsNotNone(ctx.v2_state)
        assert ctx.v2_state is not None
        final_fields = ctx.v2_state.fields
        self.assertIsNotNone(final_fields)
        second_store = ctx.run_dir / "v2" / anneal["id"] / "steps" / anneal["id"]
        self.assertTrue((second_store / "fields.npz").exists())
        self.assertIn("P_active", final_fields.fields)

    def test_legacy_step_invalidates_field_history(self) -> None:
        cell = {
            "height": 0.5,
            "substrateDepth": 0.4,
            "coverMaterial": "GAS",
            "periodic": False,
            "format": "json",
        }
        implant = {
            "id": "implant_step_01",
            "name": "implant",
            "enabled": True,
            "model": "implant",
            "species": "P",
            "material": "Si",
            "energyKeV": 10.0,
            "doseCm2": 1.0e15,
            "tiltDeg": 7.0,
            "rotationDeg": 0.0,
            "doseControl": "WaferDose",
            "profile": {
                "source": "manual",
                "kind": "pearsonIV",
                "head": {"mu": 0.03, "sigma": 0.012, "beta": 1.15, "gamma": 6.2},
                "lateralMu": 0.0,
                "lateralSigma": 0.005,
            },
            "cellSet": cell,
        }
        anneal = {
            "id": "anneal_step_01",
            "name": "anneal",
            "enabled": True,
            "model": "anneal",
            "mode": "GaussSeidel",
            "schedule": {"kind": "isothermal", "temperatureK": 1273.15, "durationSeconds": 1.0},
            "diffusivity": {"kind": "arrhenius", "D0": 1.23e13, "Ea_eV": 3.566},
            "diffusionMaterials": ["Si"],
            "blockingMaterials": [],
            "speciesLabel": "P_total",
            "activeLabel": "P_active",
            "solidActivation": {"solubilityC0": 1.521e2, "solubilityEa_eV": 0.7102},
            "cellSet": cell,
        }
        ctx = self._context("invalidate", _project(), implant, plane_domain(2))
        ctx.actual_steps = [implant, anneal]
        with _execution_flag():
            run_cell_set_step(ctx, 1, implant)
            WorkerV2State.invalidate(ctx)
            self.assertTrue(ctx.field_state.get("v2FieldInvalidated"))
            with self.assertRaises(RuntimeError) as caught:
                run_cell_set_step(ctx, 2, anneal)
        self.assertIn("cannot reuse the v2 cell set", str(caught.exception))

    def test_geometry_step_runs_through_v2_and_preserves_state(self) -> None:
        project = _project(
            {
                "kind": "trench",
                "width": 1.6,
                "depth": 1.2,
                "taperAngle": 0.0,
                "maskHeight": 0.6,
                "maskTaperAngle": 0.0,
                "substrateMaterial": "Si",
                "maskMaterial": "Mask",
            }
        )
        step = _planarize()
        ctx = self._context("geometry", project, step, trench_domain())
        with _execution_flag():
            result = run_geometry_step(ctx, 1, step)
        self.assertEqual(result["v2Metadata"]["geometryOperation"], "planarize")
        self.assertIsNotNone(ctx.v2_state)
        assert ctx.v2_state is not None
        self.assertIs(ctx.v2_state.geometry.handle, ctx.domain)

    def test_two_claimed_steps_share_one_state(self) -> None:
        project = _project()
        first = _isotropic()
        second = {
            **first,
            "id": "isotropic_step_02",
            "operation": "deposit",
            "depositMaterial": "SiO2",
        }
        ctx = self._context("two", project, first, plane_domain(2))
        with _execution_flag():
            run_process_step(ctx, 1, first)
            first_handle = ctx.domain
            run_process_step(ctx, 2, second)
        self.assertIsNot(ctx.domain, first_handle)
        self.assertIsNotNone(ctx.v2_state)
        assert ctx.v2_state is not None
        self.assertIs(ctx.v2_state.geometry.handle, ctx.domain)


if __name__ == "__main__":
    unittest.main()
