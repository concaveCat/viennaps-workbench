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

__all__ = [
    "COMPILER_VERSION",
    "STEP_ID_POLICY",
    "CompileDiffV1",
    "CompileRecordV1",
    "CompiledWorkflowV1",
    "FlowRevisionV1",
    "GeometryBindingV1",
    "ParameterProvenanceV1",
    "SourceMapEntryV1",
    "canonical_bytes",
    "compile_flow",
    "sha256_hex",
    "stable_step_id",
]
