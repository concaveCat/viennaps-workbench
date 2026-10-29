"""Compatibility and admission checks for the isolated native GPU candidate."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.hashing import execution_hash
from backend.models import ReplayRequirements
from backend.native_build import fingerprint_package
from backend.validation import select_engine, validate_project

ROOT = Path(__file__).resolve().parents[1]


class NativeMultiIonPolicyTests(unittest.TestCase):
    def test_historical_split_route_profiles_are_resolved_and_not_gpu_admission_gates(self):
        matrix = json.loads((ROOT / "docs/stage9-evidence/gpu-capability-matrix.json").read_text())
        self.assertEqual(matrix["knownGpuProfileFailures"], [])
        failures = matrix["historicalSplitRouteGpuProfileDiagnostics"]
        self.assertEqual(len(failures), 6)
        self.assertEqual(matrix["resolvedGpuMultiIonSupport"]["resolvedSolverProfileRechecks"]["count"], 6)
        self.assertTrue((ROOT / matrix["resolvedGpuMultiIonSupport"]["resolvedSolverProfileRechecks"]["browserEvidence"]).is_file())
        for failure in failures:
            project = json.loads((ROOT / failure["evidence"]).parent.joinpath("project.json").read_text())
            project["solver"].update(failure["solverProfile"])
            project["solver"]["enginePreference"] = "gpu"
            with self.subTest(profile=failure["solverProfile"], dimension=project["dimension"]):
                self.assertEqual(select_engine(project, project["steps"][0]), "GPU_DISK")
                validate_project(project, kind="simulate")

    def test_old_replay_shape_and_new_engine_identity(self):
        old = {"viennapsVersion": "4.6.2", "viennalsVersion": "5.8.5", "cpuThreads": 1,
               "steps": [{"stepId": "particle-step", "engine": "GPU_DISK", "seed": 123,
                          "executionStrategy": "symmetric-operator-splitting-v1"}]}
        self.assertEqual(ReplayRequirements.model_validate(old).model_dump(mode="json", exclude_none=True), old)
        new = {**old, "nativeEngineFingerprint": "a" * 64,
               "steps": [{**old["steps"][0], "executionStrategy": "native-multi-ion-v1"}]}
        self.assertEqual(ReplayRequirements.model_validate(new).model_dump(mode="json", exclude_none=True), new)
        args = dict(run_seed=123, cpu_threads=1, step_engines=old["steps"],
                    viennaps_version="4.6.2", viennals_version="5.8.5")
        legacy_hash = execution_hash("b" * 64, **args)
        self.assertEqual(legacy_hash, execution_hash("b" * 64, native_engine_fingerprint=None, **args))
        self.assertNotEqual(legacy_hash, execution_hash("b" * 64, native_engine_fingerprint="a" * 64, **args))

    def test_engine_fingerprint_tracks_extension_gpu_library_and_kernels(self):
        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp) / "viennaps"
            (package / "ptx").mkdir(parents=True)
            (package / "__init__.py").write_text("# isolated package\n")
            libs = Path(tmp) / "viennaps.libs"
            libs.mkdir()
            embree = libs / "libembree4.so"
            embree.write_bytes(b"embree")
            (libs / "libtbb.so").write_bytes(b"tbb")
            (package / "_core.test.so").write_bytes(b"extension")
            (package / "libViennaLS_GPU.so").write_bytes(b"gpu-abi")
            kernel = package / "ptx" / "ViennaPSCallableWrapper.optixir"
            kernel.write_bytes(b"kernel")
            first = fingerprint_package(package)
            self.assertEqual(first, fingerprint_package(package))
            kernel.write_bytes(b"changed kernel")
            self.assertNotEqual(first, fingerprint_package(package))
            kernel.write_bytes(b"kernel")
            embree.write_bytes(b"changed embree")
            self.assertNotEqual(first, fingerprint_package(package))


if __name__ == "__main__":
    unittest.main()
