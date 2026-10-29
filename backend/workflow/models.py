"""Workflow-layer contracts around the frozen ProjectV6/V7 execution schema."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from ..materials import MaterialDefinition
from ..models import (
    Domain,
    Domain3D,
    ProjectV6,
    ProjectV7,
    SolverV7,
    StepV7,
    StrictModel,
    Units,
)


class GeometryBindingV1(StrictModel):
    """An immutable application of a geometry source to one compile.

    ``Clip`` stays editable. This object freezes the material mapping, stack,
    domain, grid and coordinate transform that the compiler actually consumed.
    """

    schemaVersion: Literal[1]
    bindingId: Annotated[str, Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
    name: Annotated[str, Field(min_length=1, max_length=80)]
    dimension: Literal[2, 3]
    units: Units
    domain: Domain | Domain3D
    geometry: dict[str, Any]
    materialDefinitions: Annotated[list[MaterialDefinition], Field(max_length=64)] | None = None
    materialCatalogVersion: Annotated[str, Field(min_length=1, max_length=32)] = "4.6.2"
    defaultValuesVersion: Annotated[str, Field(min_length=1, max_length=32)] = "1"
    schemaVersions: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def domain_matches_dimension(self) -> "GeometryBindingV1":
        if self.dimension == 2 and not isinstance(self.domain, Domain):
            raise ValueError("2D GeometryBinding requires a 2D domain")
        if self.dimension == 3 and not isinstance(self.domain, Domain3D):
            raise ValueError("3D GeometryBinding requires a 3D domain")
        return self


class ParameterProvenanceV1(StrictModel):
    kind: Literal["literal", "variable", "expression"]
    source: str | None = None
    resolvedValue: Any = None


class SourceMapEntryV1(StrictModel):
    stepId: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
    flowPath: Annotated[str, Field(min_length=1, max_length=512)]
    authoredStepId: Annotated[str, Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
    moduleInstanceIdPath: Annotated[str, Field(min_length=1, max_length=256)]
    iterationKey: Annotated[str, Field(min_length=1, max_length=32, pattern=r"^i[0-9]+$")]
    parameterProvenance: dict[str, ParameterProvenanceV1] = Field(default_factory=dict)
    variableRefs: list[str] = Field(default_factory=list)


class FlowRevisionV1(StrictModel):
    """P0 flow definition: literal steps, fixed solver defaults, no geometry."""

    schemaVersion: Literal[1]
    flowId: Annotated[str, Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
    revision: Annotated[int, Field(ge=1)]
    name: Annotated[str, Field(min_length=1, max_length=80)]
    steps: Annotated[list[StepV7], Field(min_length=1)]
    solver: SolverV7

    @model_validator(mode="after")
    def unique_authored_step_ids(self) -> "FlowRevisionV1":
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("authored step IDs must be unique")
        if self.solver.random.mode != "fixed":
            raise ValueError("P0 FlowRevision requires a fixed random seed")
        return self


class CompileRecordV1(StrictModel):
    compileId: Annotated[str, Field(pattern=r"^c_[a-f0-9]{24}$")]
    compilerVersion: Annotated[str, Field(min_length=1, max_length=32)]
    flowId: Annotated[str, Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
    flowRevisionId: Annotated[str, Field(min_length=3, max_length=96)]
    flowRevisionHash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    geometryBindingId: Annotated[str, Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]
    geometryBindingHash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    variableSet: list[dict[str, Any]] = Field(default_factory=list)
    overrides: list[dict[str, Any]] = Field(default_factory=list)
    materialCatalogVersion: Annotated[str, Field(min_length=1, max_length=32)]
    materialDefinitionsHash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    defaultValuesVersion: Annotated[str, Field(min_length=1, max_length=32)]
    engineVersions: dict[str, str] = Field(default_factory=dict)
    seedPolicy: Literal["shared-run-seed"] = "shared-run-seed"
    stepIdPolicy: Annotated[str, Field(min_length=1, max_length=64)]
    fullProjectHash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    sourceMapHash: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    createdAt: str | None = None


class CompiledWorkflowV1(StrictModel):
    project: Annotated[ProjectV6 | ProjectV7, Field(discriminator="schemaVersion")]
    sourceMap: list[SourceMapEntryV1]
    compileRecord: CompileRecordV1


class CompileDiffV1(StrictModel):
    addedStepIds: list[str] = Field(default_factory=list)
    removedStepIds: list[str] = Field(default_factory=list)
    movedStepIds: list[str] = Field(default_factory=list)
    changedStepIds: list[str] = Field(default_factory=list)
    changedVariables: list[str] = Field(default_factory=list)
    affectedStepIds: list[str] = Field(default_factory=list)
    suggestedBaseCheckpoint: str | None = None
