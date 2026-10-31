"""Compatibility and admission checks for the isolated native GPU candidate."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.hashing import execution_hash
from backend.models import ReplayRequirements
from backend.native_build import fingerprint_package
from backend.validation import ProjectInputError, select_engine, validate_project

ROOT = Path(__file__).resolve().parents[1]
EIGHT_ION_PROJECT = (ROOT / "docs/stage9-evidence/gpu-coverage-audit/multiparticle-species-probe"
                     / "operator-split/eight-ions-2d-128rays.project.json")


class MultiIonGpuRuntimePolicyTests(unittest.TestCase):
    """Stock 4.7.0 GPU engines keep one ion flux field and drop the rest."""

    def project(self, ions: int, preference: str = "gpu") -> dict:
        project = json.loads(EIGHT_ION_PROJECT.read_text(encoding="utf-8"))
        step = project["steps"][0]
        step["species"] = step["species"][:ions]
        labels = [row["label"] for row in step["species"]]
        step["rateCoefficients"] = {"defaultBySpecies": {name: 0.025 for name in labels}}
        project["solver"]["enginePreference"] = preference
        return project

    def test_stock_47_rejects_multi_ion_gpu_with_an_actionable_policy(self):
        with patch("backend.engine.engine_version", return_value="4.7.0"):
            with self.assertRaises(ProjectInputError) as context:
                validate_project(self.project(2), kind="simulate")
            messages = " ".join(issue.message for issue in context.exception.issues)
            self.assertIn("silently drop every ion after the first", messages)
            self.assertIn("4.6.2 patched runtime", messages)
            # One ion keeps working, and CPU_DISK is the in-version route.
            validate_project(self.project(1), kind="simulate")
            cpu = validate_project(self.project(2, preference="cpu"), kind="simulate")[0]
            self.assertEqual(select_engine(cpu.model_dump(mode="json"),
                                           cpu.model_dump(mode="json")["steps"][0]), "CPU_DISK")

    def test_patched_462_runtime_keeps_accepting_multi_ion_gpu(self):
        with patch("backend.engine.engine_version", return_value="4.6.2"):
            parsed = validate_project(self.project(2), kind="simulate")[0]
        normalized = parsed.model_dump(mode="json")
        self.assertEqual(select_engine(normalized, normalized["steps"][0]), "GPU_DISK")


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
                # These profiles are 4.6.2 measurements; the multi-ion GPU
                # policy is version-qualified, so pin the recorded runtime.
                with patch("backend.engine.engine_version", return_value="4.6.2"):
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
