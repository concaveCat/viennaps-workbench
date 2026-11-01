"""Core tests for the v2 process-model contract.

These tests use dummy in-memory variants and monkeypatched voxel conversions.
They do not import ViennaPS, start a server, or touch the workbench data
directory.
"""

from __future__ import annotations

import dataclasses
import json
import unittest
from unittest import mock

from pydantic import BaseModel

import process_models.v2.registry as registry_module
from process_models.v2 import (
    Artifact,
    DefaultStateAdapter,
    EngineCapabilities,
    EngineRequirement,
    EngineUnavailableError,
    ExecutionRequest,
    ExecutionResolutionError,
    FieldState,
    Issue,
    LevelSetState,
    MaterialState,
    ProcessContext,
    ProcessRegistry,
    ProcessSpec,
    ProcessState,
    StateDelta,
    StateMissingError,
    StaticEngine,
    StaticEngineProvider,
    VariantCapabilities,
    VariantRegistration,
    VoxelState,
    compute_implementation_hash,
    resolve_execution,
    version_satisfies,
)


# ---------------------------------------------------------------------------
# dummy contract implementations
# ---------------------------------------------------------------------------
class DummyParams(BaseModel):
    amount: float = 1.0


class OtherParams(BaseModel):
    amount: float = 1.0


_DUMMY_CAPS = VariantCapabilities(
    dimensions=(2, 3),
    reads=frozenset({"geometry"}),
    writes=frozenset({"geometry"}),
    supports_masks=True,
)

_VOXEL_CAPS = VariantCapabilities(
    dimensions=(2, 3),
    reads=frozenset({"geometry", "voxels"}),
    writes=frozenset({"voxels"}),
    geometry_constraints=("height-field",),
    experimental=True,
)

_ALL_CAPS = VariantCapabilities(
    dimensions=(2, 3),
    reads=frozenset({"geometry", "voxels", "fields"}),
    writes=frozenset({"geometry", "voxels", "fields"}),
    geometry_constraints=("height-field",),
    experimental=True,
)

_FIELD_CAPS = VariantCapabilities(
    dimensions=(2, 3),
    reads=frozenset({"fields"}),
    writes=frozenset({"fields"}),
)


class DummyVariant:
    variant_id = "dummy"
    version = "1.0.0"
    capabilities = _DUMMY_CAPS
    engine = EngineRequirement(kind="native", engine_id="dummy")
    implementation_hash = None

    def __init__(self) -> None:
        self.execute_calls = 0
        self.last_engine = None

    def validate(self, ctx: ProcessContext, params: BaseModel) -> list[Issue]:
        return []

    def execute(self, state: ProcessState, params: BaseModel, ctx: ProcessContext, engine: object) -> StateDelta:
        self.execute_calls += 1
        self.last_engine = engine
        return StateDelta()


class DummyVariantV2(DummyVariant):
    version = "2.0.0"


class ScriptedVariant(DummyVariant):
    variant_id = "scripted"

    def __init__(self, issues: list[Issue] | None = None) -> None:
        super().__init__()
        self.issues = list(issues or [])

    def validate(self, ctx: ProcessContext, params: BaseModel) -> list[Issue]:
        return list(self.issues)


class VoxelVariant(DummyVariant):
    variant_id = "voxel"
    capabilities = _VOXEL_CAPS


class FieldVariant(DummyVariant):
    variant_id = "field"
    capabilities = _FIELD_CAPS


def _spec(version: str = "1.0.0", process_id: str = "dummy.deposit") -> ProcessSpec:
    return ProcessSpec(
        id=process_id,
        version=version,
        label="Dummy deposition",
        params_model=DummyParams,
        dimensions=(2, 3),
        operations=("deposit",),
        description="test fixture",
    )


def _registration(
    variant: object | None = None,
    *,
    process_id: str = "dummy.deposit",
    engine: EngineRequirement | None = None,
    implementation_hash: str | None = None,
) -> VariantRegistration:
    variant = variant or DummyVariant()
    if engine is not None:
        variant.engine = engine  # type: ignore[attr-defined]
    if implementation_hash is not None:
        variant.implementation_hash = implementation_hash  # type: ignore[attr-defined]
    return VariantRegistration(process_id=process_id, variant=variant)  # type: ignore[arg-type]


