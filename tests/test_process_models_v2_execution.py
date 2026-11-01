"""Execution-entry tests for the v2 process-model contract.

These tests use dummy variants only; they do not import ViennaPS and do not
touch the data directory. They cover the gap between ``resolve_execution`` and
a real step: binding the engine lease, calling the adapter, executing the
variant, committing the delta, and surfacing metadata/warnings/artifacts.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from types import SimpleNamespace

from pydantic import BaseModel

from process_models.v2 import (
    Artifact,
    DefaultStateAdapter,
    EngineCapabilities,
    EngineRequirement,
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
    implementation_hash = "d" * 64

    def validate(self, ctx: ProcessContext, params: BaseModel) -> list[Issue]:
        return []

    def execute(self, state: ProcessState, params: BaseModel, ctx: ProcessContext) -> StateDelta:
        return StateDelta(
            geometry=state.geometry,
            metadata={"value": params.value},
            warnings=["dummy warning"],
            artifacts=[Artifact(kind="json", name="dummy.json")],
        )


class VoxelReadVariant(DummyVariant):
    variant_id = "voxel-read"
    version = "1.0.0"
    capabilities = VariantCapabilities(
        dimensions=(2,),
        reads=frozenset({"voxels"}),
        writes=frozenset({"voxels"}),
    )

    def execute(self, state: ProcessState, params: BaseModel, ctx: ProcessContext) -> StateDelta:
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
    version = "1.0.0"

    def execute(self, state: ProcessState, params: BaseModel, ctx: ProcessContext) -> StateDelta:
        return {"not": "a delta"}  # type: ignore[return-value]


@dataclass
class Fixture:
    registry: ProcessRegistry
    provider: StaticEngineProvider
    spec: ProcessSpec
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
    registration = VariantRegistration(
        process_id=spec.id,
        variant_id=variant.variant_id,
        version=variant.version,
        variant=variant,
        capabilities=variant.capabilities,
        engine=EngineRequirement(kind="native", engine_id="test", min_version="1.0.0"),
        implementation_hash=variant.implementation_hash,
    )
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
        materials=materials,
    )
    state = ProcessState(
        geometry=LevelSetState(handle=object(), dimension=2, grid_delta=0.1, material_order=(10,)),
        materials=materials,
    )
    return Fixture(registry=registry, provider=provider, spec=spec, context=context, state=state)


class ExecutionEntryTest(unittest.TestCase):
    def test_execute_plan_binds_engine_and_returns_outcome(self) -> None:
        fixture = _fixture()
        plan = resolve_execution(
            request=_request(fixture),
            registry=fixture.registry,
            provider=fixture.provider,
        )
        outcome = execute_plan(plan, fixture.state, DummyParams(value=7), fixture.context)

        self.assertIs(outcome.plan, plan)
        self.assertIs(outcome.state.engine, plan.engine)
        self.assertEqual(outcome.metadata["value"], 7)
        self.assertEqual(outcome.warnings, ["dummy warning"])
        self.assertEqual([artifact.name for artifact in outcome.artifacts], ["dummy.json"])

    def test_execute_plan_rejects_wrong_params_type(self) -> None:
        fixture = _fixture()
        plan = resolve_execution(_request(fixture), fixture.registry, fixture.provider)
        with self.assertRaises(ExecutionResolutionError):
            execute_plan(plan, fixture.state, OtherParams(), fixture.context)

    def test_execute_plan_rejects_non_delta_return(self) -> None:
        fixture = _fixture()
        variant = BadDeltaVariant()
        fixture.registry.register_variant(
            VariantRegistration(
                process_id=fixture.spec.id,
                variant_id=variant.variant_id,
                version=variant.version,
                variant=variant,
                capabilities=variant.capabilities,
                engine=EngineRequirement(kind="native", engine_id="test", min_version="1.0.0"),
                implementation_hash=variant.implementation_hash,
            )
        )
        plan = resolve_execution(
            _request(fixture, variant_id="bad-delta"),
            fixture.registry,
            fixture.provider,
        )
        with self.assertRaises(ExecutionResolutionError):
            execute_plan(plan, fixture.state, DummyParams(), fixture.context)

    def test_resolver_uses_adapter_derivation_capabilities(self) -> None:
        fixture = _fixture()
        variant = VoxelReadVariant()
        fixture.registry.register_variant(
            VariantRegistration(
                process_id=fixture.spec.id,
                variant_id=variant.variant_id,
                version=variant.version,
                variant=variant,
                capabilities=variant.capabilities,
                engine=EngineRequirement(kind="native", engine_id="test", min_version="1.0.0"),
                implementation_hash=variant.implementation_hash,
            )
        )
        request = _request(fixture, variant_id="voxel-read")
        with self.assertRaises(ExecutionResolutionError):
            resolve_execution(request, fixture.registry, fixture.provider, adapter=NoVoxelAdapter())
        plan = resolve_execution(request, fixture.registry, fixture.provider)
        self.assertEqual(plan.state_adapter.id, "default-v2")

    def test_resolver_rejects_geometry_dimension_mismatch(self) -> None:
        fixture = _fixture()
        fixture.state.geometry = LevelSetState(handle=object(), dimension=3, grid_delta=0.1, material_order=(10,))
        with self.assertRaises(ExecutionResolutionError):
            resolve_execution(_request(fixture), fixture.registry, fixture.provider)

    def test_resolver_rejects_geometry_grid_mismatch(self) -> None:
        fixture = _fixture()
        fixture.state.geometry = LevelSetState(handle=object(), dimension=2, grid_delta=0.2, material_order=(10,))
        with self.assertRaises(ExecutionResolutionError):
            resolve_execution(_request(fixture), fixture.registry, fixture.provider)

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


def _request(fixture: Fixture, *, variant_id: str = "dummy"):
    from process_models.v2 import ExecutionRequest

    return ExecutionRequest(
        process_id=fixture.spec.id,
        variant_id=variant_id,
        params=DummyParams(),
        context=fixture.context,
        state=fixture.state,
    )


if __name__ == "__main__":
    unittest.main()
