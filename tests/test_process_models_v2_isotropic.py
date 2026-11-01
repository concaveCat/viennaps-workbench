"""Tests for the native v2 isotropic plugin.

Each test builds one small 2D/3D level-set domain, resolves the real
``isotropic``/``native`` registration against a static native engine whose
handle is the installed ``viennaps`` module, and executes the step. No server,
worker process, or data directory is involved.
"""

from __future__ import annotations

import hashlib
import unittest
from typing import Any

import numpy as np
import viennaps as ps
from pydantic import ValidationError

from process_models.v2.contract import ProcessContext, VariantCapabilities, VariantRegistration
from process_models.v2.engine import (
    EngineCapabilities,
    StaticEngine,
    StaticEngineProvider,
)
from process_models.v2.native.isotropic import (
    IsotropicNativeVariant,
    IsotropicParams,
    IsotropicProcessSpec,
    IsotropicSpec,
    register_isotropic,
)
from process_models.v2.registry import ProcessRegistry
from process_models.v2.resolver import (
    ExecutionRequest,
    ExecutionResolutionError,
    resolve_execution,
)
from process_models.v2.state import LevelSetState, MaterialState, ProcessState, StateDelta

GRID_DELTA = 0.1
X_EXTENT = 1.0
FINGERPRINT = "test-native-fingerprint"
VIENNAPS_VERSION = "4.7.0"


def _engine_capabilities(version: str = VIENNAPS_VERSION) -> EngineCapabilities:
    return EngineCapabilities(
        engine_id="viennaps",
        version=version,
        kind="native",
        backends=("cpu",),
    )


def _provider(version: str = VIENNAPS_VERSION, handle: Any = ps) -> StaticEngineProvider:
    return StaticEngineProvider(
        [StaticEngine(capabilities=_engine_capabilities(version), handle=handle, fingerprint=FINGERPRINT)]
    )


def _plane(dimension: int = 2) -> Any:
    ps.setDimension(dimension)
    if dimension == 2:
        domain = ps.Domain(
            gridDelta=GRID_DELTA,
            xExtent=X_EXTENT,
            boundary=ps.BoundaryType.REFLECTIVE_BOUNDARY,
        )
    else:
        domain = ps.Domain(
            gridDelta=GRID_DELTA,
            xExtent=X_EXTENT,
            yExtent=X_EXTENT,
            boundary=ps.BoundaryType.REFLECTIVE_BOUNDARY,
        )
    ps.MakePlane(domain, height=0.0, material=ps.Material.Si, addToExisting=False).apply()
    return domain


def _masked_trench() -> Any:
    ps.setDimension(2)
    domain = ps.Domain(
        gridDelta=GRID_DELTA,
        xExtent=X_EXTENT,
        boundary=ps.BoundaryType.REFLECTIVE_BOUNDARY,
    )
    ps.MakeTrench(
        domain=domain,
        trenchWidth=0.4,
        trenchDepth=0.4,
        trenchTaperAngle=0.0,
        maskHeight=0.3,
        maskTaperAngle=0.0,
        halfTrench=False,
        material=ps.Material.Si,
        maskMaterial=ps.Material.Mask,
    ).apply()
    return domain


def _material_order(domain: Any) -> tuple[int, ...]:
    mapping = domain.getMaterialMap()
    return tuple(int(mapping.getMaterialIdAtIdx(index)) for index in range(mapping.size()))


def _state(domain: Any, dimension: int = 2) -> ProcessState:
    order = _material_order(domain)
    return ProcessState(
        geometry=LevelSetState(
            handle=domain,
            dimension=dimension,
            grid_delta=GRID_DELTA,
            material_order=order,
        ),
        materials=MaterialState(order=order),
    )


def _context(
    dimension: int = 2,
    project: dict[str, Any] | None = None,
) -> ProcessContext:
    if project is None:
        project = {
            "schemaVersion": 3,
            "dimension": dimension,
            "geometry": {"kind": "plane", "substrateMaterial": "Si"},
            "solver": {"timeStepRatio": 0.25},
        }
    return ProcessContext(dimension=dimension, grid_delta=GRID_DELTA, project=project)


def _surface_points(domain: Any) -> np.ndarray:
    mesh = domain.getSurfaceMesh(addInterfaces=True, minNodeDistanceFactor=0.01)
    points = [[float(value) for value in node] for node in mesh.getNodes()]
    if not points:
        return np.zeros((0, 3), dtype=float)
    return np.asarray(points, dtype=float)


def _sorted_surface(domain: Any) -> np.ndarray:
    points = _surface_points(domain)
    if points.size == 0:
        return points
    return points[np.lexsort((points[:, 0], points[:, 1], points[:, 2]))]