def _static_engine(
    *,
    kind: str = "native",
    engine_id: str = "dummy",
    version: str = "1.2.3",
    backends: tuple[str, ...] = ("cpu",),
    capabilities: tuple[str, ...] = (),
    fingerprint: str = "fingerprint-1",
) -> StaticEngine:
    return StaticEngine(
        capabilities=EngineCapabilities(
            engine_id=engine_id,
            version=version,
            kind=kind,
            backends=backends,
            capabilities=capabilities,
        ),
        handle=object(),
        fingerprint=fingerprint,
    )


def _state(
    *,
    dimension: int = 2,
    material_order: tuple[int, ...] = (0,),
    materials: MaterialState | None = None,
) -> ProcessState:
    return ProcessState(
        geometry=LevelSetState(handle=object(), dimension=dimension, grid_delta=0.5, material_order=material_order),
        materials=materials if materials is not None else MaterialState(order=material_order, names={0: "Si"}),
    )


def _context(dimension: int = 2) -> ProcessContext:
    return ProcessContext(
        dimension=dimension,
        grid_delta=0.5,
        project={"name": "dummy"},
        step={"type": "deposit"},
    )


def _resolver_fixture(
    variant: object | None = None,
    *,
    engine: EngineRequirement | None = None,
) -> tuple[ProcessRegistry, StaticEngineProvider, ProcessSpec]:
    registry = ProcessRegistry()
    spec = _spec()
    registry.register_process(spec)
    registry.register_variant(_registration(variant, engine=engine))
    provider = StaticEngineProvider([_static_engine()])
    return registry, provider, spec


class FakeGrid:
    def __init__(self, material_order: tuple[int, ...] = (0,)) -> None:
        self.material_order = material_order


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------
class StateTest(unittest.TestCase):
    def test_material_state_validation_and_lookup(self) -> None:
        materials = MaterialState(order=(0, 1), names={0: "Si", 1: "SiO2"})
        self.assertEqual(materials.name_for(0), "Si")
        self.assertEqual(materials.name_for(1), "SiO2")
        self.assertIsNone(materials.name_for(9))
        self.assertEqual(materials.id_for("SiO2"), 1)
        self.assertIsNone(materials.id_for("Si3N4"))

        with self.assertRaises(ValueError):
            MaterialState(order=(-1,))
        with self.assertRaises(ValueError):
            MaterialState(order=(0, 0))
        with self.assertRaises(ValueError):
            MaterialState(order=(0,), names={1: "SiO2"})
        with self.assertRaises(ValueError):
            MaterialState(order=(0,), names={0: "  "})
        with self.assertRaises(ValueError):
            MaterialState(order=(0, 1), names={0: "Si", 1: "Si"})

    def test_levelset_state_validation(self) -> None:
        state = LevelSetState(handle=object(), dimension=3, grid_delta=0.25, material_order=(0, 1))
        self.assertEqual(state.dimension, 3)
        with self.assertRaises(ValueError):
            LevelSetState(handle=object(), dimension=4, grid_delta=0.5)
        with self.assertRaises(ValueError):
            LevelSetState(handle=object(), dimension=2, grid_delta=0.0)
        with self.assertRaises(ValueError):
            LevelSetState(handle=object(), dimension=2, grid_delta=float("nan"))
        with self.assertRaises(ValueError):
            LevelSetState(handle=object(), dimension=2, grid_delta=0.5, material_order=(-1,))

    def test_process_state_is_frozen_and_has_no_engine_or_meta(self) -> None:
        state = _state()
        self.assertEqual(
            {field.name for field in dataclasses.fields(ProcessState)},
            {"geometry", "fields", "voxels", "materials"},
        )
        self.assertFalse(hasattr(state, "engine"))
        self.assertFalse(hasattr(state, "meta"))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            state.fields = FieldState(data={"temperature": 300.0})  # type: ignore[misc]

        replaced = dataclasses.replace(state, voxels=VoxelState(grid=FakeGrid()))
        self.assertIsNot(replaced, state)
        self.assertIsNone(state.voxels)

    def test_process_state_has_require_and_missing_error(self) -> None:
        empty = ProcessState()
        self.assertFalse(empty.has("geometry"))
        self.assertFalse(empty.has("fields"))
        self.assertFalse(empty.has("voxels"))
        self.assertFalse(empty.has("materials"))
        with self.assertRaises(StateMissingError):
            empty.require("geometry")
        with self.assertRaises(StateMissingError):
            empty.require("fields")
        with self.assertRaises(ValueError):
            empty.has("bogus")  # type: ignore[arg-type]

        state = dataclasses.replace(_state(), fields=FieldState(data={"temperature": 300.0}))
        self.assertTrue(state.has("geometry"))
        self.assertTrue(state.has("fields"))
        self.assertTrue(state.has("materials"))
        self.assertIs(state.require("geometry"), state.geometry)
        self.assertIs(state.require("fields"), state.fields)
        self.assertIs(state.require("materials"), state.materials)

    def test_state_delta_is_empty_ignores_bookkeeping(self) -> None:
        self.assertTrue(StateDelta().is_empty())
        self.assertTrue(
            StateDelta(
                artifacts=[Artifact(kind="json", name="summary")],
                metadata={"rate": 1.0},
                warnings=["heads up"],
            ).is_empty()
        )
        self.assertFalse(StateDelta(geometry=LevelSetState(handle=object(), dimension=2, grid_delta=0.5)).is_empty())
        self.assertFalse(StateDelta(voxels=VoxelState(grid=FakeGrid())).is_empty())
        self.assertFalse(StateDelta(fields=FieldState(data={"x": 1})).is_empty())

    def test_process_context_validation_and_no_materials(self) -> None:
        with self.assertRaises(ValueError):
            ProcessContext(dimension=1, grid_delta=0.5)
        with self.assertRaises(ValueError):
            ProcessContext(dimension=2, grid_delta=0.0)

        context = ProcessContext(dimension=2, grid_delta=0.5)
        self.assertFalse(hasattr(context, "materials"))
        with self.assertRaises(TypeError):
            ProcessContext(dimension=2, grid_delta=0.5, materials=MaterialState(order=(0,)))  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------
