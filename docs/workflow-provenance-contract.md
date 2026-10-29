# Workflow provenance and measurement binding contract

**Status**: Draft for implementation after P0-3  
**Scope**: Measurement → Route/Attempt/Segment binding  
**Non-goal**: claiming that a measurement is a verified result of a real run until every required evidence item below is present.

## 1. Three independent status axes

A measurement has three separate facts. They must never be collapsed into one boolean.

| Axis | Values | Meaning |
|---|---|---|
| `measurementStatus` | `measured`, `no_intersection`, `ambiguous`, `material_not_found`, `region_empty`, `unsupported_dimension`, `unsupported_units`, `invalid_geometry` | Did the measurement algorithm produce a value? |
| `bindingStatus` | `unbound`, `verified`, `failed` | Is the measurement tied to a server-verified execution segment and step? |
| `equivalenceStatus` | `not_evaluated`, `established`, `failed`, `inconclusive` | Has numerical/semantic equivalence to a segment result been established? |

Rules:

- `measurementStatus=measured` does not imply `bindingStatus=verified`.
- `bindingStatus=verified` does not imply `equivalenceStatus=established`.
- Historical measurements may remain `unbound` forever without becoming invalid.
- A failed binding must preserve the original Measurement and record the reason. Do not overwrite or delete it.
- `Segment.runId` supplied by a user is not evidence. Only a server-established or server-verified link to a real run can grant `verified`.

## 2. Minimum evidence for `bindingStatus=verified`

| Evidence object | Minimum requirement |
|---|---|
| **Execution ownership** | Server establishes or verifies `Segment → actual Run`. The actual submitted project must match `segmentProjectHash` and the source `CompileRecord`. |
| **Target step** | The target step belongs to that segment; the run manifest contains its snapshot; the step is recorded as completed. |
| **Source identity** | Measurement `sourceStateId` equals the native state of that step; `sourceSnapshotHash` is recomputed from the snapshot actually read. |
| **Verification record** | Persist method version, evidence references, result, and failure reason. |

Additional rules:

- If any item is missing or mismatched, `bindingStatus=failed` (with reason) or remains `unbound`; never `verified`.
- The actual submitted project hash is compared to `segmentProjectHash`; the segment's `sourceCompileId` must resolve to an immutable CompileRecord.
- The run manifest must record the step as completed and reference the snapshot/state. A step that merely appears in a string field is not sufficient.
- If the snapshot is re-materialized or its content changes, `sourceSnapshotHash` must be recomputed and the binding re-checked.

## 3. Hash roles

| Hash | Role |
|---|---|
| `sourceSnapshotHash` | Identifies the measurement input snapshot content. |
| `segmentProjectHash` | Identifies the actual project submitted for the segment. |
| `CompileRecord.fullProjectHash` | Identifies the complete compiled flow. |
| `resultDigest` | Semantic result comparison for the segment's **final** state. |

Do not use `resultDigest` to validate an intermediate-step measurement. A CD/depth measured at step 5 cannot be compared with the segment's final-state digest. Numerical equivalence, when implemented, uses a separate `ComparisonProtocol` and explicitly stated scope.

## 4. Verification record

Proposed `ProvenanceVerificationV1`:

```text
ProvenanceVerificationV1 {
  methodVersion,
  verifiedAt,
  execution: {
    status: verified | failed,
    segmentId, runId,
    sourceCompileId, segmentProjectHash,
    submittedProjectHash, manifestRunId,
    reasons[]
  },
  targetStep: {
    status: verified | failed,
    stepId, stepIndex, completed,
    snapshotFile, snapshotHash, stateId, stateHash,
    reasons[]
  },
  sourceIdentity: {
    status: verified | failed,
    measurementStateId, manifestStateId,
    recomputedSnapshotHash, measurementSnapshotHash,
    reasons[]
  },
  equivalence: {
    status: not_evaluated | established | failed | inconclusive,
    comparisonProtocolId?, metricScope?, resultDigest?, reasons[]
  }
}
```

`bindingStatus=verified` requires `execution.status=verified`, `targetStep.status=verified`, and `sourceIdentity.status=verified`. It does not require `equivalence.status=established`.

## 5. State transitions

```text
unbound ──verify success──► verified
unbound ──verify failure──► failed (reason preserved)
failed  ──new evidence────► verified
verified ──hash/state mismatch──► failed (revoked)
```

- A measurement created with `POST /measurements` from a raw snapshot is `unbound` unless the caller also supplies a server-verifiable segment/run/step identity.
- `POST /measurements/from-run` may verify execution ownership only when the run/step/state exists and matches. Otherwise it returns the Measurement as `unbound` with a reason.
- Binding is idempotent for the same evidence; a new verification method version produces a new verification record, not a mutated old one.

## 6. Persistence and API proposal

`measurements` table additions:

```text
binding_status TEXT NOT NULL DEFAULT 'unbound',
binding_evidence_json TEXT,
equivalence_status TEXT NOT NULL DEFAULT 'not_evaluated',
equivalence_evidence_json TEXT
```

Proposed endpoints:

```text
POST /api/v1/workflow/measurements/{measurementId}/bind
GET  /api/v1/workflow/measurements/{measurementId}/provenance
POST /api/v1/workflow/measurements/{measurementId}/equivalence
GET  /api/v1/workflow/measurements?binding_status=unbound
```

`POST /bind` accepts an explicit `segmentId`, `runId`, `stepId` and evidence references; it must not trust arbitrary strings without manifest/state/snapshot checks.

## 7. Acceptance tests for the next stage

1. A user-supplied `Segment.runId` that does not match a real run leaves the Measurement `unbound`.
2. A step that exists in the manifest but is not completed cannot be bound.
3. A snapshot whose recomputed hash differs from `sourceSnapshotHash` cannot be bound.
4. A `sourceStateId` that differs from the manifest native state cannot be bound.
5. A submitted project whose hash differs from `segmentProjectHash` cannot be bound.
6. A valid run/step/state/snapshot/compile chain produces `bindingStatus=verified` and a verification record.
7. An intermediate-step measurement with no final `resultDigest` can still be `verified` for binding but remains `equivalenceStatus=not_evaluated`.
8. Revoking a verified binding after a hash mismatch sets `bindingStatus=failed` and preserves the original Measurement.
9. Historical measurements with no execution identity remain readable and `unbound`.

## 8. Invariants

- Measurement success and execution binding are independent.
- Binding requires evidence, not labels.
- `sourceSnapshotHash` is recalculated from the actual snapshot.
- `resultDigest` is never a substitute for intermediate-step binding.
- Failed or absent verification is explicit, never silent.