def _top(domain: Any, dimension: int = 2) -> float:
    points = _surface_points(domain)
    if points.size == 0:
        raise AssertionError("domain has no surface nodes")
    vertical = 1 if dimension == 2 else 2
    return float(np.max(points[:, vertical]))


def _material_bounds(domain: Any, material_id: int) -> tuple[float, float] | None:
    """Return the (min, max) vertical extent of ``material_id`` surface cells."""
    mesh = domain.getSurfaceMesh(addInterfaces=True, minNodeDistanceFactor=0.01)
    points = [[float(value) for value in node] for node in mesh.getNodes()]
    lines = [[int(value) for value in row] for row in mesh.getLines()]
    data = mesh.getCellData()
    material_ids: list[int] = []
    for index in range(data.getScalarDataSize()):
        if str(data.getScalarDataLabel(index)) == "MaterialIds":
            material_ids = [int(value) for value in data.getScalarData(index)]
    verticals = [
        points[node][1] for cell, material in zip(lines, material_ids) if material == material_id for node in cell
    ]
    if not verticals:
        return None
    return min(verticals), max(verticals)


def _surface_digest(domain: Any) -> str:
    return hashlib.sha256(_sorted_surface(domain).tobytes()).hexdigest()


class IsotropicNativePluginTest(unittest.TestCase):
    def setUp(self) -> None:
        ps.setNumThreads(1)
        self.registry = ProcessRegistry()
        self.registration = register_isotropic(self.registry)

    def _params(self, **overrides: Any) -> IsotropicParams:
        values: dict[str, Any] = {"operation": "etch", "rateMagnitude": 0.3, "duration": 1.0}
        values.update(overrides)
        return IsotropicParams(**values)

    def _resolve(
        self,
        params: IsotropicParams,
        state: ProcessState,
        ctx: ProcessContext,
        version: str = VIENNAPS_VERSION,
    ) -> Any:
        request = ExecutionRequest(
            process_id="isotropic",
            variant_id="native",
            params=params,
            context=ctx,
            state=state,
        )
        return resolve_execution(request, self.registry, _provider(version))

    def _execute(
        self,
        params: IsotropicParams,
        domain: Any | None = None,
        dimension: int = 2,
        project: dict[str, Any] | None = None,
        ctx: ProcessContext | None = None,
    ) -> tuple[Any, ProcessState, StateDelta, Any]:
        state = _state(domain if domain is not None else _plane(dimension), dimension)
        ctx = ctx or _context(dimension, project)
        plan = self._resolve(params, state, ctx)
        state.engine = plan.engine
        delta = plan.variant.variant.execute(state, params, ctx)
        return plan, state, delta, state.geometry.handle

    # -- identity / registration ------------------------------------------

    def test_registration_exposes_stable_process_and_variant_identity(self) -> None:
        self.assertEqual(IsotropicSpec, IsotropicProcessSpec)
        self.assertEqual(IsotropicProcessSpec.id, "isotropic")
        self.assertEqual(IsotropicProcessSpec.version, "1.0.0")
        self.assertEqual(IsotropicProcessSpec.params_model, IsotropicParams)
        self.assertEqual(IsotropicProcessSpec.dimensions, (2, 3))
        self.assertEqual(IsotropicProcessSpec.operations, ("etch", "deposit"))

        variant = IsotropicNativeVariant()
        self.assertEqual(variant.variant_id, "native")
        self.assertEqual(variant.version, "1.0.0")
        self.assertEqual(variant.capabilities.dimensions, (2, 3))
        self.assertEqual(variant.capabilities.reads, frozenset({"geometry"}))
        self.assertEqual(variant.capabilities.writes, frozenset({"geometry"}))
        self.assertTrue(variant.capabilities.supports_masks)
        self.assertTrue(variant.capabilities.mutates_input)
        self.assertEqual(variant.capabilities.determinism, "strict")
        self.assertEqual(variant.engine.kind, "native")
        self.assertEqual(variant.engine.engine_id, "viennaps")
        self.assertEqual(variant.engine.min_version, "4.6.2")

        resolved = self.registry.get_variant("isotropic", "native")
        self.assertIsInstance(resolved.variant, VariantRegistration)
        self.assertEqual(resolved.variant.capabilities, variant.capabilities)
        self.assertEqual(len(resolved.implementation_hash), 64)
        descriptors = self.registry.list_variants("isotropic")
        self.assertEqual([descriptor.id for descriptor in descriptors], ["native"])
        self.assertEqual(descriptors[0].kind, "native")
        self.assertEqual(descriptors[0].engine_id, "viennaps")

    def test_params_reject_invalid_values(self) -> None:
        params = self._params()
        self.assertEqual(params.rateMode, "uniform")
        self.assertEqual(params.depositMaterial, "SiO2")
        self.assertIsNone(params.isotropicTimeStepRatio)

        invalid = (
            {"operation": "isotropic"},
            {"rateMagnitude": 0.0},
            {"rateMagnitude": float("inf")},
            {"duration": 0.0},
            {"duration": float("nan")},
            {"isotropicTimeStepRatio": 0.01},
            {"isotropicTimeStepRatio": 0.6},
            {"materialRates": {"Si": -0.1}},
            {"materialRates": {"Si": float("inf")}},
            {"maskMaterials": [""]},
            {"unexpected": 1},
        )
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValidationError):
                    self._params(**overrides)

    def test_validate_reports_rate_mode_and_mask_issues(self) -> None:
        variant = IsotropicNativeVariant()
        ctx = _context()

        empty_table = variant.validate(
            ctx,
            self._params(rateMode="material", materialRates={}),
        )
        self.assertTrue(
            any(issue.path == "materialRates" and issue.severity == "error" for issue in empty_table),
            empty_table,
        )

        uniform_table = variant.validate(ctx, self._params(materialRates={"Si": 0.1}))
        self.assertTrue(
            any(issue.path == "materialRates" and issue.severity == "error" for issue in uniform_table),
            uniform_table,
        )

        masks_in_material_mode = variant.validate(
            ctx,
            self._params(rateMode="material", materialRates={"Si": 0.1}, maskMaterials=["Mask"]),
        )
        self.assertEqual(len(masks_in_material_mode), 1)
        self.assertEqual(masks_in_material_mode[0].severity, "warning")
        self.assertTrue(
            all(issue.severity != "error" for issue in masks_in_material_mode),
            "material-mode masks must be a warning so resolution still succeeds",
        )

        self.assertEqual(variant.validate(ctx, self._params()), [])

    # -- execution ---------------------------------------------------------

    def test_etch_lowers_the_surface_by_rate_times_duration(self) -> None:
        domain = _plane(2)
        before = _top(domain, 2)
        plan, _state_after, delta, executed_domain = self._execute(self._params(), domain=domain)

        self.assertIs(executed_domain, domain)
        self.assertIsInstance(delta, StateDelta)
        self.assertIsInstance(delta.geometry, LevelSetState)
        self.assertIs(delta.geometry.handle, domain)
        self.assertEqual(delta.geometry.dimension, 2)
        self.assertEqual(delta.geometry.grid_delta, GRID_DELTA)
        self.assertEqual(delta.geometry.material_order, (int(ps.Material.Si),))

        after = _top(domain, 2)
        self.assertAlmostEqual(after - before, -0.3, delta=1e-6)

        self.assertEqual(delta.metadata["operation"], "etch")
        self.assertEqual(delta.metadata["rateMode"], "uniform")
        self.assertEqual(delta.metadata["requestedRate"], 0.3)
        self.assertEqual(delta.metadata["actualRate"], 0.3)
        self.assertAlmostEqual(delta.metadata["signedRate"], -0.3)
        self.assertEqual(delta.metadata["duration"], 1.0)
        self.assertEqual(delta.metadata["nativeClass"], "ps.IsotropicProcess")
        self.assertEqual(delta.metadata["fluxEngineType"], "CPU_DISK")
        self.assertEqual(delta.metadata["dimension"], 2)
        self.assertEqual(delta.metadata["timeStepRatio"], 0.25)
        self.assertEqual(delta.metadata["maskMaterials"], [])
        self.assertEqual(plan.engine.engine_id, "viennaps")

    def test_deposit_raises_the_surface_and_adds_sio2(self) -> None:
        domain = _plane(2)
        before = _top(domain, 2)
        params = self._params(operation="deposit", depositMaterial="SiO2")
        _plan, state, delta, _domain = self._execute(params, domain=domain)

        after = _top(domain, 2)
        self.assertAlmostEqual(after - before, 0.3, delta=1e-6)

        order = _material_order(domain)
        self.assertEqual(order, (int(ps.Material.Si), int(ps.Material.SiO2)))
        self.assertEqual(delta.geometry.material_order, order)
        self.assertEqual(delta.metadata["depositMaterial"], "SiO2")
        self.assertEqual(delta.metadata["levelSetCount"], 2)
        self.assertEqual(delta.metadata["materialMap"], list(order))

        # The deposited material is the new top level set, as in the worker path.
        self.assertEqual(domain.getNumberOfLevelSets(), 2)
        self.assertEqual(order[-1], int(ps.Material.SiO2))
        bounds = _material_bounds(domain, int(ps.Material.SiO2))
        self.assertIsNotNone(bounds)
        self.assertGreater(bounds[1], 0.2)

        # The committed delta only replaces geometry; the state materials merge
        # happens in the adapter, so the variant state keeps its original order.
        self.assertEqual(state.materials.order, (int(ps.Material.Si),))

    def test_material_rate_mode_moves_only_the_listed_material(self) -> None:
        domain = _masked_trench()
        mask_before = _material_bounds(domain, int(ps.Material.Mask))
        si_before = _material_bounds(domain, int(ps.Material.Si))
        self.assertIsNotNone(mask_before)
        self.assertIsNotNone(si_before)

        params = self._params(
            rateMagnitude=0.2,
            duration=0.3,
            rateMode="material",
            materialRates={"Si": 0.2},
        )
        _plan, _state, delta, _domain = self._execute(
            params,
            domain=domain,
            project={
                "schemaVersion": 3,
                "geometry": {"kind": "trench", "maskHeight": 0.3, "maskMaterial": "Mask"},
                "solver": {"timeStepRatio": 0.25},
            },
        )

        mask_after = _material_bounds(domain, int(ps.Material.Mask))
        si_after = _material_bounds(domain, int(ps.Material.Si))
        self.assertIsNotNone(mask_after)
        self.assertIsNotNone(si_after)

        self.assertAlmostEqual(mask_after[1], mask_before[1], delta=1e-6)
        self.assertAlmostEqual(si_after[0] - si_before[0], -0.2 * 0.3, delta=1e-6)
        self.assertEqual(delta.metadata["rateMode"], "material")
        self.assertEqual(delta.metadata["actualRate"], 0.2)
        self.assertEqual(delta.metadata["nativeDefaultRate"], 0.0)
        self.assertAlmostEqual(delta.metadata["nativeMaterialRates"]["Si"], -0.2)
        self.assertEqual(delta.metadata["maskMaterials"], [])

    def test_uniform_masks_come_from_the_project_geometry(self) -> None:
        domain = _masked_trench()
        mask_before = _material_bounds(domain, int(ps.Material.Mask))
        si_before = _material_bounds(domain, int(ps.Material.Si))
        self.assertIsNotNone(mask_before)
        self.assertIsNotNone(si_before)

        _plan, _state, delta, _domain = self._execute(
            self._params(rateMagnitude=0.2, duration=0.3),
            domain=domain,
            project={
                "schemaVersion": 3,
                "geometry": {"kind": "trench", "maskHeight": 0.3, "maskMaterial": "Mask"},
                "solver": {"timeStepRatio": 0.25},
            },
        )

        mask_after = _material_bounds(domain, int(ps.Material.Mask))
        si_after = _material_bounds(domain, int(ps.Material.Si))
        self.assertIsNotNone(mask_after)
        self.assertIsNotNone(si_after)
        self.assertEqual(delta.metadata["maskMaterials"], ["Mask"])
        self.assertAlmostEqual(mask_after[1], mask_before[1], delta=1e-6)
        self.assertAlmostEqual(si_after[0] - si_before[0], -0.2 * 0.3, delta=1e-6)

    def test_three_dimensional_plane_smoke(self) -> None:
        domain = _plane(3)
        before = _top(domain, 3)
        _plan, _state, delta, _domain = self._execute(
            self._params(rateMagnitude=0.2, duration=0.5),
            domain=domain,
            dimension=3,
            ctx=_context(3),
        )
        after = _top(domain, 3)
        self.assertAlmostEqual(after - before, -0.1, delta=1e-6)
        self.assertEqual(delta.geometry.dimension, 3)
        self.assertEqual(delta.metadata["dimension"], 3)

    # -- resolver admission ------------------------------------------------

    def test_missing_geometry_is_rejected(self) -> None:
        state = ProcessState(
            materials=MaterialState(order=(int(ps.Material.Si),)),
        )
        with self.assertRaises(ExecutionResolutionError) as raised:
            self._resolve(self._params(), state, _context())
        self.assertIn("geometry", str(raised.exception))

    def test_engine_version_mismatch_is_rejected_without_fallback(self) -> None:
        state = _state(_plane(2))
        with self.assertRaises(ExecutionResolutionError) as raised:
            self._resolve(self._params(), state, _context(), version="4.6.0")
        message = str(raised.exception)
        self.assertIn("viennaps", message)
        self.assertIn("4.6.2", message)
        self.assertIn("4.6.0", message)

        # A matching engine next to the mismatched one still resolves; there is
        # no fallback when only the mismatched engine exists (above).
        provider = StaticEngineProvider(
            [
                StaticEngine(capabilities=_engine_capabilities("4.6.0"), handle=ps, fingerprint="old"),
                StaticEngine(capabilities=_engine_capabilities(VIENNAPS_VERSION), handle=ps, fingerprint=FINGERPRINT),
            ]
        )
        plan = resolve_execution(
            ExecutionRequest(
                process_id="isotropic",
                variant_id="native",
                params=self._params(),
                context=_context(),
                state=state,
            ),
            self.registry,
            provider,
        )
        self.assertEqual(plan.engine.engine_version, VIENNAPS_VERSION)
        self.assertEqual(plan.engine_fingerprint, FINGERPRINT)

    def test_wrong_dimension_is_rejected_by_the_resolver(self) -> None:
        class TwoDimensionalIsotropic(IsotropicNativeVariant):
            variant_id = "native-2d"
            capabilities = VariantCapabilities(
                dimensions=(2,),
                reads=frozenset({"geometry"}),
                writes=frozenset({"geometry"}),
                supports_masks=True,
                mutates_input=True,
            )

        variant = TwoDimensionalIsotropic()
        self.registry.register_variant(
            VariantRegistration(
                process_id="isotropic",
                variant_id=variant.variant_id,
                version=variant.version,
                variant=variant,
                capabilities=variant.capabilities,
                engine=variant.engine,
                implementation_hash="test-2d-variant",
            )
        )

        domain = _plane(3)
        state = _state(domain, 3)
        request = ExecutionRequest(
            process_id="isotropic",
            variant_id="native-2d",
            params=self._params(),
            context=_context(3),
            state=state,
        )
        with self.assertRaises(ExecutionResolutionError) as raised:
            resolve_execution(request, self.registry, _provider())
        self.assertIn("dimension", str(raised.exception))

    def test_validation_errors_block_resolution(self) -> None:
        state = _state(_plane(2))
        request = ExecutionRequest(
            process_id="isotropic",
            variant_id="native",
            params=self._params(rateMode="material", materialRates={}),
            context=_context(),
            state=state,
        )
        with self.assertRaises(ExecutionResolutionError) as raised:
            resolve_execution(request, self.registry, _provider())
        self.assertIn("materialRates", str(raised.exception))

    # -- identity / determinism --------------------------------------------

    def test_plan_identity_is_stable_and_complete(self) -> None:
        domain = _plane(2)
        state = _state(domain)
        ctx = _context()
        params = self._params()
        first = self._resolve(params, state, ctx)
        second = self._resolve(params, state, ctx)

        identity = first.identity()
        self.assertEqual(identity, second.identity())
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
        self.assertEqual(identity["process_id"], "isotropic")
        self.assertEqual(identity["process_version"], "1.0.0")
        self.assertEqual(identity["variant_id"], "native")
        self.assertEqual(identity["variant_version"], "1.0.0")
        self.assertEqual(identity["engine_id"], "viennaps")
        self.assertEqual(identity["engine_version"], VIENNAPS_VERSION)
        self.assertEqual(identity["engine_fingerprint"], FINGERPRINT)
        self.assertEqual(identity["adapter"], "default-v2")
        self.assertEqual(len(identity["implementation_hash"]), 64)
        int(identity["implementation_hash"], 16)

    def test_execution_is_deterministic_on_fresh_domains(self) -> None:
        params = self._params(rateMagnitude=0.25, duration=0.4)
        ctx = _context()

        first_domain = _plane(2)
        first_plan = self._resolve(params, _state(first_domain), ctx)
        first_state = _state(first_domain)
        first_state.engine = first_plan.engine
        first_delta = first_plan.variant.variant.execute(first_state, params, ctx)

        second_domain = _plane(2)
        second_plan = self._resolve(params, _state(second_domain), ctx)
        second_state = _state(second_domain)
        second_state.engine = second_plan.engine
        second_delta = second_plan.variant.variant.execute(second_state, params, ctx)

        first_surface = _sorted_surface(first_domain)
        second_surface = _sorted_surface(second_domain)
        np.testing.assert_array_equal(first_surface, second_surface)
        self.assertEqual(_surface_digest(first_domain), _surface_digest(second_domain))
        self.assertEqual(first_delta.geometry.material_order, second_delta.geometry.material_order)
        self.assertEqual(first_delta.metadata, second_delta.metadata)
        self.assertEqual(first_plan.identity(), second_plan.identity())


if __name__ == "__main__":
    unittest.main()