class EngineTest(unittest.TestCase):
    def test_static_engine_matches_version_bounds(self) -> None:
        engine = _static_engine(version="4.7.0")

        lease = engine.acquire(EngineRequirement(kind="native", engine_id="dummy", min_version="4.6.2"))
        self.assertEqual(lease.engine_id, "dummy")
        self.assertEqual(lease.engine_version, "4.7.0")
        self.assertEqual(lease.fingerprint, "fingerprint-1")
        self.assertIsNotNone(lease.handle)
        self.assertEqual(lease.backend, "cpu")

        engine.acquire(EngineRequirement(kind="native", engine_id="dummy", max_version="4.7.0"))
        with self.assertRaises(EngineUnavailableError):
            engine.acquire(EngineRequirement(kind="native", engine_id="dummy", min_version="5.0.0"))
        with self.assertRaises(EngineUnavailableError):
            engine.acquire(EngineRequirement(kind="native", engine_id="dummy", max_version="4.6.2"))

    def test_static_engine_rejects_kind_backend_and_capability_mismatch(self) -> None:
        engine = _static_engine(version="1.0.0", backends=("cpu",))
        with self.assertRaises(EngineUnavailableError):
            engine.acquire(EngineRequirement(kind="surrogate", engine_id="dummy"))
        with self.assertRaises(EngineUnavailableError):
            engine.acquire(EngineRequirement(kind="native", engine_id="other"))
        with self.assertRaises(EngineUnavailableError):
            engine.acquire(EngineRequirement(kind="native", engine_id="dummy", backend="gpu"))
        with self.assertRaises(EngineUnavailableError):
            engine.acquire(EngineRequirement(kind="native", engine_id="dummy", capabilities=("masks",)))

    def test_backend_any_matches_gpu_only_engine(self) -> None:
        engine = _static_engine(backends=("gpu",))
        lease = engine.acquire(EngineRequirement(kind="native", engine_id="dummy", backend="any"))
        self.assertEqual(lease.backend, "gpu")
        gpu_lease = engine.acquire(EngineRequirement(kind="native", engine_id="dummy", backend="gpu"))
        self.assertEqual(gpu_lease.backend, "gpu")

    def test_version_satisfies_non_semver_requires_exact_bound(self) -> None:
        self.assertTrue(version_satisfies("4.7.0", "4.6.2", None))
        self.assertTrue(version_satisfies("4.7.0", None, "4.7.0"))
        self.assertFalse(version_satisfies("4.6.1", "4.6.2", None))
        self.assertTrue(version_satisfies("rev-a", "rev-a", None))
        self.assertFalse(version_satisfies("rev-a", "rev-b", None))
        self.assertFalse(version_satisfies("4.7.0", "rev-a", None))

    def test_static_engine_provider_first_match_and_no_fallback(self) -> None:
        cpu = _static_engine(backends=("cpu",), fingerprint="cpu-fp")
        gpu = _static_engine(backends=("gpu",), fingerprint="gpu-fp")
        provider = StaticEngineProvider([cpu, gpu])

        self.assertEqual([caps.backends for caps in provider.engines()], [("cpu",), ("gpu",)])
        self.assertEqual(provider.acquire(EngineRequirement(kind="native", engine_id="dummy")).fingerprint, "cpu-fp")
        self.assertEqual(
            provider.acquire(EngineRequirement(kind="native", engine_id="dummy", backend="gpu")).fingerprint,
            "gpu-fp",
        )

        with self.assertRaises(EngineUnavailableError):
            provider.acquire(EngineRequirement(kind="native", engine_id="missing"))
        gpu_only = StaticEngineProvider([_static_engine(backends=("gpu",))])
        with self.assertRaises(EngineUnavailableError):
            gpu_only.acquire(EngineRequirement(kind="native", engine_id="dummy", backend="cpu"))

    def test_static_lease_release_is_a_noop_and_supports_context_manager(self) -> None:
        with _static_engine().acquire(EngineRequirement(kind="native", engine_id="dummy")) as lease:
            self.assertEqual(lease.engine_id, "dummy")
        lease.release()
        lease.release()


