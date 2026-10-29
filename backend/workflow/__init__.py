"""Workflow-layer contracts and deterministic compilation."""

from .compiler import COMPILER_VERSION, compile_flow
from .ids import STEP_ID_POLICY, canonical_bytes, sha256_hex, stable_step_id
from .models import (
    CompileDiffV1,
    CompiledWorkflowV1,
    CompileRecordV1,
    FlowRevisionV1,
    GeometryBindingV1,
    ParameterProvenanceV1,
    SourceMapEntryV1,
)
from .routes import (
    AttemptV1,
    CheckpointRefV1,
    PrefixCompatibilityV1,
    RoutePlanV1,
    RouteV1,
    SegmentRefV1,
    SegmentReuseV1,
    SegmentV1,
    materialize_attempt,
)

__all__ = [
    "COMPILER_VERSION",
    "STEP_ID_POLICY",
    "AttemptV1",
    "CheckpointRefV1",
    "CompileDiffV1",
    "CompileRecordV1",
    "CompiledWorkflowV1",
    "FlowRevisionV1",
    "GeometryBindingV1",
    "ParameterProvenanceV1",
    "PrefixCompatibilityV1",
    "RoutePlanV1",
    "RouteV1",
    "SegmentRefV1",
    "SegmentReuseV1",
    "SegmentV1",
    "SourceMapEntryV1",
    "canonical_bytes",
    "compile_flow",
    "materialize_attempt",
    "sha256_hex",
    "stable_step_id",
]
