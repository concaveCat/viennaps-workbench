"""Execution-entry tests for the v2 process-model contract.

These tests use dummy variants only; they do not import ViennaPS and do not
touch the data directory. They cover the gap between ``resolve_execution`` and
a real step: passing the engine lease explicitly, calling the adapter,
executing the variant, committing the delta, and surfacing
metadata/warnings/artifacts.
"""

from __future__ import annotations

import dataclasses
import unittest
from dataclasses import dataclass
from types import SimpleNamespace

from pydantic import BaseModel

from process_models.v2 import (
    Artifact,
    DefaultStateAdapter,
    EngineCapabilities,
    EngineRequirement,
    ExecutionOutcome,
    ExecutionRequest,
    ExecutionResolutionError,
    FieldState,
    LevelSetState,
    MaterialState,
    ProcessContext,
    ProcessRegistry,
    ProcessSpec,
    ProcessState,
    StateDelta,
    StaticEngine,
    StaticEngineProvider,
    VariantCapabilities,
    VariantRegistration,
    VoxelState,
    execute_plan,
    resolve_execution,
    run_step,
)
from process_models.v2.contract import EngineVariant, Issue


class DummyParams(BaseModel):
    value: int = 1


class OtherParams(BaseModel):
    other: int = 0


class DummyVariant(EngineVariant):
    variant_id = "dummy"
    version = "1.0.0"
    capabilities = VariantCapabilities(
        dimensions=(2,),
        reads=frozenset({"geometry"}),
        writes=frozenset({"geometry"}),
    )
    engine = EngineRequirement(kind="native", engine_id="test", min_version="1.0.0")
    implementation_hash = "d" * 64

    def __init__(self) -> None:
        self.last_engine = None
        self.last_state = None
        self.last_params = None
        self.last_context = None

    def validate(self, ctx: ProcessContext, params: BaseModel) -> list[Issue]:
        return []

    def execute(self, state: ProcessState, params: BaseModel, ctx: ProcessContext, engine: object) -> StateDelta:
        self.last_engine = engine
        self.last_state = state
        self.last_params = params
        self.last_context = ctx
        return StateDelta(
            geometry=state.geometry,
            metadata={"value": params.value},
            warnings=["dummy warning"],
            artifacts=[Artifact(kind="json", name="dummy.json")],
        )


class VoxelReadVariant(DummyVariant):
    variant_id = "voxel-read"
    capabilities = VariantCapabilities(
        dimensions=(2,),
        reads=frozenset({"voxels"}),
        writes=frozenset({"voxels"}),
    )

    def execute(self, state: ProcessState, params: BaseModel, ctx: ProcessContext, engine: object) -> StateDelta:
        return StateDelta(voxels=state.voxels)


class NoVoxelAdapter:
    id = "no-voxels"
    provides = frozenset({"geometry"})

    def prepare(self, state: ProcessState, capabilities: VariantCapabilities) -> ProcessState:
        return state

    def commit(
        self,
        state: ProcessState,
        delta: StateDelta,
        capabilities: VariantCapabilities,
    ) -> ProcessState:
        return state


class BadDeltaVariant(DummyVariant):
    variant_id = "bad-delta"

    def execute(self, state: ProcessState, params: BaseModel, ctx: ProcessContext, engine: object):
        return {"not": "a delta"}


@dataclass
class Fixture:
    registry: ProcessRegistry
    provider: StaticEngineProvider
    spec: ProcessSpec
    variant: DummyVariant
    context: ProcessContext
    state: ProcessState