# ---------------------------------------------------------------------------
# contract + registry
# ---------------------------------------------------------------------------
class ContractRegistryTest(unittest.TestCase):
    def test_list_processes_builds_descriptor_without_spec_method(self) -> None:
        self.assertFalse(hasattr(ProcessSpec, "describe"))

        registry = ProcessRegistry()
        registry.register_process(_spec())
        registry.register_variant(_registration(DummyVariant()))

        descriptor = registry.list_processes()[0]
        self.assertEqual(descriptor.id, "dummy.deposit")
        self.assertEqual(descriptor.version, "1.0.0")
        self.assertEqual(descriptor.dimensions, (2, 3))
        self.assertEqual(descriptor.operations, ("deposit",))
        self.assertEqual([variant.id for variant in descriptor.variants], ["dummy"])
        self.assertEqual(descriptor.description, "test fixture")

    def test_variant_registration_derives_identity(self) -> None:
        variant = DummyVariant()
        registration = VariantRegistration(process_id="dummy.deposit", variant=variant)

        self.assertEqual(registration.process_id, "dummy.deposit")
        self.assertEqual(registration.variant_id, "dummy")
        self.assertEqual(registration.version, "1.0.0")
        self.assertIs(registration.capabilities, variant.capabilities)
        self.assertIs(registration.capabilities, _DUMMY_CAPS)
        self.assertIs(registration.engine, variant.engine)
        self.assertIsNone(registration.declared_hash)

        variant.implementation_hash = "sha256:declared"
        self.assertEqual(registration.declared_hash, "sha256:declared")

    def test_variant_registration_validation(self) -> None:
        with self.assertRaises(ValueError):
            VariantRegistration(process_id=" ", variant=DummyVariant())

        class BlankIdVariant(DummyVariant):
            variant_id = " "

        with self.assertRaises(ValueError):
            VariantRegistration(process_id="dummy.deposit", variant=BlankIdVariant())

        class BlankVersionVariant(DummyVariant):
            version = ""

        with self.assertRaises(ValueError):
            VariantRegistration(process_id="dummy.deposit", variant=BlankVersionVariant())

        class MissingEngineVariant(DummyVariant):
            engine = None  # type: ignore[assignment]

        with self.assertRaises(TypeError):
            VariantRegistration(process_id="dummy.deposit", variant=MissingEngineVariant())

        class BadCapabilitiesVariant(DummyVariant):
            capabilities = "not-capabilities"  # type: ignore[assignment]

        with self.assertRaises(TypeError):
            VariantRegistration(process_id="dummy.deposit", variant=BadCapabilitiesVariant())

        class BadHashVariant(DummyVariant):
            implementation_hash = 123  # type: ignore[assignment]

        with self.assertRaises(TypeError):
            VariantRegistration(process_id="dummy.deposit", variant=BadHashVariant())

    def test_registry_multiple_process_versions_and_pins(self) -> None:
        registry = ProcessRegistry()
        registry.register_process(_spec("1.0.0"))
        registry.register_process(_spec("2.0.0"))
        registry.register_variant(_registration())

        self.assertEqual(registry.get_process("dummy.deposit").version, "2.0.0")
        self.assertEqual(registry.get_process("dummy.deposit", "1.0.0").version, "1.0.0")
        self.assertEqual(registry.get_variant("dummy.deposit", "dummy").process_version, "2.0.0")
        pinned = registry.get_variant("dummy.deposit", "dummy", expected_process_version="1.0.0")
        self.assertEqual(pinned.process_version, "1.0.0")

    def test_registry_add_variant_without_process_bump(self) -> None:
        registry = ProcessRegistry()
        registry.register_process(_spec("1.0.0"))
        registry.register_variant(_registration(DummyVariant()))

        registry.register_variant(_registration(DummyVariantV2()))

        self.assertEqual(registry.get_process("dummy.deposit").version, "1.0.0")
        self.assertEqual(registry.get_variant("dummy.deposit", "dummy").variant_version, "2.0.0")
        pinned = registry.get_variant(
            "dummy.deposit", "dummy", expected_process_version="1.0.0", expected_variant_version="1.0.0"
        )
        self.assertEqual(pinned.variant_version, "1.0.0")
        descriptors = registry.list_processes()
        self.assertEqual(len(descriptors), 1)
        self.assertEqual([variant.version for variant in descriptors[0].variants], ["1.0.0", "2.0.0"])

    def test_registry_latest_selection_and_registration_order_fallback(self) -> None:
        semver = ProcessRegistry()
        for version in ("1.9.0", "1.10.0", "1.2.0"):
            semver.register_process(_spec(version))
        self.assertEqual(semver.get_process("dummy.deposit").version, "1.10.0")

        commit_style = ProcessRegistry()
        for version in ("rev-a", "rev-b", "rev-c"):
            commit_style.register_process(_spec(version))
        self.assertEqual(commit_style.get_process("dummy.deposit").version, "rev-c")

    def test_registry_duplicate_keys_raise_value_error(self) -> None:
        registry = ProcessRegistry()
        registry.register_process(_spec("1.0.0"))
        with self.assertRaises(ValueError):
            registry.register_process(_spec("1.0.0"))

        registry.register_variant(_registration(DummyVariant()))
        with self.assertRaises(ValueError):
            registry.register_variant(_registration(DummyVariant()))

    def test_registry_unknown_keys_raise_key_error(self) -> None:
        registry = ProcessRegistry()
        with self.assertRaises(KeyError):
            registry.get_process("missing")
        with self.assertRaises(KeyError):
            registry.register_variant(_registration())

        registry.register_process(_spec("1.0.0"))
        with self.assertRaises(KeyError):
            registry.get_process("missing")
        with self.assertRaises(KeyError):
            registry.get_process("dummy.deposit", "9.9.9")
        with self.assertRaises(KeyError):
            registry.get_variant("dummy.deposit", "missing")
        with self.assertRaises(KeyError):
            registry.list_variants("missing")

        registry.register_variant(_registration())
        with self.assertRaises(KeyError):
            registry.get_variant("dummy.deposit", "dummy", expected_variant_version="9.9.9")

    def test_registry_descriptors_include_variants(self) -> None:
        registry = ProcessRegistry()
        registry.register_process(_spec())
        registry.register_variant(_registration(DummyVariant()))
        registry.register_variant(_registration(VoxelVariant()))

        descriptors = registry.list_processes()
        self.assertEqual(len(descriptors), 1)
        variants = {variant.id: variant for variant in descriptors[0].variants}
        self.assertEqual(variants["voxel"].kind, "native")
        self.assertEqual(variants["voxel"].reads, ("geometry", "voxels"))
        self.assertEqual(variants["voxel"].writes, ("voxels",))
        self.assertTrue(variants["voxel"].experimental)
        self.assertEqual(len(registry.list_variants("dummy.deposit")), 2)

    def test_implementation_hash_is_file_based_and_stable(self) -> None:
        variant = DummyVariant()
        digest = compute_implementation_hash(variant)
        self.assertEqual(len(digest), 64)
        int(digest, 16)
        self.assertEqual(digest, compute_implementation_hash(variant))
        self.assertNotEqual(digest, compute_implementation_hash(DummyVariantV2()))

        # Forcing the "no source file" fallback must change the digest: the
        # file bytes (including module-level helpers) were part of it.
        with mock.patch.object(registry_module, "_source_file", return_value=None):
            class_only = compute_implementation_hash(variant)
        self.assertEqual(len(class_only), 64)
        self.assertNotEqual(class_only, digest)

    def test_registry_uses_declared_or_computed_hash(self) -> None:
        registry = ProcessRegistry()
        registry.register_process(_spec())
        registry.register_variant(_registration(DummyVariant(), implementation_hash="sha256:explicit"))
        self.assertEqual(
            registry.get_variant("dummy.deposit", "dummy").implementation_hash,
            "sha256:explicit",
        )
        self.assertEqual(registry.list_variants("dummy.deposit")[0].implementation_hash, "sha256:explicit")

        computed = ProcessRegistry()
        computed.register_process(_spec())
        computed.register_variant(_registration(DummyVariant()))
        self.assertIsNone(computed._variants["dummy.deposit"]["dummy"][0].declared_hash)
        self.assertEqual(
            computed.get_variant("dummy.deposit", "dummy").implementation_hash,
            compute_implementation_hash(DummyVariant()),
        )
        self.assertEqual(
            computed.list_variants("dummy.deposit")[0].implementation_hash,
            compute_implementation_hash(DummyVariant()),
        )


