"""Strict version-one request models shared by API and storage."""

from __future__ import annotations

import math
import re
import hashlib
import json
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, field_validator, model_validator
from .materials import MaterialDefinition
from .runtime_limits import available_cpu_threads


def _finite_number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("must be a JSON number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("must be finite")
    return result


def _integer(value: object) -> int:
    if isinstance(value, bool) or type(value) is not int:
        raise ValueError("must be a JSON integer")
    return value


Number = Annotated[float, BeforeValidator(_finite_number)]
Integer = Annotated[int, BeforeValidator(_integer)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True)


class Units(StrictModel):
    length: Literal["um"]
    time: Literal["s"]
    rate: Literal["um/s"]
    angle: Literal["deg"]


# Minimum lateral 3D domain size in um. A domain must hold real structure
# (a 22 nm FinFET cell is 0.48 x 0.50 um), so the historical 1 um floor was an
# arbitrary UI limit, not a native requirement. 0.1 um still rejects a
# degenerate or zero-sized domain, and ``Domain3D`` additionally requires two
# grid intervals per axis below.
MIN_DOMAIN_EXTENT_3D = 0.1


class Domain(StrictModel):
    gridDelta: Annotated[Number, Field(gt=0, le=0.5)]
    xExtent: Annotated[Number, Field(ge=1, le=40)]
    xBoundary: Literal["reflective", "periodic"]


class PlaneGeometry(StrictModel):
    kind: Literal["plane"]
    height: Number
    substrateMaterial: Literal["Si"]


class TrenchGeometry(StrictModel):
    kind: Literal["trench"]
    width: Annotated[Number, Field(gt=0)]
    depth: Annotated[Number, Field(gt=0)]
    taperAngle: Annotated[Number, Field(ge=-45, le=45)]
    maskHeight: Annotated[Number, Field(ge=0)]
    maskTaperAngle: Annotated[Number, Field(ge=-45, le=45)]
    substrateMaterial: Literal["Si"]


Geometry = Annotated[Union[PlaneGeometry, TrenchGeometry], Field(discriminator="kind")]


class Step(StrictModel):
    id: Annotated[str, Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
    name: Annotated[str, Field(min_length=1, max_length=80)]
    enabled: bool
    operation: Literal["etch", "deposit"]
    model: Literal["isotropic", "singleParticle"]
    rateMagnitude: Annotated[Number, Field(ge=0, le=2)]
    duration: Annotated[Number, Field(ge=0.01, le=600)]
    depositMaterial: Literal["SiO2"] | None = None
    stickingProbability: Annotated[Number, Field(ge=0, le=1)] | None = None
    sourceExponent: Annotated[Number, Field(ge=0, le=20)] | None = None

    @model_validator(mode="after")
    def check_model_fields(self) -> "Step":
        if self.operation == "deposit" and self.depositMaterial != "SiO2":
            raise ValueError("deposit steps require depositMaterial=SiO2")
        if self.operation == "etch" and "depositMaterial" in self.model_fields_set:
            raise ValueError("depositMaterial applies only to deposit steps")
        particle_fields = (self.stickingProbability, self.sourceExponent)
        if self.model == "singleParticle" and any(value is None for value in particle_fields):
            raise ValueError("singleParticle requires stickingProbability and sourceExponent")
        if self.model == "isotropic" and ({"stickingProbability", "sourceExponent"} & self.model_fields_set):
            raise ValueError("particle parameters apply only to singleParticle")
        return self


class FixedRandom(StrictModel):
    mode: Literal["fixed"]
    seed: Annotated[Integer, Field(ge=0, le=4294967295)]


class UnseededRandom(StrictModel):
    mode: Literal["random"]
    seed: None = None

    @model_validator(mode="after")
    def omit_seed(self) -> "UnseededRandom":
        if "seed" in self.model_fields_set:
            raise ValueError("random mode must omit seed; the server generates it when a particle step runs")
        return self


RandomSettings = Annotated[Union[FixedRandom, UnseededRandom], Field(discriminator="mode")]


class Solver(StrictModel):
    timeStepRatio: Annotated[Number, Field(ge=0.05, le=0.5)]
    raysPerPoint: Annotated[Integer, Field(ge=16, le=4096)]
    cpuThreads: Annotated[Integer, Field(ge=1)]
    random: RandomSettings
    enginePreference: Literal["auto", "cpu", "gpu"]
    timeoutSeconds: Annotated[Integer, Field(ge=0, le=600)] = 0

    @field_validator("cpuThreads")
    @classmethod
    def cpu_threads_within_available_limit(cls, value: int) -> int:
        maximum = available_cpu_threads()
        if value > maximum:
            raise ValueError(f"must not exceed the {maximum} CPU threads available to this process")
        return value


class AdvectionSettingsV7(StrictModel):
    spatialScheme: Literal[
        "ENGQUIST_OSHER_1ST_ORDER", "ENGQUIST_OSHER_2ND_ORDER", "LAX_FRIEDRICHS_1ST_ORDER",
        "LAX_FRIEDRICHS_2ND_ORDER", "LOCAL_LAX_FRIEDRICHS_ANALYTICAL_1ST_ORDER",
        "LOCAL_LOCAL_LAX_FRIEDRICHS_1ST_ORDER", "LOCAL_LOCAL_LAX_FRIEDRICHS_2ND_ORDER",
        "LOCAL_LAX_FRIEDRICHS_1ST_ORDER", "LOCAL_LAX_FRIEDRICHS_2ND_ORDER",
        "STENCIL_LOCAL_LAX_FRIEDRICHS_1ST_ORDER", "WENO_3RD_ORDER", "WENO_5TH_ORDER",
    ] = "ENGQUIST_OSHER_1ST_ORDER"
    temporalScheme: Literal["FORWARD_EULER", "RUNGE_KUTTA_2ND_ORDER", "RUNGE_KUTTA_3RD_ORDER"] = "FORWARD_EULER"
    adaptiveTimeStepping: bool = False
    adaptiveTimeStepSubdivisions: Annotated[Integer, Field(ge=1, le=256)] = 20
    calculateIntermediateVelocities: bool = False
    checkDissipation: bool = True
    dissipationAlpha: Annotated[Number, Field(ge=0, le=10)] = 1.0
    ignoreVoids: bool = False
    velocityOutput: bool = False


class RayTracingSettingsV7(StrictModel):
    diskRadius: Annotated[Number, Field(ge=0, le=100)] = 0.0
    ignoreFluxBoundaries: bool = False
    maxBoundaryHits: Annotated[Integer, Field(ge=1, le=1000000)] = 1000
    maxReflections: Annotated[Integer, Field(ge=0, le=4294967295)] = 4294967295
    minNodeDistanceFactor: Annotated[Number, Field(ge=0, le=1)] = 0.05
    normalizationType: Literal["SOURCE", "MAX"] = "SOURCE"
    smoothingNeighbors: Annotated[Integer, Field(ge=0, le=1024)] = 1


class SolverV7(Solver):
    # Stable physical NVIDIA device UUID. Missing means host-preferred CUDA
    # device; older schema-1—6 projects remain unchanged and hash-compatible.
    gpuDeviceId: Annotated[str, Field(pattern=r"^GPU-[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")] | None = None
    advection: AdvectionSettingsV7 = Field(default_factory=AdvectionSettingsV7)
    rayTracing: RayTracingSettingsV7 = Field(default_factory=RayTracingSettingsV7)


class View(StrictModel):
    panX: Number
    panH: Number
    zoom: Annotated[Number, Field(gt=0, le=20)]
    showGrid: bool


class ProjectV1(StrictModel):
    schemaVersion: Literal[1]
    name: Annotated[str, Field(min_length=1, max_length=80)]
    dimension: Literal[2]
    units: Units
    domain: Domain
    geometry: Geometry
    steps: list[Step]
    solver: Solver
    view: View

    @model_validator(mode="after")
    def unique_step_ids(self) -> "ProjectV1":
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("step IDs must be unique")
        return self


class Domain3D(StrictModel):
    gridDelta: Annotated[Number, Field(gt=0, le=0.5)]
    xExtent: Annotated[Number, Field(ge=MIN_DOMAIN_EXTENT_3D, le=40)]
    yExtent: Annotated[Number, Field(ge=MIN_DOMAIN_EXTENT_3D, le=40)]
    boundaryXY: Literal["reflective", "periodic"]

    @model_validator(mode="after")
    def lateral_extent_spans_two_cells(self) -> "Domain3D":
        # Native mesh construction needs at least two cells per axis; keeping
        # the absolute floor low is only safe together with this check.
        minimum = 2 * float(self.gridDelta)
        smallest = min(float(self.xExtent), float(self.yExtent))
        if smallest < minimum:
            raise ValueError(f"xExtent and yExtent must span at least two grid intervals "
                             f"({minimum:g} um for gridDelta={float(self.gridDelta):g})")
        return self


def _select_dimension_domain(data: Any, model_class: type[BaseModel]) -> Any:
    """Validate ``domain`` against the branch selected by ``dimension``.

    ``domain`` is declared as ``Domain | Domain3D``. A plain union reports the
    errors of both branches, so a 3D payload also complains about missing 2D
    ``xBoundary``/``boundaryXY`` fields, which is noise for the caller. The
    branch is picked before validation from the declared dimension; the public
    JSON schema keeps the union and unchanged field names.
    """
    if isinstance(data, dict) and isinstance(data.get("domain"), dict):
        dimension = data.get("dimension")
        model = Domain3D if type(dimension) is int and dimension == 3 else (
            Domain if type(dimension) is int and dimension == 2 else None)
        if model is not None:
            try:
                return {**data, "domain": model.model_validate(data["domain"])}
            except ValidationError as exc:
                raise ValidationError.from_exception_data(
                    model_class.__name__,
                    [{**error, "loc": ("domain",) + tuple(error["loc"])} for error in exc.errors()],
                ) from None
    return data


class HoleGeometry(StrictModel):
    kind: Literal["hole"]
    radius: Annotated[Number, Field(gt=0)]
    depth: Annotated[Number, Field(gt=0)]
    taperAngle: Annotated[Number, Field(ge=-45, le=45)]
    maskHeight: Annotated[Number, Field(ge=0)]
    maskTaperAngle: Annotated[Number, Field(ge=-45, le=45)]
    substrateMaterial: Literal["Si"]


Vector3 = Annotated[list[Number], Field(min_length=3, max_length=3)]
Vector2 = Annotated[list[Number], Field(min_length=2, max_length=2)]
DirectionVector3 = Annotated[list[Annotated[Number, Field(ge=-1, le=1)]], Field(min_length=3, max_length=3)]


class Camera3D(StrictModel):
    position: Vector3
    focalPoint: Vector3
    viewUp: Vector3
    parallelScale: Annotated[Number, Field(gt=0)]

    @model_validator(mode="after")
    def nondegenerate(self) -> "Camera3D":
        direction = [a-b for a,b in zip(self.position,self.focalPoint)]
        cross = [direction[1]*self.viewUp[2]-direction[2]*self.viewUp[1], direction[2]*self.viewUp[0]-direction[0]*self.viewUp[2], direction[0]*self.viewUp[1]-direction[1]*self.viewUp[0]]
        if sum(x*x for x in cross) < 1e-24:
            raise ValueError("camera position, focalPoint and viewUp must define a camera frame")
        return self


class Clip3D(StrictModel):
    enabled: bool
    axis: Literal["x", "y", "z"]
    position: Number
    keep: Literal["positive", "negative"]


class Section3D(StrictModel):
    enabled: bool
    axis: Literal["x", "y", "z"]
    position: Number


class View3D(StrictModel):
    projection: Literal["parallel", "perspective"] = "parallel"
    camera: Camera3D | None = None
    displayMode: Literal["surface", "wireframe", "surfaceEdges"] = "surface"
    visibleMaterialIds: list[Integer] = Field(default_factory=lambda: [0,10,30], max_length=3)
    opacityByMaterial: dict[str, Annotated[Number, Field(ge=0, le=1)]] = Field(default_factory=lambda: {"0":1.,"10":1.,"30":1.})
    clip: Clip3D = Field(default_factory=lambda: Clip3D(enabled=False,axis="x",position=0.,keep="positive"))
    section: Section3D = Field(default_factory=lambda: Section3D(enabled=False,axis="y",position=0.))

    @model_validator(mode="after")
    def materials(self) -> "View3D":
        if any(x not in (0,10,30) for x in self.visibleMaterialIds) or any(x not in ("0","10","30") for x in self.opacityByMaterial):
            raise ValueError("view material IDs must be Mask=0, Si=10 or SiO2=30")
        return self


class ProjectV2(StrictModel):
    schemaVersion: Literal[2]
    name: Annotated[str, Field(min_length=1, max_length=80)]
    dimension: Literal[3]
    units: Units
    domain: Domain3D
    geometry: Annotated[Union[PlaneGeometry, TrenchGeometry, HoleGeometry], Field(discriminator="kind")]
    steps: list[Step]
    solver: Solver
    view3D: View3D

    @model_validator(mode="after")
    def unique_step_ids(self) -> "ProjectV2":
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("step IDs must be unique")
        return self


class View3DV3(StrictModel):
    projection: Literal["parallel", "perspective"] = "parallel"
    camera: Camera3D | None = None
    displayMode: Literal["surface", "wireframe", "surfaceEdges"] = "surface"
    visibleMaterialIds: list[Integer] = Field(default_factory=list, max_length=16)
    opacityByMaterial: dict[str, Annotated[Number, Field(ge=0, le=1)]] = Field(default_factory=dict)
    clip: Clip3D = Field(default_factory=lambda: Clip3D(enabled=False,axis="x",position=0.,keep="positive"))
    section: Section3D = Field(default_factory=lambda: Section3D(enabled=False,axis="y",position=0.))


class ProjectV3(StrictModel):
    schemaVersion: Literal[3]
    name: Annotated[str, Field(min_length=1, max_length=80)]
    dimension: Literal[2, 3]
    units: Units
    domain: Domain | Domain3D
    geometry: dict
    steps: list[dict]
    solver: Solver
    view: View | None = None
    view3D: View3DV3 | None = None
    materialDefinitions: Annotated[list[MaterialDefinition], Field(max_length=64)] | None = None

    @model_validator(mode="before")
    @classmethod
    def select_dimension_domain(cls, data):
        return _select_dimension_domain(data, cls)

    @model_validator(mode="after")
    def version3_shape(self):
        if self.dimension == 2 and (self.view is None or self.view3D is not None):
            raise ValueError("2D schemaVersion 3 requires view and forbids view3D")
        if self.dimension == 3 and (self.view3D is None or self.view is not None):
            raise ValueError("3D schemaVersion 3 requires view3D and forbids view")
        if self.dimension == 2 and not isinstance(self.domain, Domain):
            raise ValueError("2D schemaVersion 3 requires the 2D domain")
        if self.dimension == 3 and not isinstance(self.domain, Domain3D):
            raise ValueError("3D schemaVersion 3 requires the 3D domain")
        # V3 geometry and step unions are validated by the explicit strict
        # validators below; dict fields preserve the v1/v2 model byte shape.
        normalized_geometry, normalized_steps = validate_v3_project_parts(self.geometry, self.steps, self.dimension)
        # Normalize validated numbers/defaults once, without triggering the
        # validate_assignment hook recursively. Legacy contracts are untouched.
        object.__setattr__(self, "geometry", normalized_geometry)
        object.__setattr__(self, "steps", normalized_steps)
        return self


def validate_v3_project_parts(geometry, steps, dimension):
    from pydantic import TypeAdapter
    parsed_geometry = TypeAdapter(GeometryV3).validate_python(geometry)
    parsed = [StepV3.model_validate(item) for item in steps]
    ids = [item.id for item in parsed]
    if len(ids) != len(set(ids)):
        raise ValueError("step IDs must be unique")
    return parsed_geometry.model_dump(mode="json", exclude_none=True), [item.model_dump(mode="json", exclude_none=True) for item in parsed]


class PlaneV3(StrictModel):
    kind: Literal["plane"]
    height: Number
    substrateMaterial: str

class TrenchV3(StrictModel):
    kind: Literal["trench"]
    width: Annotated[Number, Field(gt=0)]
    depth: Annotated[Number, Field(gt=0)]
    taperAngle: Annotated[Number, Field(ge=-45, le=45)]
    maskHeight: Annotated[Number, Field(ge=0)]
    maskTaperAngle: Annotated[Number, Field(ge=-45, le=45)]
    substrateMaterial: str
    maskMaterial: str = "Mask"

class HoleV3(StrictModel):
    kind: Literal["hole"]
    radius: Annotated[Number, Field(gt=0)]
    depth: Annotated[Number, Field(gt=0)]
    taperAngle: Annotated[Number, Field(ge=-45, le=45)]
    maskHeight: Annotated[Number, Field(ge=0)]
    maskTaperAngle: Annotated[Number, Field(ge=-45, le=45)]
    substrateMaterial: str
    maskMaterial: str = "Mask"

class StackLayer(StrictModel):
    id: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
    name: Annotated[str, Field(min_length=1, max_length=80)]
    material: str
    thickness: Annotated[Number, Field(gt=0)]
    role: Literal["film", "mask"]
    source: Literal["gds-mask"] | None = None

class StackPattern(StrictModel):
    kind: Literal["none", "trench", "hole"]
    width: Annotated[Number, Field(gt=0)] | None = None
    radius: Annotated[Number, Field(gt=0)] | None = None
    taperAngle: Annotated[Number, Field(ge=-45, le=45)] | None = None

    @model_validator(mode="after")
    def pattern_shape(self):
        expected={"kind"} if self.kind in {"none", "gds"} else {"kind","width","taperAngle"} if self.kind=="trench" else {"kind","radius","taperAngle"}
        if self.model_fields_set!=expected:
            if self.kind in {"none", "gds"}: raise ValueError(f"{self.kind} pattern has no dimensions")
            if self.kind == "trench": raise ValueError("trench pattern requires exactly width and taperAngle")
            raise ValueError("hole pattern requires exactly radius and taperAngle")
        if self.kind == "trench" and (self.width is None or self.taperAngle is None):
            raise ValueError("trench pattern requires width and taperAngle")
        if self.kind == "hole" and (self.radius is None or self.taperAngle is None):
            raise ValueError("hole pattern requires radius and taperAngle")
        return self

class StackPatternV7(StackPattern):
    kind: Literal["none", "trench", "hole", "gds"]

class StackV3(StrictModel):
    kind: Literal["stack"]
    substrateMaterial: str
    baseHeight: Number = 0
    layers: Annotated[list[StackLayer], Field(max_length=16)]
    pattern: StackPattern

    @model_validator(mode="after")
    def stack_constraints(self):
        ids = [x.id for x in self.layers]
        if len(ids) != len(set(ids)): raise ValueError("stack layer IDs must be unique")
        if self.pattern.kind != "none" and (not self.layers or self.layers[-1].role != "mask"):
            raise ValueError("an opening requires the top layer to have role=mask")
        if not self.layers and self.pattern.kind != "none": raise ValueError("an empty stack only allows pattern none")
        gds_layers = [index for index, layer in enumerate(self.layers) if layer.source == "gds-mask"]
        if len(gds_layers) > 1 or (gds_layers and (gds_layers[0] != len(self.layers) - 1 or self.pattern.kind != "gds")):
            raise ValueError("a GDS mask must be the unique top layer with top pattern gds")
        if self.pattern.kind == "gds" and not gds_layers:
            raise ValueError("top pattern gds requires a GDS mask layer")
        return self

GeometryV3 = Annotated[Union[PlaneV3,TrenchV3,HoleV3,StackV3], Field(discriminator="kind")]


class FinExtrusionV7(StrictModel):
    extent: Vector2
    axis: Literal[1] = 1

    @model_validator(mode="after")
    def ordered_extent(self):
        if self.extent[0] >= self.extent[1]:
            raise ValueError("fin extrusion extent must have an increasing minimum and maximum")
        return self


class FinGeometryV7(StrictModel):
    kind: Literal["fin"]
    width: Annotated[Number, Field(gt=0)]
    height: Annotated[Number, Field(gt=0)]
    taperAngle: Annotated[Number, Field(ge=-45, le=45)] = 0
    maskHeight: Annotated[Number, Field(ge=0)] = 0
    maskTaperAngle: Annotated[Number, Field(ge=-45, le=45)] = 0
    substrateMaterial: str
    maskMaterial: str = "Mask"
    construction: Literal["native", "boolean"] = "native"
    extrusion: FinExtrusionV7 | None = None


class GDSLayerV7(StrictModel):
    layer: Annotated[Integer, Field(ge=0, le=65535)]
    material: str
    baseHeight: Number | None = None
    height: Annotated[Number, Field(gt=0)] | None = None
    mask: bool | None = None


class GDSMaskPatternV7(StrictModel):
    """A GDS polygon layer used as a physical mask over an existing 3D stack."""
    name: Annotated[str, Field(min_length=1, max_length=80)] = "GDS Mask"
    assetId: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    fileName: Annotated[str, Field(min_length=1, max_length=255, pattern=r"^[^/\\]+$")]
    structure: Annotated[str, Field(min_length=1, max_length=512)]
    layer: Annotated[Integer, Field(ge=0, le=65535)]
    datatype: Annotated[Integer, Field(ge=0, le=32767)] = 0
    unitScale: Annotated[Number, Field(gt=0, le=1000000)] = 1
    position: Vector3 = [0, 0, 0]
    baseHeight: Number = 1.2
    height: Annotated[Number, Field(gt=0)] = 0.3
    material: str = "Mask"
    retainedRegion: Literal["inside", "outside"] = "inside"
    protectsEtch: bool = True


class StackV7(StackV3):
    pattern: StackPatternV7
    gdsMask: GDSMaskPatternV7 | None = None


class GDSGeometryV7(StrictModel):
    kind: Literal["gds"]
    assetId: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    fileName: Annotated[str, Field(min_length=1, max_length=255, pattern=r"^[^/\\\\]+$")]
    structure: Annotated[str, Field(min_length=1, max_length=512)]
    unitScale: Annotated[Number, Field(gt=0, le=1000000)] = 1
    position: Vector3 = [0, 0, 0]
    layers: Annotated[list[GDSLayerV7], Field(min_length=1, max_length=64)]

    @model_validator(mode="after")
    def unique_layers(self):
        values = [row.layer for row in self.layers]
        if len(values) != len(set(values)):
            raise ValueError("GDS layer mappings must use unique layer IDs")
        return self


GeometryV7 = Annotated[Union[PlaneV3,TrenchV3,HoleV3,StackV7,FinGeometryV7,GDSGeometryV7], Field(discriminator="kind")]


def normalized_geometry_v3(project: object) -> dict:
    """Return the canonical native-geometry representation for schemas 1–7."""
    from pydantic import TypeAdapter

    raw = getattr(project, "geometry")
    geometry = raw if isinstance(raw, dict) else raw.model_dump(mode="json", exclude_none=True)
    geometry = dict(geometry)
    if geometry.get("kind") in ("trench", "hole"):
        geometry.setdefault("maskMaterial", "Mask")
    schema_version = getattr(project, "schemaVersion", 1)
    adapter = GeometryV7 if schema_version == 7 else GeometryV3
    normalized = TypeAdapter(adapter).validate_python(geometry).model_dump(mode="json", exclude_none=True)
    # Keep historical schema-7 project hashes stable when the Fin used the
    # original native MakeFin constructor (the default before this field).
    if normalized.get("kind") == "fin" and normalized.get("construction", "native") == "native":
        normalized.pop("construction", None)
    return normalized

class StepV3(StrictModel):
    id: Annotated[str, Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
    name: Annotated[str, Field(min_length=1, max_length=80)]
    enabled: bool
    operation: Literal["etch", "deposit"]
    model: Literal["isotropic", "singleParticle"]
    rateMagnitude: Annotated[Number, Field(ge=0, le=2)]
    duration: Annotated[Number, Field(ge=0.01, le=600)]
    depositMaterial: str | None = None
    stickingProbability: Annotated[Number, Field(ge=0, le=1)] | None = None
    sourceExponent: Annotated[Number, Field(ge=0, le=20)] | None = None

    @model_validator(mode="after")
    def validate_model_fields(self):
        if self.operation == "deposit" and self.depositMaterial is None: raise ValueError("deposit steps require depositMaterial")
        if self.operation == "etch" and "depositMaterial" in self.model_fields_set: raise ValueError("depositMaterial applies only to deposit steps")
        if self.model == "singleParticle" and (self.stickingProbability is None or self.sourceExponent is None): raise ValueError("singleParticle requires stickingProbability and sourceExponent")
        if self.model == "isotropic" and ({"stickingProbability","sourceExponent"} & self.model_fields_set): raise ValueError("particle parameters apply only to singleParticle")
        return self


class StepV4(StrictModel):
    id: Annotated[str, Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
    name: Annotated[str, Field(min_length=1, max_length=80)]
    enabled: bool
    operation: Literal["etch", "deposit"]
    model: Literal["isotropic", "singleParticle"]
    rateMagnitude: Annotated[Number, Field(ge=0, le=2)]
    duration: Annotated[Number, Field(ge=0.01, le=600)]
    depositMaterial: str | None = None
    stickingProbability: Annotated[Number, Field(ge=0, le=1)] | None = None
    sourceExponent: Annotated[Number, Field(ge=0, le=20)] | None = None
    rateMode: Literal["uniform", "material"] = "uniform"
    materialRates: dict[str, Annotated[Number, Field(ge=0, le=2)]] | None = None
    selectivityReference: Annotated[str, Field(min_length=1, max_length=80)] | None = None

    @model_validator(mode="after")
    def validate_rate_and_model_fields(self):
        if self.operation == "deposit" and self.depositMaterial is None:
            raise ValueError("deposit steps require depositMaterial")
        if self.operation == "etch" and "depositMaterial" in self.model_fields_set:
            raise ValueError("depositMaterial applies only to deposit steps")
        if self.model == "singleParticle" and (self.stickingProbability is None or self.sourceExponent is None):
            raise ValueError("singleParticle requires stickingProbability and sourceExponent")
        if self.model == "isotropic" and ({"stickingProbability", "sourceExponent"} & self.model_fields_set):
            raise ValueError("particle parameters apply only to singleParticle")
        if self.rateMode == "uniform" and "materialRates" in self.model_fields_set:
            raise ValueError("uniform rateMode forbids materialRates")
        if self.rateMode == "material":
            if self.operation != "etch":
                raise ValueError("material rateMode is supported only for etch")
            if "materialRates" not in self.model_fields_set or self.materialRates is None:
                raise ValueError("material rateMode requires materialRates, which may be empty")
        return self


class ProjectV4(StrictModel):
    schemaVersion: Literal[4]
    name: Annotated[str, Field(min_length=1, max_length=80)]
    dimension: Literal[2, 3]
    units: Units
    domain: Domain | Domain3D
    geometry: dict
    steps: list[StepV4]
    solver: Solver
    view: View | None = None
    view3D: View3DV3 | None = None
    materialDefinitions: Annotated[list[MaterialDefinition], Field(max_length=64)] | None = None

    @model_validator(mode="before")
    @classmethod
    def select_dimension_domain(cls, data):
        return _select_dimension_domain(data, cls)

    @model_validator(mode="after")
    def version4_shape(self):
        if self.dimension == 2 and (self.view is None or self.view3D is not None or not isinstance(self.domain,Domain)):
            raise ValueError("2D schemaVersion 4 requires 2D domain and view only")
        if self.dimension == 3 and (self.view3D is None or self.view is not None or not isinstance(self.domain,Domain3D)):
            raise ValueError("3D schemaVersion 4 requires 3D domain and view3D only")
        from pydantic import TypeAdapter
        geometry=TypeAdapter(GeometryV3).validate_python(self.geometry)
        object.__setattr__(self,"geometry",geometry.model_dump(mode="json",exclude_none=True))
        ids=[step.id for step in self.steps]
        if len(ids)!=len(set(ids)): raise ValueError("step IDs must be unique")
        return self

Project = ProjectV1  # frozen v1 public Python API


def _directional_rate_set_directions(step: "DirectionalStepV5") -> list[tuple[int | None, list[float]]]:
    """Return the per-set (index, direction) pairs a 2D project must check."""
    if step.rateSets is not None:
        return [(index, rate_set.direction) for index, rate_set in enumerate(step.rateSets)]
    return [(None, step.direction)]


# A 2D direction is projected onto [x, h] by the schema and by the worker's
# normalization. Checking the raw third component with an absolute tolerance is
# not enough: [0, -1e-12, 1e-12] passes an absolute <= 1e-12 test but normalizes
# to [0, -0.707, 0.707], i.e. a mostly out-of-plane beam. Compare the normalized
# third component instead so the tolerance is relative to the vector length.
PLANAR_DIRECTION_TOLERANCE = 1e-12


def normalized_out_of_plane_component(direction: list[float]) -> float:
    length = math.sqrt(sum(float(value) ** 2 for value in direction))
    if length <= 0.0:
        return 0.0
    return abs(float(direction[2])) / length


class DirectionalRateSetV5(StrictModel):
    """One real ``viennaps.RateSet`` of the native multi-set overload.

    ``directionalVelocity`` and ``isotropicVelocity`` are non-negative rate
    magnitudes in um/s, exactly like the step-level scalar fields: the step
    ``operation`` selects the native sign (etch negative, deposit positive).
    The name follows the native RateSet property and is not a signed raw value.
    """

    direction: DirectionVector3
    directionalVelocity: Annotated[Number, Field(ge=0, le=2)]
    isotropicVelocity: Annotated[Number, Field(ge=0, le=2)] = 0.0
    # ``None`` means "derive from the project geometry", the same protected
    # materials the scalar directional path uses. An explicit list (including
    # the empty list) is honoured verbatim, so a set can only skip protection
    # by naming that intent explicitly.
    maskMaterials: Annotated[list[Annotated[str, Field(min_length=1, max_length=80)]], Field(max_length=16)] | None = None
    calculateVisibility: bool = True

    @model_validator(mode="after")
    def validate_rate_set(self):
        if math.sqrt(sum(float(value) ** 2 for value in self.direction)) <= 1e-12:
            raise ValueError("rateSets direction must be a nonzero 3-component vector")
        if self.maskMaterials is not None and len(set(self.maskMaterials)) != len(self.maskMaterials):
            raise ValueError("rateSets maskMaterials must not contain duplicate material names")
        return self


class DirectionalStepV5(StrictModel):
    id: Annotated[str, Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
    name: Annotated[str, Field(min_length=1, max_length=80)]
    enabled: bool
    operation: Literal["etch", "deposit"]
    model: Literal["directional"]
    # Scalar mode keeps the historical required fields. ``None`` rather than a
    # default keeps them out of ``model_dump(exclude_none=True)`` for the
    # additive ``rateSets`` mode, so every existing request hash is unchanged.
    direction: DirectionVector3 | None = None
    directionalRateMagnitude: Annotated[Number, Field(ge=0, le=2)] | None = None
    isotropicRateMagnitude: Annotated[Number, Field(ge=0, le=2)] | None = None
    calculateVisibility: bool = True
    # Native DirectionalProcess(rateSets: Sequence[RateSet]). Bounded because
    # every set with a directed component costs one native visibility pass.
    rateSets: Annotated[list[DirectionalRateSetV5], Field(min_length=1, max_length=8)] | None = None
    duration: Annotated[Number, Field(ge=0.01, le=600)]
    depositMaterial: str | None = None

    @model_validator(mode="after")
    def validate_directional_fields(self):
        scalar_fields = ("direction", "directionalRateMagnitude", "isotropicRateMagnitude")
        if self.rateSets is None:
            if any(getattr(self, name) is None for name in scalar_fields):
                raise ValueError(
                    "directional steps require direction, directionalRateMagnitude and "
                    "isotropicRateMagnitude unless rateSets is used")
            if math.sqrt(sum(float(value) ** 2 for value in self.direction)) <= 1e-12:
                raise ValueError("direction must be a nonzero 3-component vector")
        else:
            if any(name in self.model_fields_set for name in scalar_fields):
                raise ValueError(
                    "rateSets is mutually exclusive with direction, directionalRateMagnitude "
                    "and isotropicRateMagnitude: omit all three fields in rateSets mode; send them "
                    "only for scalar directional steps, never as 0 placeholders")
            if self.calculateVisibility is not True:
                raise ValueError(
                    "rateSets uses per-set calculateVisibility; omit the step-level flag "
                    "instead of materializing it (or leave it true)")
        if self.operation == "deposit" and self.depositMaterial is None:
            raise ValueError("deposit steps require depositMaterial")
        if self.operation == "etch" and "depositMaterial" in self.model_fields_set:
            raise ValueError("depositMaterial applies only to deposit steps")
        return self


class RemoveMaterialStepV5(StrictModel):
    id: Annotated[str, Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
    name: Annotated[str, Field(min_length=1, max_length=80)]
    enabled: bool
    model: Literal["removeMaterial"]
    material: Annotated[str, Field(min_length=1, max_length=80)]


class PlanarizeStepV5(StrictModel):
    id: Annotated[str, Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
    name: Annotated[str, Field(min_length=1, max_length=80)]
    enabled: bool
    model: Literal["planarize"]
    # A planarize step either cuts at an explicit height (the historical shape,
    # unchanged so old projects and their request hashes stay valid) or derives
    # that height at run time from the measured top of one material.
    # ``None`` rather than 0 keeps both new fields out of
    # ``model_dump(exclude_none=True)``, which is the canonical request hash
    # input, for every step that does not use them.
    cutoffHeight: Number | None = None
    stopMaterial: Annotated[str, Field(min_length=1, max_length=80)] | None = None
    overPolish: Annotated[Number, Field(ge=0, le=100)] | None = None

    @model_validator(mode="after")
    def validate_cutoff_source(self):
        if (self.cutoffHeight is None) == (self.stopMaterial is None):
            raise ValueError("planarize requires exactly one of cutoffHeight or stopMaterial")
        if self.overPolish is not None and self.stopMaterial is None:
            raise ValueError("overPolish applies only to stopMaterial planarize steps")
        return self


class BooleanStepV5(StrictModel):
    id: Annotated[str, Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
    name: Annotated[str, Field(min_length=1, max_length=80)]
    enabled: bool
    model: Literal["boolean"]
    booleanOperation: Literal["union", "intersection", "difference"]
    # Position is [x,h,0] in 2D and [x,y,z] in 3D; z is vertical in 3D.
    position: Vector3
    width: Annotated[Number, Field(gt=0, le=40)]
    height: Annotated[Number, Field(gt=0, le=100)]
    depth: Annotated[Number, Field(gt=0, le=40)] | None = None
    angle: Annotated[Number, Field(ge=-45, le=45)] = 0.0
    applyToAll: bool = True


StepV5 = Annotated[Union[StepV4, DirectionalStepV5, RemoveMaterialStepV5, PlanarizeStepV5, BooleanStepV5], Field(discriminator="model")]


ParticleLabel = Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")]
Probability = Annotated[Number, Field(ge=0, le=1)]
RateCoefficient = Annotated[Number, Field(ge=0, le=2)]


class IonParticleV7(StrictModel):
    kind: Literal["ion"]
    label: ParticleLabel
    sourcePower: Annotated[Number, Field(ge=0, le=20)]
    thetaRMin: Annotated[Number, Field(ge=0, le=90)] = 0
    thetaRMax: Annotated[Number, Field(ge=0, le=90)] = 90
    minAngle: Annotated[Number, Field(ge=0, le=90)] = 80
    B_sp: Annotated[Number, Field(ge=-1, le=20)] = -1
    meanEnergy: Annotated[Number, Field(ge=0, le=1_000_000)] = 0
    sigmaEnergy: Annotated[Number, Field(ge=0, le=1_000_000)] = 0
    thresholdEnergy: Annotated[Number, Field(ge=0, le=1_000_000)] = 0
    inflectAngle: Annotated[Number, Field(ge=0, le=90)] = 0
    n: Annotated[Integer, Field(ge=1, le=100)] = 1

    @model_validator(mode="after")
    def check_ion_angles(self):
        if self.thetaRMin > self.thetaRMax:
            raise ValueError("thetaRMin must not exceed thetaRMax")
        return self


class NeutralParticleV7(StrictModel):
    kind: Literal["neutral"]
    label: ParticleLabel
    defaultStickingProbability: Probability = 1
    stickingProbabilityByMaterial: dict[str, Probability] = Field(default_factory=dict)


ParticleSpeciesV7 = Annotated[Union[IonParticleV7, NeutralParticleV7], Field(discriminator="kind")]


class MultiParticleRateCoefficientsV7(StrictModel):
    # Returned native velocity = operation sign * sum(flux[i] * coefficient[i]).
    # Native flux units are not declared by the installed binding; coefficients
    # are therefore explicit conversion factors supplied by the project author.
    defaultBySpecies: dict[ParticleLabel, RateCoefficient]
    byMaterial: dict[str, dict[ParticleLabel, RateCoefficient]] = Field(default_factory=dict)


class MultiParticleStepV7(StrictModel):
    id: Annotated[str, Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
    name: Annotated[str, Field(min_length=1, max_length=80)]
    enabled: bool
    operation: Literal["etch", "deposit"]
    model: Literal["multiParticle"]
    duration: Annotated[Number, Field(ge=0.01, le=600)]
    depositMaterial: str | None = None
    species: Annotated[list[ParticleSpeciesV7], Field(min_length=1, max_length=8)]
    rateCoefficients: MultiParticleRateCoefficientsV7

    @model_validator(mode="after")
    def check_multi_particle_fields(self):
        if self.operation == "deposit" and self.depositMaterial is None:
            raise ValueError("multi-particle deposit steps require depositMaterial")
        if self.operation == "etch" and "depositMaterial" in self.model_fields_set:
            raise ValueError("depositMaterial applies only to deposit steps")
        labels = [species.label for species in self.species]
        if len(labels) != len(set(labels)):
            raise ValueError("multi-particle species labels must be unique")
        label_set = set(labels)
        if set(self.rateCoefficients.defaultBySpecies) != label_set:
            raise ValueError("defaultBySpecies must contain exactly one coefficient per species label")
        for material, coefficients in self.rateCoefficients.byMaterial.items():
            if not material or set(coefficients) - label_set:
                raise ValueError(f"material rate coefficients for {material!r} contain an unknown species label")
        return self


class SurfaceDiffusionV7(StrictModel):
    """Native ``SurfaceDiffusionParameters`` smoothing of ALD coverage.

    ViennaPS 4.6.2 builds this Laplacian stencil only inside the ALP strategy,
    and the ALP flag is set solely by a positive
    ``SingleParticleALDParams.coverageDiffusionCoefficient``. It therefore
    smooths the scalar coverage field (which drives the ALD growth rate), not
    the level-set geometry, and is accepted only on ``specialized:ald`` steps.
    Defaults are the installed native struct defaults, so materializing them is
    a behaviour no-op relative to omitting the object.
    """

    enabled: bool = True
    kNeighbors: Annotated[Integer, Field(ge=1, le=64)] = 16
    normalCutoff: Annotated[Number, Field(ge=-1, le=1)] = 0.25
    normalizeByLocalScale: bool = True
    radius: Annotated[Number, Field(ge=0, le=2)] = 0.0
    sigmaNormal: Annotated[Number, Field(gt=0, le=10)] = 0.35
    stabilityFactor: Annotated[Number, Field(gt=0, le=1)] = 1.0
    symmetrizeWeights: bool = True

    @model_validator(mode="after")
    def check_disabled_matches_defaults(self) -> "SurfaceDiffusionV7":
        if not self.enabled:
            defaults = type(self)()
            overridden = sorted(
                key for key in self.model_fields_set
                if key != "enabled" and getattr(self, key) != getattr(defaults, key))
            if overridden:
                raise ValueError(
                    "disabled surfaceDiffusion must not override "
                    f"{overridden}; omit the object instead")
        return self


class SpecializedStepV7(StrictModel):
    id: Annotated[str, Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
    name: Annotated[str, Field(min_length=1, max_length=80)]
    enabled: bool
    operation: Literal["etch", "deposit", "native"]
    model: Literal["specialized"]
    nativeModel: Annotated[str, Field(min_length=1, max_length=40)]
    duration: Annotated[Number, Field(ge=0.01, le=600)] | None = None
    depositMaterial: str | None = None
    nativeParameters: dict[str, Any]
    surfaceDiffusion: SurfaceDiffusionV7 | None = None

    @model_validator(mode="after")
    def check_specialized_shape(self):
        from .specialized_models import model_spec
        spec = model_spec(self.nativeModel)
        if spec is None:
            raise ValueError(f"unknown installed ViennaPS specialized model {self.nativeModel!r}")
        if self.operation != spec["operation"]:
            raise ValueError(f"{self.nativeModel} requires operation={spec['operation']!r}")
        if self.nativeModel == "oxidation":
            if self.duration is not None:
                raise ValueError("oxidation duration comes from nativeParameters.timeHours; omit duration")
        elif self.duration is None:
            raise ValueError(f"{self.nativeModel} requires a physical process duration in seconds")
        expected_material = spec.get("depositMaterial")
        if expected_material is not None and self.depositMaterial != expected_material:
            raise ValueError(f"{self.nativeModel} deposits {expected_material}")
        if spec.get("depositMaterialSelectable") and (not isinstance(self.depositMaterial, str) or not self.depositMaterial):
            raise ValueError(f"{self.nativeModel} requires an output material")
        if spec.get("operation") != "deposit" and "depositMaterial" in self.model_fields_set:
            raise ValueError("depositMaterial applies only to a native deposition model")
        expected_parameters = {row["key"] for row in spec["fields"]}
        optional_parameters = set(spec.get("optionalKeys", ()))
        if set(self.nativeParameters) - optional_parameters != expected_parameters:
            raise ValueError(f"{self.nativeModel} nativeParameters must contain exactly {sorted(expected_parameters)}"
                             + (f" (optional: {sorted(optional_parameters)})" if optional_parameters else ""))
        if self.surfaceDiffusion is not None:
            # Placement is ALD-only whenever the object exists, including a
            # disabled token: there is no other 4.6.2 process with a native
            # surface-diffusion flag, so accepting it elsewhere would make the
            # schema claim a capability the engine does not have.
            if self.nativeModel != "ald":
                raise ValueError(
                    "surfaceDiffusion is only available for specialized:ald; "
                    f"{self.nativeModel} has no native surface-diffusion flag")
            if self.surfaceDiffusion.enabled:
                # ViennaPS 4.6.2 only builds the stencil when the ALD surface
                # model reports a positive coverage diffusion coefficient;
                # a disabled token is inert and may keep a zero coefficient.
                coefficient = self.nativeParameters.get("coverageDiffusionCoefficient")
                try:
                    finite = isinstance(coefficient, (int, float)) and not isinstance(coefficient, bool) \
                        and math.isfinite(float(coefficient)) and float(coefficient) > 0.0
                except (TypeError, ValueError):
                    finite = False
                if not finite:
                    raise ValueError(
                        "enabled surfaceDiffusion requires nativeParameters.coverageDiffusionCoefficient > 0; "
                        "ViennaPS 4.6.2 never builds the stencil for zero")
        return self


class GdsBooleanMaskV7(StrictModel):
    """One selected GDS layer/datatype used as a boolean operand.

    The geometry kind ``gds`` geometry owns the asset; this step re-reads that
    same asset and layer, so there is no second upload path. ``position`` is an
    additional XY placement (and a z offset added to ``baseHeight``) relative
    to the geometry placement, so the zero default is exactly aligned.
    """

    layer: Annotated[Integer, Field(ge=0, le=65535)]
    datatype: Annotated[Integer, Field(ge=0, le=32767)] = 0
    unitScale: Annotated[Number, Field(gt=0, le=1000000)] = 1
    position: Vector3 = [0, 0, 0]
    baseHeight: Annotated[Number, Field(ge=0)]
    height: Annotated[Number, Field(gt=0)]
    retainedRegion: Literal["inside", "outside"] = "inside"


class GdsBooleanStepV7(StrictModel):
    """A boolean cut/add whose operand is a real 3D mask built from a GDS layer."""

    id: Annotated[str, Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
    name: Annotated[str, Field(min_length=1, max_length=80)]
    enabled: bool
    model: Literal["gdsBoolean"]
    booleanOperation: Literal["union", "intersection", "difference"]
    gdsMask: GdsBooleanMaskV7
    applyToAll: bool = True


class CsvProcessStepV7(StrictModel):
    """Native ``ps.CSVFileProcess`` with an uploaded ``RateGrid`` table.

    ``operation`` is ``native`` because the installed model has no etch/deposit
    flag: the sign of every CSV rate chooses the direction of material motion
    for the selected direction vector (negative rates etch, positive rates
    deposit for a downward direction). ``interpolationMode`` is mandatory.
    ``CUSTOM`` and ``setCustomInterpolator`` are deliberately not expressible.
    """

    id: Annotated[str, Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
    name: Annotated[str, Field(min_length=1, max_length=80)]
    enabled: bool
    operation: Literal["native"]
    model: Literal["csvProcess"]
    duration: Annotated[Number, Field(ge=0.01, le=600)]
    assetId: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    fileName: Annotated[str, Field(min_length=1, max_length=255, pattern=r"^[^/\\\x00-\x1f\x7f]+$")]
    direction: DirectionVector3
    offset: Vector2
    isotropicComponent: Annotated[Number, Field(ge=0, le=100)] = 0.0
    directionalComponent: Annotated[Number, Field(ge=0, le=100)] = 1.0
    interpolationMode: Literal["LINEAR", "IDW"]
    idwNeighbors: Annotated[Integer, Field(ge=1, le=64)] | None = None
    calculateVisibility: bool = True

    @model_validator(mode="after")
    def check_csv_process_fields(self) -> "CsvProcessStepV7":
        if math.sqrt(sum(float(value) ** 2 for value in self.direction)) <= 1e-12:
            raise ValueError("csvProcess requires a nonzero direction vector")
        if any(abs(float(value)) > 1e6 for value in self.offset):
            raise ValueError("csvProcess offset must stay within ±1,000,000 um")
        if float(self.isotropicComponent) == 0.0 and float(self.directionalComponent) == 0.0:
            raise ValueError("csvProcess requires a nonzero isotropicComponent or directionalComponent")
        if self.interpolationMode == "IDW" and self.idwNeighbors is None:
            raise ValueError("csvProcess IDW interpolation requires idwNeighbors")
        if self.interpolationMode == "LINEAR" and self.idwNeighbors is not None:
            raise ValueError("idwNeighbors applies only to IDW interpolation")
        return self


StepV7 = Annotated[Union[StepV5, MultiParticleStepV7, SpecializedStepV7, GdsBooleanStepV7, CsvProcessStepV7], Field(discriminator="model")]


class DomainStateRef(StrictModel):
    formatVersion: Literal[1]
    stateId: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    structureHash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    dimension: Literal[2, 3]
    gridDelta: Annotated[Number, Field(gt=0)]
    xExtent: Annotated[Number, Field(gt=0)]
    yExtent: Annotated[Number, Field(gt=0)] | None = None
    boundary: Literal["reflective", "periodic"]
    boundaryConditions: Annotated[list[Literal["reflective", "periodic", "infinite"]], Field(min_length=2, max_length=3)]
    materialIdsByLevelSetIndex: Annotated[list[Integer], Field(min_length=1, max_length=64)]
    levelSetCount: Annotated[Integer, Field(ge=1, le=64)]
    viennapsVersion: Annotated[str, Field(min_length=1, max_length=32)]
    viennalsVersion: Annotated[str, Field(min_length=1, max_length=32)]
    sourceRunId: Annotated[str, Field(pattern=r"^[a-f0-9]{24}$")] | None = None
    sourceStepId: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")] | None = None

    @model_validator(mode="after")
    def state_shape(self):
        if (self.sourceRunId is None) != (self.sourceStepId is None):
            raise ValueError("sourceRunId and sourceStepId must both be present or both be omitted")
        if self.dimension == 2 and self.boundaryConditions != [self.boundary, "infinite"]:
            raise ValueError("2D native state boundary conditions must match the project and infinite height")
        if self.dimension == 3 and self.boundaryConditions != [self.boundary, self.boundary, "infinite"]:
            raise ValueError("3D native state boundary conditions must match the project lateral boundaries and infinite height")
        return self


def project_structure_hash(dimension: int, units: Units, domain: Domain | Domain3D, geometry: dict) -> str:
    payload = {"dimension": dimension, "units": units.model_dump(mode="json"),
               "domain": domain.model_dump(mode="json"), "geometry": geometry}
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class ProjectV6(StrictModel):
    schemaVersion: Literal[6]
    name: Annotated[str, Field(min_length=1, max_length=80)]
    dimension: Literal[2, 3]
    units: Units
    domain: Domain | Domain3D
    geometry: dict
    steps: list[StepV5]
    solver: Solver
    view: View | None = None
    view3D: View3DV3 | None = None
    continuationState: DomainStateRef | None = None
    materialDefinitions: Annotated[list[MaterialDefinition], Field(max_length=64)] | None = None

    @model_validator(mode="before")
    @classmethod
    def select_dimension_domain(cls, data):
        return _select_dimension_domain(data, cls)

    @model_validator(mode="after")
    def version6_shape(self):
        if self.dimension == 2 and (self.view is None or self.view3D is not None or not isinstance(self.domain, Domain)):
            raise ValueError("2D schemaVersion 6 requires 2D domain and view only")
        if self.dimension == 3 and (self.view3D is None or self.view is not None or not isinstance(self.domain, Domain3D)):
            raise ValueError("3D schemaVersion 6 requires 3D domain and view3D only")
        from pydantic import TypeAdapter
        geometry_adapter = GeometryV7 if self.schemaVersion == 7 else GeometryV3
        geometry = TypeAdapter(geometry_adapter).validate_python(self.geometry)
        normalized_geometry = geometry.model_dump(mode="json", exclude_none=True)
        object.__setattr__(self, "geometry", normalized_geometry)
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("step IDs must be unique")
        for index, step in enumerate(self.steps):
            if isinstance(step, DirectionalStepV5) and self.dimension == 2:
                for set_index, direction in _directional_rate_set_directions(step):
                    out_of_plane = normalized_out_of_plane_component(direction)
                    if out_of_plane > PLANAR_DIRECTION_TOLERANCE:
                        path = f"{index}" if set_index is None else f"{index}.rateSets[{set_index}]"
                        raise ValueError(
                            f"2D directional step {path} must use [x, h, 0]; its third direction component must be zero "
                            f"after normalization (raw {direction[2]!r} gives |z|={out_of_plane:.6g} on the unit vector)")
            if isinstance(step, BooleanStepV5):
                if self.dimension == 2 and (abs(step.position[2]) > 1e-12 or step.depth is not None):
                    raise ValueError(f"2D boolean step {index} requires position [x, h, 0] and no depth")
                if self.dimension == 3 and step.depth is None:
                    raise ValueError(f"3D boolean step {index} requires depth")
            if isinstance(step, CsvProcessStepV7) and self.dimension == 2:
                # ``ps.CSVFileProcess`` normalizes a 3-component direction in
                # both dimensions, but the 2D RateGrid/visibility path only has
                # an x/h plane; a nonzero third component has no native meaning.
                if abs(step.direction[2]) > 1e-12:
                    raise ValueError(f"2D csvProcess step {index} must use direction [x, h, 0]")
                # RateGrid::interpolateLinear/IDW use only offset[0] when D == 2.
                if abs(step.offset[1]) > 1e-12:
                    raise ValueError(f"2D csvProcess step {index} must use offset [x, 0]; the native 2D RateGrid has no second offset axis")
        state = self.continuationState
        if state is not None:
            boundary = self.domain.xBoundary if self.dimension == 2 else self.domain.boundaryXY
            if state.dimension != self.dimension or not math.isclose(state.gridDelta, self.domain.gridDelta, rel_tol=0.0, abs_tol=1e-12):
                raise ValueError("continuation state dimension and gridDelta must match the project")
            if not math.isclose(state.xExtent, self.domain.xExtent, rel_tol=0.0, abs_tol=1e-12) or state.boundary != boundary:
                raise ValueError("continuation state x extent and boundary must match the project")
            if self.dimension == 2 and state.yExtent is not None:
                raise ValueError("2D continuation state must omit yExtent")
            if self.dimension == 3 and (state.yExtent is None or not math.isclose(state.yExtent, self.domain.yExtent, rel_tol=0.0, abs_tol=1e-12)):
                raise ValueError("3D continuation state y extent must match the project")
            if state.structureHash != project_structure_hash(self.dimension, self.units, self.domain, normalized_geometry):
                raise ValueError("continuation state geometry and domain fingerprint does not match the project")
            if len(state.materialIdsByLevelSetIndex) != state.levelSetCount:
                raise ValueError("continuation state material map length is invalid")
        return self


class OutputSettingsV7(StrictModel):
    """Native result artifacts; the surface mesh remains the viewer's required result."""

    levelSets: bool = False
    levelSetMeshes: bool = False
    surfaceMeshVtk: bool = False
    volumeMesh: bool = False
    nativeMetadata: bool = False


class ProjectV7(ProjectV6):
    """Schema 7 is the unified extension point for advanced native capabilities."""

    schemaVersion: Literal[7]
    solver: SolverV7
    steps: list[StepV7]
    outputSettings: OutputSettingsV7 | None = None

    @model_validator(mode="after")
    def version7_geometry(self):
        geometry = self.geometry
        delta = self.domain.gridDelta
        if geometry["kind"] == "fin":
            if geometry["width"] < 2 * delta or geometry["height"] < 2 * delta:
                raise ValueError("fin width and height must span at least two grid intervals")
            if geometry["maskHeight"] > 0 and geometry["maskHeight"] < 2 * delta:
                raise ValueError("fin maskHeight must span at least two grid intervals or be zero")
            if self.dimension == 2 and geometry.get("extrusion") is not None:
                raise ValueError("Fin extrusion is only available for a 3D project")
            if geometry.get("extrusion") is not None:
                start, end = geometry["extrusion"]["extent"]
                if end - start < 2 * delta:
                    raise ValueError("fin extrusion extent must span at least two grid intervals")
                if min(abs(start), abs(end)) > self.domain.yExtent / 2 or max(abs(start), abs(end)) > self.domain.yExtent / 2:
                    raise ValueError("fin extrusion extent must fit inside the 3D y domain")
        elif geometry["kind"] == "gds":
            if self.dimension == 2:
                if abs(geometry["position"][2]) > 1e-12:
                    raise ValueError("2D GDS position must use [x,h,0]")
                if any(row.get("baseHeight") is not None or row.get("height") is not None or row.get("mask") is not None for row in geometry["layers"]):
                    raise ValueError("2D GDS layer mappings do not accept 3D height or mask settings")
            else:
                for index, row in enumerate(geometry["layers"]):
                    if row.get("baseHeight") is None or row.get("height") is None:
                        raise ValueError(f"3D GDS layer mapping {index} requires baseHeight and height")
                    if row["height"] < 2 * delta:
                        raise ValueError(f"3D GDS layer mapping {index} height must span at least two grid intervals")
                    if abs(row["baseHeight"] + geometry["position"][2]) / delta > 1_000_000:
                        raise ValueError(f"3D GDS layer mapping {index} base height exceeds the application coordinate budget")
        elif geometry["kind"] == "stack" and geometry.get("gdsMask") is not None:
            mask = geometry["gdsMask"]
            if self.dimension != 3:
                raise ValueError("GDS mask transfer requires a 3D stack project")
            gds_layers = [row for row in geometry["layers"] if row.get("source") == "gds-mask"]
            if gds_layers:
                layer = gds_layers[0]
                if geometry["pattern"]["kind"] != "gds":
                    raise ValueError("a GDS mask layer requires top pattern gds")
                if layer["name"] != mask["name"] or layer["material"] != mask["material"] or abs(layer["thickness"] - mask["height"]) > 1e-9:
                    raise ValueError("GDS mask metadata must match its top stack layer")
            elif geometry["pattern"]["kind"] != "none":
                raise ValueError("legacy GDS masks cannot be combined with a built-in stack pattern")
            if mask["height"] < 2 * delta:
                raise ValueError("GDS mask height must span at least two grid intervals")
            if abs(mask["baseHeight"] + mask["position"][2]) / delta > 1_000_000:
                raise ValueError("GDS mask base height exceeds the application coordinate budget")
        for index, step in enumerate(self.steps):
            if not isinstance(step, GdsBooleanStepV7):
                continue
            # A GDS boolean operand is re-read from the geometry's own asset and
            # built as a real 3D body, so both a 3D project and GDS geometry are
            # required. Anything else must fail loudly instead of becoming a box.
            if self.dimension != 3:
                raise ValueError(f"steps[{index}]: gdsBoolean requires a 3D project")
            if geometry["kind"] != "gds":
                raise ValueError(f"steps[{index}]: gdsBoolean requires geometry.kind=gds; "
                                 "use the box boolean step for other structures")
            mask = step.gdsMask
            if float(mask.height) < 2 * delta:
                raise ValueError(f"steps[{index}].gdsMask.height must span at least two grid intervals")
            if abs(float(mask.baseHeight) + float(mask.position[2])) / delta > 1_000_000:
                raise ValueError(f"steps[{index}].gdsMask.baseHeight exceeds the application coordinate budget")
            if any(abs(float(value)) / delta > 1_000_000 for value in mask.position):
                raise ValueError(f"steps[{index}].gdsMask.position exceeds the application coordinate budget")
        if self.dimension == 2 and self.outputSettings is not None and self.outputSettings.volumeMesh:
            raise ValueError("native volume mesh output is only available for 3D projects")
        return self


class ProjectV5(StrictModel):
    schemaVersion: Literal[5]
    name: Annotated[str, Field(min_length=1, max_length=80)]
    dimension: Literal[2, 3]
    units: Units
    domain: Domain | Domain3D
    geometry: dict
    steps: list[StepV5]
    solver: Solver
    view: View | None = None
    view3D: View3DV3 | None = None
    materialDefinitions: Annotated[list[MaterialDefinition], Field(max_length=64)] | None = None

    @model_validator(mode="before")
    @classmethod
    def select_dimension_domain(cls, data):
        return _select_dimension_domain(data, cls)

    @model_validator(mode="after")
    def version5_shape(self):
        if self.dimension == 2 and (self.view is None or self.view3D is not None or not isinstance(self.domain, Domain)):
            raise ValueError("2D schemaVersion 5 requires 2D domain and view only")
        if self.dimension == 3 and (self.view3D is None or self.view is not None or not isinstance(self.domain, Domain3D)):
            raise ValueError("3D schemaVersion 5 requires 3D domain and view3D only")
        from pydantic import TypeAdapter
        geometry = TypeAdapter(GeometryV3).validate_python(self.geometry)
        object.__setattr__(self, "geometry", geometry.model_dump(mode="json", exclude_none=True))
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("step IDs must be unique")
        for index, step in enumerate(self.steps):
            if isinstance(step, DirectionalStepV5) and self.dimension == 2:
                for set_index, direction in _directional_rate_set_directions(step):
                    out_of_plane = normalized_out_of_plane_component(direction)
                    if out_of_plane > PLANAR_DIRECTION_TOLERANCE:
                        path = f"{index}" if set_index is None else f"{index}.rateSets[{set_index}]"
                        raise ValueError(
                            f"2D directional step {path} must use [x, h, 0]; its third direction component must be zero "
                            f"after normalization (raw {direction[2]!r} gives |z|={out_of_plane:.6g} on the unit vector)")
            elif isinstance(step, BooleanStepV5):
                if self.dimension == 2 and (abs(step.position[2]) > 1e-12 or step.depth is not None):
                    raise ValueError(f"2D boolean step {index} requires position [x, h, 0] and no depth")
                if self.dimension == 3 and step.depth is None:
                    raise ValueError(f"3D boolean step {index} requires depth")
        return self


ProjectAny = Annotated[Union[ProjectV1, ProjectV2, ProjectV3, ProjectV4, ProjectV5, ProjectV6, ProjectV7], Field(discriminator="schemaVersion")]


def parse_project(payload: object) -> ProjectV1 | ProjectV2 | ProjectV3 | ProjectV4 | ProjectV5 | ProjectV6 | ProjectV7:
    if isinstance(payload, dict) and type(payload.get("schemaVersion")) is int:
        if payload["schemaVersion"] == 1:
            return ProjectV1.model_validate(payload)
        if payload["schemaVersion"] == 2:
            return ProjectV2.model_validate(payload)
        if payload["schemaVersion"] == 3:
            return ProjectV3.model_validate(payload)
        if payload["schemaVersion"] == 4:
            return ProjectV4.model_validate(payload)
        if payload["schemaVersion"] == 5:
            return ProjectV5.model_validate(payload)
        if payload["schemaVersion"] == 6:
            return ProjectV6.model_validate(payload)
        if payload["schemaVersion"] == 7:
            return ProjectV7.model_validate(payload)
    from pydantic import TypeAdapter
    return TypeAdapter(ProjectAny).validate_python(payload)


class RunRequest(StrictModel):
    kind: Literal["preview", "simulate"]
    project: ProjectAny


class FluxRunRequest(StrictModel):
    project: ProjectV7
    stepId: Annotated[str, Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]


class ReplayStepRequirement(StrictModel):
    stepId: Annotated[str, Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
    engine: Literal["CPU_DISK", "GPU_DISK", "CPU_TRIANGLE", "GPU_TRIANGLE", "not-applicable"]
    seed: Annotated[Integer, Field(ge=0, le=4294967295)] | None = None
    executionStrategy: Literal["symmetric-operator-splitting-v1", "native-multi-ion-v1"] | None = None
    fluxStrategy: Literal["per-species-native-mesh-merge-v1", "native-multi-ion-flux-v1"] | None = None

    @model_validator(mode="after")
    def engine_seed_pair(self) -> "ReplayStepRequirement":
        if self.engine != "not-applicable" and self.seed is None:
            raise ValueError(f"{self.engine} replay requirements need a seed")
        if self.engine == "not-applicable" and self.seed is not None:
            raise ValueError("non-particle steps must not carry a random seed")
        return self


class ReplayRequirements(StrictModel):
    viennapsVersion: str
    viennalsVersion: str
    cpuThreads: Annotated[Integer, Field(ge=1)]
    steps: list[ReplayStepRequirement]
    nativeEngineFingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None

    @field_validator("cpuThreads")
    @classmethod
    def cpu_threads_within_available_limit(cls, value: int) -> int:
        maximum = available_cpu_threads()
        if value > maximum:
            raise ValueError(f"must not exceed the {maximum} CPU threads available to this process")
        return value


class SliceRequestV7(StrictModel):
    axis: Literal[1] = 1
    position: Number


class ReplayProjectV1(StrictModel):
    format: Literal["viennaps-replay"]
    schemaVersion: Literal[1]
    normalizedProject: Project
    executionRequirements: ReplayRequirements


class ReplayProjectV2(StrictModel):
    format: Literal["viennaps-replay"]
    schemaVersion: Literal[2]
    normalizedProject: ProjectV2
    executionRequirements: ReplayRequirements


class ReplayProjectV3(StrictModel):
    format: Literal["viennaps-replay"]
    schemaVersion: Literal[3]
    normalizedProject: ProjectV3
    executionRequirements: ReplayRequirements


class ReplayProjectV4(StrictModel):
    format: Literal["viennaps-replay"]
    schemaVersion: Literal[4]
    normalizedProject: ProjectV4
    executionRequirements: ReplayRequirements


class ReplayProjectV5(StrictModel):
    format: Literal["viennaps-replay"]
    schemaVersion: Literal[5]
    normalizedProject: ProjectV5
    executionRequirements: ReplayRequirements


class ReplayProjectV6(StrictModel):
    format: Literal["viennaps-replay"]
    schemaVersion: Literal[6]
    replayMode: Literal["from-initial", "from-domain-state"]
    normalizedProject: ProjectV6
    executionRequirements: ReplayRequirements

    @model_validator(mode="after")
    def replay_mode_matches_project(self):
        expected = "from-domain-state" if self.normalizedProject.continuationState is not None else "from-initial"
        if self.replayMode != expected:
            raise ValueError("replayMode must identify whether the project begins from its initial structure or a saved Domain state")
        return self


class ReplayProjectV7(StrictModel):
    format: Literal["viennaps-replay"]
    schemaVersion: Literal[7]
    replayMode: Literal["from-initial", "from-domain-state"]
    analysisMode: Literal["process", "flux", "slice"] = "process"
    analysisStepId: Annotated[str, Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")] | None = None
    sliceRequest: SliceRequestV7 | None = None
    normalizedProject: ProjectV7
    executionRequirements: ReplayRequirements

    @model_validator(mode="after")
    def replay_modes_match_project(self):
        expected = "from-domain-state" if self.normalizedProject.continuationState is not None else "from-initial"
        if self.replayMode != expected:
            raise ValueError("replayMode must identify whether the project begins from its initial structure or a saved Domain state")
        if (self.analysisMode == "flux") != (self.analysisStepId is not None):
            raise ValueError("flux replay requires exactly one analysisStepId")
        if (self.analysisMode == "slice") != (self.sliceRequest is not None):
            raise ValueError("slice replay requires exactly one sliceRequest")
        if self.analysisMode == "slice" and (self.normalizedProject.dimension != 3 or self.normalizedProject.steps):
            raise ValueError("native Slice replay requires a 3D geometry project without process steps")
        return self


ReplayProject = ReplayProjectV1


def parse_replay(payload: object) -> ReplayProjectV1 | ReplayProjectV2 | ReplayProjectV3 | ReplayProjectV4 | ReplayProjectV5 | ReplayProjectV6 | ReplayProjectV7:
    from pydantic import TypeAdapter
    return TypeAdapter(Annotated[Union[ReplayProjectV1, ReplayProjectV2, ReplayProjectV3, ReplayProjectV4, ReplayProjectV5, ReplayProjectV6, ReplayProjectV7], Field(discriminator="schemaVersion")]).validate_python(payload)


def model_error_path(message: str) -> str:
    """Extract a stable field path from common Pydantic messages when possible."""
    match = re.search(r"(?:steps\s*\[?\s*\d+\]?|domain|geometry|solver|view|units)", message)
    return match.group(0).replace(" ", "") if match else "$"