def _fixture() -> Fixture:
    spec = ProcessSpec(
        id="dummy",
        version="1.0.0",
        label="Dummy",
        params_model=DummyParams,
        dimensions=(2,),
        operations=("test",),
    )
    variant = DummyVariant()
    registration = VariantRegistration(process_id=spec.id, variant=variant)
    registry = ProcessRegistry()
    registry.register_process(spec)
    registry.register_variant(registration)

    engine = StaticEngine(
        capabilities=EngineCapabilities(
            engine_id="test",
            version="1.0.0",
            kind="native",
            backends=("cpu",),
        ),
        handle="engine-handle",
        fingerprint="engine-fingerprint",
    )
    provider = StaticEngineProvider([engine])

    materials = MaterialState(order=(10,), names={10: "Si"})
    context = ProcessContext(
        dimension=2,
        grid_delta=0.1,
        project={},
        step={},
    )
    state = ProcessState(
        geometry=LevelSetState(handle=object(), dimension=2, grid_delta=0.1, material_order=(10,)),
        materials=materials,
    )
    return Fixture(registry=registry, provider=provider, spec=spec, variant=variant, context=context, state=state)


def _request(
    fixture: Fixture,
    *,
    variant_id: str = "dummy",
    state: ProcessState | None = None,
    params: BaseModel | None = None,
) -> ExecutionRequest:
    return ExecutionRequest(
        process_id=fixture.spec.id,
        variant_id=variant_id,
        params=params if params is not None else DummyParams(),
        context=fixture.context,
        state=state if state is not None else fixture.state,
    )