# ---------------------------------------------------------------------------
# resolver
# ---------------------------------------------------------------------------
class ResolverTest(unittest.TestCase):
    def test_resolver_success_and_stable_identity(self) -> None:
        registry, provider, spec = _resolver_fixture()
        request = ExecutionRequest(
            process_id="dummy.deposit",
            variant_id="dummy",
            params=DummyParams(amount=2.0),
            context=_context(),
            state=_state(),
        )
        plan = resolve_execution(request, registry, provider)

        self.assertIs(plan.request, request)
        self.assertEqual(plan.process.id, spec.id)
        self.assertEqual(plan.process_version, "1.0.0")
        self.assertEqual(plan.variant_version, "1.0.0")
        self.assertEqual(plan.engine_version, "1.2.3")
        self.assertEqual(plan.engine_fingerprint, "fingerprint-1")
        self.assertEqual(plan.state_adapter.id, "default-v2")
        self.assertEqual(plan.implementation_hash, compute_implementation_hash(DummyVariant()))

        identity = plan.identity()
        self.assertEqual(
            set(identity),
            {
                "process_id",
                "process_version",
                "variant_id",
                "variant_version",
                "implementation_hash",
                "engine_id",
                "engine_version",
                "engine_fingerprint",
                "adapter",
            },
        )
        self.assertTrue(all(isinstance(value, str) for value in identity.values()))
        json.dumps(identity)
        self.assertEqual(identity, plan.identity())

        # Resolution must not execute the variant.
        self.assertEqual(plan.variant.variant.execute_calls, 0)

    def test_resolver_rejects_wrong_params_type(self) -> None:
        registry, provider, _ = _resolver_fixture()
        with self.assertRaises(ExecutionResolutionError):
            resolve_execution(
                ExecutionRequest(
                    process_id="dummy.deposit",
                    variant_id="dummy",
                    params=OtherParams(),
                    context=_context(),
                    state=_state(),
                ),
                registry,
                provider,
            )

    def test_resolver_rejects_unsupported_dimension(self) -> None:
        variant = VoxelVariant()
        variant.capabilities = VariantCapabilities(dimensions=(2,), reads=frozenset({"geometry"}), writes=frozenset())
        registry = ProcessRegistry()
        registry.register_process(_spec())
        registry.register_variant(_registration(variant))

        with self.assertRaises(ExecutionResolutionError) as caught:
            resolve_execution(
                ExecutionRequest(
                    process_id="dummy.deposit",
                    variant_id="voxel",
                    params=DummyParams(),
                    context=_context(dimension=3),
                    state=_state(dimension=3),
                ),
                registry,
                StaticEngineProvider([_static_engine()]),
            )
        self.assertIn("dimension 3", str(caught.exception))

    def test_resolver_rejects_missing_state_kind(self) -> None:
        registry, provider, _ = _resolver_fixture(VoxelVariant())
        with self.assertRaises(ExecutionResolutionError) as caught:
            resolve_execution(
                ExecutionRequest(
                    process_id="dummy.deposit",
                    variant_id="voxel",
                    params=DummyParams(),
                    context=_context(),
                    state=ProcessState(),
                ),
                registry,
                provider,
            )
        message = str(caught.exception)
        self.assertIn("geometry", message)
        self.assertIn("voxels", message)

    def test_resolver_allows_voxel_read_derived_from_geometry(self) -> None:
        registry, provider, _ = _resolver_fixture(VoxelVariant())
        state = _state()
        self.assertIsNone(state.voxels)
        plan = resolve_execution(
            ExecutionRequest(
                process_id="dummy.deposit",
                variant_id="voxel",
                params=DummyParams(),
                context=_context(),
                state=state,
            ),
            registry,
            provider,
        )
        self.assertEqual(plan.state_adapter.id, "default-v2")
        self.assertEqual(plan.state_adapter.provides, frozenset({"voxels"}))

    def test_resolver_rejects_material_order_mismatch(self) -> None:
        registry, provider, _ = _resolver_fixture()
        state = _state(material_order=(0, 1), materials=MaterialState(order=(0,)))
        with self.assertRaises(ExecutionResolutionError) as caught:
            resolve_execution(
                ExecutionRequest(
                    process_id="dummy.deposit",
                    variant_id="dummy",
                    params=DummyParams(),
                    context=_context(),
                    state=state,
                ),
                registry,
                provider,
            )
        self.assertIn("material order", str(caught.exception))

    def test_resolver_uses_state_materials_not_context(self) -> None:
        registry, provider, _ = _resolver_fixture()
        context = _context()
        self.assertFalse(hasattr(context, "materials"))
        # A consistent state.materials passes even though the context has no
        # material table at all.
        plan = resolve_execution(
            ExecutionRequest(
                process_id="dummy.deposit",
                variant_id="dummy",
                params=DummyParams(),
                context=context,
                state=_state(materials=MaterialState(order=(0,), names={0: "Si"})),
            ),
            registry,
            provider,
        )
        self.assertEqual(plan.process.id, "dummy.deposit")

    def test_resolver_rejects_any_validation_error_with_all_messages(self) -> None:
        variant = ScriptedVariant(
            [
                Issue(path="amount", message="amount is too small"),
                Issue(path="", message="material table is empty"),
                Issue(path="rate", message="warning only", severity="warning"),
            ]
        )
        registry, provider, _ = _resolver_fixture(variant)
        with self.assertRaises(ExecutionResolutionError) as caught:
            resolve_execution(
                ExecutionRequest(
                    process_id="dummy.deposit",
                    variant_id="scripted",
                    params=DummyParams(),
                    context=_context(),
                    state=_state(),
                ),
                registry,
                provider,
            )
        message = str(caught.exception)
        self.assertIn("amount is too small", message)
        self.assertIn("material table is empty", message)
        self.assertNotIn("warning only", message)

    def test_resolver_maps_engine_unavailable_without_fallback(self) -> None:
        registry = ProcessRegistry()
        registry.register_process(_spec())
        registry.register_variant(
            _registration(
                engine=EngineRequirement(kind="native", engine_id="dummy", min_version="9.0.0"),
            )
        )
        provider = StaticEngineProvider([_static_engine(version="1.2.3", fingerprint="old")])

        with self.assertRaises(ExecutionResolutionError) as caught:
            resolve_execution(
                ExecutionRequest(
                    process_id="dummy.deposit",
                    variant_id="dummy",
                    params=DummyParams(),
                    context=_context(),
                    state=_state(),
                ),
                registry,
                provider,
            )
        self.assertIn("9.0.0", str(caught.exception))


