# FinFET.py acceptance fixture

This directory turns the upstream ViennaPS example
`/root/ViennaPS/examples/emulation/FinFET.py` into a platform acceptance test.
It does **not** add a script executor: the example is translated into a
schema-7 project and every operation runs through the ordinary workbench
dispatch, plugin, geometry, state and replay paths.

| File | Purpose |
|---|---|
| `reference/FinFET.py` | verbatim upstream script (SHA-256 `32ec9f2d…`) |
| `reference/FinFET_um.py` | mechanically unit-scaled native twin (µm frame) |
| `reference/canonical.py` | order-independent surface/volume digest |
| `reference/run_reference.py` | runs a native script and writes `expected*.json` |
| `expected.json` | 26 surfaces + final volume from the original nm script |
| `expected-um.json` | 26 surfaces + final volume from the project-frame twin |
| `project.json` | equivalent schema-7 workbench project (25 steps) |
| `mapping.md` | line-by-line source-to-step and unit mapping |
| `../../tests/acceptance/test_finfet_example.py` | heavy API acceptance test |
| `../../scripts/accept-finfet-example.sh` | one-command acceptance runner |

## What is checked

- 26 native surface checkpoints: point/triangle count, material order,
  per-triangle material distribution and canonical SHA-256.
- Final volume: node/cell count, tetrahedral type, material distribution and
  canonical SHA-256 (`volumeMeshDelta = 0.05`).
- All 25 project steps succeeded through
  `VIENNAPS_WORKBENCH_V2_EXECUTION=1`; `/api/runs/{id}` exposes a `v2Steps`
  record with plan, outputs and state for every step.
- Replay of the same project/seed: final surface and volume digests repeat.
- The default path (v2 switch off) is untouched and covered by the fast
  regression suite.

## Unit frame

The source is in nanometres; the workbench schema is micrometres. Lengths,
radii, half-axes, cutoffs and rates are multiplied by exactly 0.001; durations
are unchanged. The domain uses an explicit `domain.bounds` array because the
centered `xExtent`/`yExtent` constructor always produces an odd number of grid
points per axis and cannot reproduce the native 128-point y grid.

The original nm run and the µm twin agree on surfaces 0–13 and diverge at
surface 14 (dummy-gate masked box etch). A direct native µm run reproduces the
workbench digest exactly, so this is a ViennaPS level-set boolean scale
sensitivity, not a workbench dispatch bug. The acceptance therefore uses
`expected-um.json`, while `expected.json` remains the unmodified-script
evidence. `tests/test_finfet_reference.py` pins the frame relationship.

## Running

Fast checks (no heavy simulation):

```bash
VIENNAPS_WORKBENCH_DATA_DIR=$PWD/var/test-finfet WORKBENCH_PORT=8767 \
  .venv/bin/python -m unittest tests.test_finfet_schema tests.test_finfet_geometry \
  tests.test_finfet_reference -v
```

Full acceptance (native reference twice, workbench project, replay):

```bash
scripts/accept-finfet-example.sh
```

Reuse the committed reference digests and only run the workbench + replay:

```bash
scripts/accept-finfet-example.sh --skip-reference
```

The heavy test is gated by
`VIENNAPS_WORKBENCH_RUN_FINFET_ACCEPTANCE=1`. Runtime artifacts stay below
`var/accept-finfet-*`; the 94 MB final volume is not committed.

## Acceptance evidence

Recorded API acceptance run (project-frame reference):

- run id: `bdadcaa795e8f422cc284ff5`
- status: succeeded, 25/25 v2 steps
- wall time: 1299.9 s (21 m 40 s), CPU only
- surface 25 digest:
  `2465419c18cf80ea8abf3099e315c1eb6dae41694c23b7f3772b2d7c862410a5`
- final volume: 2,074,708 nodes / 12,132,792 tetrahedra, digest
  `e8320f7196489f55806accd5d9c41982e4b2d365b9c8d6712ebb35a7e98e6a21`
- replay run id: `332ba71ddd69314e7039f327`
- replay wall time: 1140.2 s (19 m 0 s); identical request and execution
  hashes (`e4252a89…`, `705af40d…`)
- replay final volume file SHA-256 is byte-identical to the first run
  (`f922a7a6…`), and the acceptance test compares every surface digest and the
  canonical volume digest before passing.

Known differences: the native nm/µm frame divergence at surface 14 described
above. All other platform behaviour matches digest-for-digest.
