"""P0 workflow contract: deterministic compile, stable ids, geometry binding."""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from backend.models import Domain, Domain3D, FixedRandom, SolverV7, Units
from backend.workflow import (
    FlowRevisionV1,
    GeometryBindingV1,
    compile_flow,
    stable_step_id,
)


UNITS = Units(length="um", time="s", rate="um/s", angle="deg")


def solver() -> SolverV7:
    return SolverV7(
        timeStepRatio=0.25,
        raysPerPoint=32,
        cpuThreads=1,
        random=FixedRandom(mode="fixed", seed=314159),
        enginePreference="cpu",
        timeoutSeconds=60,
    )


def binding(*, binding_id: str = "bind_trench_01", grid_delta: float = 0.2) -> GeometryBindingV1:
    return GeometryBindingV1(
        schemaVersion=1,
        bindingId=binding_id,
        name="3D trench binding",
        dimension=3,
        units=UNITS,
        domain=Domain3D(
            gridDelta=grid_delta,
            xExtent=4.0,
            yExtent=4.0,
            boundaryXY="reflective",
        ),
        geometry={
            "kind": "trench",
            "width": 1.0,
            "depth": 1.2,
            "taperAngle": 0.0,
            "maskHeight": 0.0,
            "maskTaperAngle": 0.0,
            "substrateMaterial": "Si",
            "maskMaterial": "Mask",
        },
        materialCatalogVersion="4.6.2",
        defaultValuesVersion="1",
    )


def step(
    step_id: str,
    *,
    duration: float = 0.4,
    operation: str = "etch",
    rate: float = 0.2,
) -> dict:
    payload = {
        "id": step_id,
        "name": step_id,
        "enabled": True,
        "operation": operation,
        "model": "isotropic",
        "rateMagnitude": rate,
        "duration": duration,
        "rateMode": "uniform",
    }
    if operation == "deposit":
        payload["depositMaterial"] = "SiO2"
    return payload


def flow(*steps: dict, flow_id: str = "flow_trench_01", revision: int = 1) -> FlowRevisionV1:
    return FlowRevisionV1(
        schemaVersion=1,
        flowId=flow_id,
        revision=revision,
        name="P0 trench flow",
        steps=list(steps) or [step("step_etch_01")],
        solver=solver(),
    )