# ---------------------------------------------------------------------------
# adapters
# ---------------------------------------------------------------------------
class AdapterTest(unittest.TestCase):
    def test_adapter_provides_only_derived_representations(self) -> None:
        self.assertEqual(DefaultStateAdapter.provides, frozenset({"voxels"}))
        self.assertEqual(DefaultStateAdapter().provides, frozenset({"voxels"}))

    def test_prepare_reads_voxels_from_geometry_without_mutating_input(self) -> None:
        state = _state()
        grid = FakeGrid()
        adapter = DefaultStateAdapter()
        with mock.patch("process_models.v2.adapters._read_voxels", return_value=grid) as read:
            prepared = adapter.prepare(state, _VOXEL_CAPS)

        read.assert_called_once_with(state.geometry.handle)
        self.assertIs(prepared.voxels.grid, grid)
        self.assertIsNone(state.voxels)
        self.assertIsNot(prepared, state)
        self.assertIs(prepared.geometry, state.geometry)

    def test_prepare_rejects_voxels_without_geometry(self) -> None:
        with self.assertRaises(StateMissingError):
            DefaultStateAdapter().prepare(ProcessState(), _VOXEL_CAPS)

    def test_prepare_rejects_missing_fields(self) -> None:
        with self.assertRaises(StateMissingError):
            DefaultStateAdapter().prepare(_state(), _FIELD_CAPS)

        state = dataclasses.replace(_state(), fields=FieldState(data={"x": 1}))
        self.assertIs(DefaultStateAdapter().prepare(state, _FIELD_CAPS), state)

    def test_commit_voxel_delta_writes_back_and_clears_voxels(self) -> None:
        state = dataclasses.replace(_state(), voxels=VoxelState(grid=FakeGrid()))
        grid = FakeGrid(material_order=(0, 1))
        new_handle = object()
        delta = StateDelta(voxels=VoxelState(grid=grid))

        with mock.patch("process_models.v2.adapters._write_voxels", return_value=new_handle) as write:
            committed = DefaultStateAdapter().commit(state, delta, _VOXEL_CAPS)

        write.assert_called_once_with(state.geometry.handle, grid)
        self.assertIs(committed.geometry.handle, new_handle)
        self.assertEqual(committed.geometry.material_order, (0, 1))
        self.assertEqual(committed.geometry.dimension, state.geometry.dimension)
        self.assertEqual(committed.geometry.grid_delta, state.geometry.grid_delta)
        self.assertIsNone(committed.voxels)
        self.assertEqual(committed.materials.order, (0, 1))
        # Input state is untouched.
        self.assertIsNotNone(state.voxels)
        self.assertIsNot(state.geometry, committed.geometry)

    def test_commit_geometry_delta_wins_over_voxels_and_applies_fields(self) -> None:
        state = dataclasses.replace(_state(), voxels=VoxelState(grid=FakeGrid()))
        new_geometry = LevelSetState(handle=object(), dimension=2, grid_delta=0.5, material_order=(0, 1))
        fields = FieldState(data={"temperature": 400.0})
        delta = StateDelta(geometry=new_geometry, voxels=VoxelState(grid=FakeGrid()), fields=fields)

        with mock.patch("process_models.v2.adapters._write_voxels") as write:
            committed = DefaultStateAdapter().commit(state, delta, _ALL_CAPS)

        write.assert_not_called()
        self.assertIs(committed.geometry, new_geometry)
        self.assertIs(committed.fields, fields)
        self.assertIsNone(committed.voxels)
        self.assertEqual(committed.materials.order, (0, 1))
        self.assertEqual(committed.materials.name_for(0), "Si")

    def test_commit_rejects_voxel_delta_when_write_is_not_declared(self) -> None:
        state = _state()
        with self.assertRaises(ExecutionResolutionError):
            DefaultStateAdapter().commit(state, StateDelta(voxels=VoxelState(grid=FakeGrid())), _DUMMY_CAPS)

    def test_commit_rejects_geometry_delta_when_write_is_not_declared(self) -> None:
        state = _state()
        capabilities = VariantCapabilities(dimensions=(2,), reads=frozenset({"geometry"}), writes=frozenset())
        with self.assertRaises(ExecutionResolutionError):
            DefaultStateAdapter().commit(state, StateDelta(geometry=state.geometry), capabilities)

    def test_commit_requires_original_geometry_for_voxels(self) -> None:
        with self.assertRaises(StateMissingError):
            DefaultStateAdapter().commit(
                ProcessState(voxels=VoxelState(grid=FakeGrid())),
                StateDelta(voxels=VoxelState(grid=FakeGrid())),
                _VOXEL_CAPS,
            )

    def test_commit_empty_delta_returns_same_state(self) -> None:
        state = _state()
        self.assertIs(DefaultStateAdapter().commit(state, StateDelta(), _DUMMY_CAPS), state)

    def test_commit_fields_delta_returns_new_state(self) -> None:
        state = _state()
        fields = FieldState(data={"temperature": 350.0})
        committed = DefaultStateAdapter().commit(state, StateDelta(fields=fields), _FIELD_CAPS)
        self.assertIs(committed.fields, fields)
        self.assertIsNone(state.fields)
        self.assertIs(committed.geometry, state.geometry)


if __name__ == "__main__":
    unittest.main()