class ExecutionEntryTest(unittest.TestCase):
    def test_execute_plan_passes_engine_and_returns_outcome(self) -> None:
        fixture = _fixture()
        plan = resolve_execution(_request(fixture), fixture.registry, fixture.provider)
        outcome = execute_plan(plan)

        self.assertIsInstance(outcome, ExecutionOutcome)
        self.assertIs(outcome.plan, plan)
        self.assertIs(fixture.variant.last_engine, plan.engine)
        self.assertIs(fixture.variant.last_state, fixture.state)
        self.assertIs(fixture.variant.last_params, plan.request.params)
        self.assertIs(fixture.variant.last_context, fixture.context)
        self.assertEqual(outcome.metadata["value"], 1)
        self.assertEqual(outcome.warnings, ["dummy warning"])
        self.assertEqual([artifact.name for artifact in outcome.artifacts], ["dummy.json"])
        self.assertIsInstance(outcome.artifacts[0], Artifact)

    def test_execution_plan_run_matches_execute_plan(self) -> None:
        fixture = _fixture()
        plan = resolve_execution(_request(fixture), fixture.registry, fixture.provider)

        direct = execute_plan(plan)
        through_method = plan.run()

        self.assertIs(through_method.plan, plan)
        self.assertEqual(through_method.metadata, direct.metadata)
        self.assertEqual(through_method.warnings, direct.warnings)
        self.assertEqual([item.name for item in through_method.artifacts], [item.name for item in direct.artifacts])
        self.assertEqual(through_method.state.geometry, direct.state.geometry)

    def test_run_step_resolves_and_executes_in_one_call(self) -> None:
        fixture = _fixture()
        outcome = run_step(
            fixture.registry,
            fixture.provider,
            fixture.spec.id,
            "dummy",
            DummyParams(value=7),
            fixture.context,
            fixture.state,
            expected_process_version="1.0.0",
            expected_variant_version="1.0.0",
        )

        self.assertEqual(outcome.plan.process_version, "1.0.0")
        self.assertEqual(outcome.plan.variant_version, "1.0.0")
        self.assertEqual(outcome.metadata["value"], 7)
        self.assertIs(fixture.variant.last_engine, outcome.plan.engine)

    def test_run_step_uses_custom_adapter(self) -> None:
        fixture = _fixture()
        outcome = run_step(
            fixture.registry,
            fixture.provider,
            fixture.spec.id,
            "dummy",
            DummyParams(),
            fixture.context,
            fixture.state,
            adapter=NoVoxelAdapter(),
        )
        self.assertEqual(outcome.plan.state_adapter.id, "no-voxels")

    def test_execute_plan_uses_plan_request_only(self) -> None:
        fixture = _fixture()
        plan = resolve_execution(_request(fixture), fixture.registry, fixture.provider)
        self.assertIs(plan.request.state, fixture.state)

        tampered = dataclasses.replace(plan, request=dataclasses.replace(plan.request, params=OtherParams()))
        with self.assertRaises(ExecutionResolutionError):
            execute_plan(tampered)
        with self.assertRaises(ExecutionResolutionError):
            tampered.run()

        # The untouched plan still executes with its own request.
        outcome = execute_plan(plan)
        self.assertEqual(outcome.metadata["value"], 1)

    def test_execute_plan_rejects_wrong_params_type(self) -> None:
        fixture = _fixture()
        plan = resolve_execution(_request(fixture), fixture.registry, fixture.provider)
        tampered = dataclasses.replace(plan, request=dataclasses.replace(plan.request, params=OtherParams()))
        with self.assertRaises(ExecutionResolutionError):
            execute_plan(tampered)

    def test_execute_plan_rejects_non_delta_return(self) -> None:
        fixture = _fixture()
        variant = BadDeltaVariant()
        fixture.registry.register_variant(VariantRegistration(process_id=fixture.spec.id, variant=variant))
        plan = resolve_execution(
            _request(fixture, variant_id="bad-delta"),
            fixture.registry,
            fixture.provider,
        )
        with self.assertRaises(ExecutionResolutionError):
            execute_plan(plan)

    def test_resolver_uses_adapter_derivation_capabilities(self) -> None:
        fixture = _fixture()
        variant = VoxelReadVariant()
        fixture.registry.register_variant(VariantRegistration(process_id=fixture.spec.id, variant=variant))
        request = _request(fixture, variant_id="voxel-read")
        with self.assertRaises(ExecutionResolutionError):
            resolve_execution(request, fixture.registry, fixture.provider, adapter=NoVoxelAdapter())
        plan = resolve_execution(request, fixture.registry, fixture.provider)
        self.assertEqual(plan.state_adapter.id, "default-v2")
        self.assertEqual(plan.state_adapter.provides, frozenset({"voxels"}))

    def test_resolver_rejects_geometry_dimension_mismatch(self) -> None:
        fixture = _fixture()
        state = dataclasses.replace(
            fixture.state,
            geometry=LevelSetState(handle=object(), dimension=3, grid_delta=0.1, material_order=(10,)),
        )
        with self.assertRaises(ExecutionResolutionError):
            resolve_execution(_request(fixture, state=state), fixture.registry, fixture.provider)

    def test_resolver_rejects_geometry_grid_mismatch(self) -> None:
        fixture = _fixture()
        state = dataclasses.replace(
            fixture.state,
            geometry=LevelSetState(handle=object(), dimension=2, grid_delta=0.2, material_order=(10,)),
        )
        with self.assertRaises(ExecutionResolutionError):
            resolve_execution(_request(fixture, state=state), fixture.registry, fixture.provider)

    def test_commit_rejects_unregistered_geometry_write(self) -> None:
        fixture = _fixture()
        capabilities = VariantCapabilities(
            dimensions=(2,),
            reads=frozenset({"geometry"}),
            writes=frozenset(),
        )
        with self.assertRaises(ExecutionResolutionError):
            DefaultStateAdapter().commit(
                fixture.state,
                StateDelta(geometry=fixture.state.geometry),
                capabilities,
            )

    def test_commit_rejects_unregistered_fields_write(self) -> None:
        fixture = _fixture()
        capabilities = VariantCapabilities(
            dimensions=(2,),
            reads=frozenset({"geometry"}),
            writes=frozenset(),
        )
        with self.assertRaises(ExecutionResolutionError):
            DefaultStateAdapter().commit(
                fixture.state,
                StateDelta(fields=FieldState(data={"x": 1})),
                capabilities,
            )

    def test_commit_rejects_unregistered_voxel_write(self) -> None:
        fixture = _fixture()
        capabilities = VariantCapabilities(
            dimensions=(2,),
            reads=frozenset({"geometry"}),
            writes=frozenset(),
        )
        grid = SimpleNamespace(material_order=(10,))
        with self.assertRaises(ExecutionResolutionError):
            DefaultStateAdapter().commit(
                fixture.state,
                StateDelta(voxels=VoxelState(grid=grid)),
                capabilities,
            )


if __name__ == "__main__":
    unittest.main()