class WorkflowCompileTests(unittest.TestCase):
    def test_stable_step_id_is_structural_not_content_addressed(self):
        first = stable_step_id(
            flow_id="flow_trench_01",
            module_instance_id_path="root",
            authored_step_id="step_etch_01",
            iteration_key="i0",
        )
        second = stable_step_id(
            flow_id="flow_trench_01",
            module_instance_id_path="root",
            authored_step_id="step_etch_01",
            iteration_key="i0",
        )
        other_iteration = stable_step_id(
            flow_id="flow_trench_01",
            module_instance_id_path="root",
            authored_step_id="step_etch_01",
            iteration_key="i1",
        )
        other_flow = stable_step_id(
            flow_id="flow_trench_02",
            module_instance_id_path="root",
            authored_step_id="step_etch_01",
            iteration_key="i0",
        )
        self.assertEqual(first, second)
        self.assertNotEqual(first, other_iteration)
        self.assertNotEqual(first, other_flow)
        self.assertRegex(first, r"^s_[a-z2-7]{20}$")

    def test_compile_is_deterministic(self):
        first = compile_flow(flow(), binding())
        second = compile_flow(flow(), binding())

        self.assertEqual(first.compileRecord.compileId, second.compileRecord.compileId)
        self.assertEqual(
            first.project.model_dump(mode="json", exclude_none=True),
            second.project.model_dump(mode="json", exclude_none=True),
        )
        self.assertEqual(
            [entry.model_dump(mode="json") for entry in first.sourceMap],
            [entry.model_dump(mode="json") for entry in second.sourceMap],
        )
        self.assertEqual(first.compileRecord.fullProjectHash, second.compileRecord.fullProjectHash)
        self.assertEqual(first.compileRecord.sourceMapHash, second.compileRecord.sourceMapHash)

    def test_parameter_edit_keeps_step_ids_but_changes_project_hash(self):
        before = compile_flow(flow(step("step_etch_01", duration=0.4)), binding())
        after = compile_flow(flow(step("step_etch_01", duration=0.7)), binding())

        self.assertEqual(
            [entry.stepId for entry in before.sourceMap],
            [entry.stepId for entry in after.sourceMap],
        )
        self.assertEqual(
            [entry.authoredStepId for entry in before.sourceMap],
            [entry.authoredStepId for entry in after.sourceMap],
        )
        self.assertNotEqual(
            before.compileRecord.fullProjectHash,
            after.compileRecord.fullProjectHash,
        )

    def test_inserting_a_step_does_not_change_existing_ids(self):
        before = compile_flow(flow(step("step_etch_01"), step("step_dep_01", operation="deposit")), binding())
        after = compile_flow(
            flow(
                step("step_etch_01"),
                step("step_etch_02", duration=0.1),
                step("step_dep_01", operation="deposit"),
            ),
            binding(),
        )

        before_by_authored = {entry.authoredStepId: entry.stepId for entry in before.sourceMap}
        after_by_authored = {entry.authoredStepId: entry.stepId for entry in after.sourceMap}
        self.assertEqual(before_by_authored["step_etch_01"], after_by_authored["step_etch_01"])
        self.assertEqual(before_by_authored["step_dep_01"], after_by_authored["step_dep_01"])
        self.assertNotIn("step_etch_02", before_by_authored)

    def test_geometry_binding_change_changes_binding_and_project_hashes(self):
        first = compile_flow(flow(), binding(grid_delta=0.2))
        second = compile_flow(flow(), binding(grid_delta=0.1))

        self.assertNotEqual(
            first.compileRecord.geometryBindingHash,
            second.compileRecord.geometryBindingHash,
        )
        self.assertNotEqual(
            first.compileRecord.fullProjectHash,
            second.compileRecord.fullProjectHash,
        )
        # Step identity is structural; a geometry change must not renumber it.
        self.assertEqual(
            [entry.stepId for entry in first.sourceMap],
            [entry.stepId for entry in second.sourceMap],
        )

    def test_source_map_records_flow_path_and_iteration(self):
        compiled = compile_flow(flow(step("step_etch_01")), binding())
        entry = compiled.sourceMap[0]
        self.assertEqual(entry.flowPath, "root/step_etch_01")
        self.assertEqual(entry.moduleInstanceIdPath, "root")
        self.assertEqual(entry.iterationKey, "i0")
        self.assertEqual(entry.authoredStepId, "step_etch_01")
        self.assertEqual(entry.parameterProvenance["duration"].kind, "literal")

    def test_2d_binding_compiles_to_project_v6(self):
        binding_2d = GeometryBindingV1(
            schemaVersion=1,
            bindingId="bind_plane_2d_01",
            name="2D plane binding",
            dimension=2,
            units=UNITS,
            domain=Domain(gridDelta=0.2, xExtent=4.0, xBoundary="reflective"),
            geometry={"kind": "plane", "height": 0.0, "substrateMaterial": "Si"},
        )
        compiled = compile_flow(flow(step("step_etch_01")), binding_2d)
        self.assertEqual(compiled.project.schemaVersion, 6)
        self.assertEqual(compiled.project.dimension, 2)

    def test_2d_binding_requires_2d_domain(self):
        with self.assertRaises(ValidationError):
            GeometryBindingV1(
                schemaVersion=1,
                bindingId="bind_bad_2d_01",
                name="bad 2D binding",
                dimension=2,
                units=UNITS,
                domain=Domain3D(
                    gridDelta=0.2,
                    xExtent=4.0,
                    yExtent=4.0,
                    boundaryXY="reflective",
                ),
                geometry={"kind": "plane", "height": 0.0, "substrateMaterial": "Si"},
            )

    def test_2d_binding_requires_2d_domain_type(self):
        with self.assertRaises(ValidationError):
            GeometryBindingV1(
                schemaVersion=1,
                bindingId="bind_bad_3d_01",
                name="bad 3D binding",
                dimension=3,
                units=UNITS,
                domain=Domain(gridDelta=0.2, xExtent=4.0, xBoundary="reflective"),
                geometry={"kind": "plane", "height": 0.0, "substrateMaterial": "Si"},
            )


if __name__ == "__main__":
    unittest.main()
