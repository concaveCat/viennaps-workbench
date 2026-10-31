# Per-layer stack trench (`MakeTrench(MaterialLayer[])`)

This contract is limited to the **installed native binding**, not a future
upstream API. It was checked against ViennaPS 4.6.2 (production) and 4.7.0.

## Native evidence

`viennaps.d2.MakeTrench` and `viennaps.d3.MakeTrench` expose a third overload:

```text
MakeTrench(domain, materialLayers: Sequence[MaterialLayer], halfTrench=False)
```

`MakeTrench.MaterialLayer` has exactly:

| field | meaning |
|---|---|
| `height` | thickness of this native layer |
| `width` | cutout width for this layer (top width) |
| `taperAngle` | cutout taper in degrees; positive widens the layer bottom |
| `material` | native material of the layer |
| `isMask` | `true` cuts an opening through this layer; `false` leaves it intact |

Runtime probes and the shipped `tests/trench/trench.cpp` show:

- The sequence is consumed **bottom to top**.
- The first non-mask layer is inserted with `makeSubstrate(currentBase + height)`;
  every later layer is a finite slab from `currentBase` to
  `currentBase + height`.
- `isMask` is evaluated per layer, immediately after that layer is inserted.
  It is not the stack layer's etch-protection `role`.
- A positive per-layer `taperAngle` widens the layer bottom by
  `2 * height * tan(angle)`. This is the opposite visual direction from the
  legacy single-trench `trenchTaperAngle`, which is why the sign is passed
  through unchanged to `MaterialLayer` rather than reused from the old stencil.
- The constructor clears the domain before building, so a preceding
  `MakePlane` would be discarded.

`MakeHole` in both 4.6.2 and 4.7.0 has only the radius/depth constructors. It
has **no** `MaterialLayer[]` overload, so per-layer holes are rejected instead
of being emulated with a top-only stencil or a different geometry.

## Schema

`geometry.pattern` keeps all existing fields. The only addition is an optional
`layers` array:

```json
{
  "kind": "trench",
  "layers": [
    {"layerIndex": 0, "width": 1.6, "taperAngle": 0, "isMask": true},
    {"layerId": "nitride_layer", "width": 0.8, "taperAngle": 5, "isMask": true}
  ]
}
```

- Each entry references exactly one stack layer by zero-based `layerIndex` or
  by `layerId`; supplying both or neither is rejected.
- Entries must be unique and ordered bottom to top.
- The top stack layer must be present with `isMask=true`.
- `width` must be positive, `taperAngle` must be in `[-45, 45]`, and both must
  be finite JSON numbers.
- `isMask` may open film or mask layers. `role` remains the process
  protection role and is not silently rewritten.
- The legacy `pattern.width` / `pattern.taperAngle` pair may still be supplied
  together. If present, it must equal the top entry so the request cannot
  describe two different openings. If omitted, the per-layer entries are the
  only opening specification.

Omitting `layers` keeps the historical `MakePlane` + top-layer
`GeometryFactory` stencil path, including request hashes and snapshots.

### Stack mapping

The Workbench substrate is the half-space below `baseHeight`. The native
constructor starts at `-1e-4`, so the worker prepends a native substrate layer
with `height = baseHeight + 1e-4`, then one native layer per stack layer with
its declared thickness. Unreferenced layers get `width=0`, `taperAngle=0`,
`isMask=false`; the native constructor ignores width/taper for non-cut layers.
`baseHeight` must be non-negative in this mode.

The initial snapshot's `geometryReadback.perLayerPattern` records the native
constructor, the actual native layer sequence, all heights, widths, tapers,
materials and `isMask` flags.

## Rejected combinations

- `pattern.kind` other than `trench`
- per-layer holes (no installed native overload)
- `pattern.layers` with `kind=none` or `kind=gds`
- a GDS mask overlay (`geometry.gdsMask`) or a `source="gds-mask"` stack layer
- negative `baseHeight`
- unknown, duplicate, out-of-order or ambiguous layer references
- a missing top layer or `isMask=false` on the top layer
- cutouts that close under taper or exceed the lateral domain
- unknown keys, non-finite numbers, zero/negative widths and out-of-range angles

## Verification

`tests/test_capability_layers.py` covers:

- 2D and 3D equality with an independent native `MaterialLayer[]` call
- measured per-layer widths, taper and `isMask=false` versus `true`
- replay determinism
- hard-coded pre-feature request hashes and artifact equality for omitted
  blocks
- negative schema and incompatible-geometry cases

The repository-wide suite in a worktree without the untracked `docs/*-evidence`
trees reports only those missing-evidence failures; there are no per-layer
regressions.
