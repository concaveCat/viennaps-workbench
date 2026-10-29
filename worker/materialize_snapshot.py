"""Regenerate one deferred step snapshot from its native Domain state.

The worker writes only ``<step>.meta.json`` for step snapshots; the multi
megabyte JSON is produced here the first time a consumer (snapshot API, run
archive, replay tooling) asks for it, from ``state-<step>.vpsd``.

Usage: materialize_snapshot.py <input.json> <run_dir> <step_id>
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "worker"))


def materialize(input_path: Path, run_dir: Path, step_id: str) -> dict[str, Any]:
    import viennaps as ps
    import worker.simulate as sim

    meta_path = run_dir / f"{step_id}.meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    metadata = meta["metadata"]
    config = json.loads(input_path.read_text(encoding="utf-8"))
    project = config["project"]
    state_path = run_dir / f"state-{step_id}.vpsd"
    if not state_path.is_file():
        raise RuntimeError(f"native state for step {step_id!r} is missing")

    if project.get("schemaVersion", 1) >= 3:
        ps.Length.setUnit("um")
        ps.Time.setUnit("s")
    ps.setDimension(int(project["dimension"]))
    ps.setNumThreads(int(project["solver"]["cpuThreads"]))
    sim.configure_native_materials(ps, config.get("customMaterials", []))

    if project["dimension"] == 3:
        from mesh3d import make_domain, configure_material_catalog
        configure_material_catalog(sim.MATERIAL_ROWS)
        domain = make_domain(ps, project)
    else:
        boundary = (ps.BoundaryType.REFLECTIVE_BOUNDARY
                    if project["domain"]["xBoundary"] == "reflective" else ps.BoundaryType.PERIODIC_BOUNDARY)
        domain = ps.Domain(float(project["domain"]["gridDelta"]), float(project["domain"]["xExtent"]), boundary)
    ps.Reader(domain, str(state_path)).apply()

    # Extra (non-derivable) outputs such as the native dense cell set are kept
    # as recorded rows; they point at files the run already wrote.
    extra_outputs = [row for row in metadata.get("outputs", [])
                     if row.get("kind") not in {"levelSet", "levelSetMesh", "volumeMesh", "nativeMetadata", "surfaceMesh"}]
    produced = sim.save_snapshot(
        ps, domain, run_dir,
        run_id=metadata.get("runId") or config.get("runId") or "",
        step_id=step_id, step_name=metadata["name"], step_index=int(metadata["stepIndex"]),
        requested=project, no_op=bool(metadata.get("noOp", False)),
        extra_outputs=extra_outputs or None)
    for key in ("pointCount", "triangleCount", "lineCount"):
        if key in metadata and produced.get(key) != metadata[key]:
            raise RuntimeError(f"materialised snapshot {step_id}: {key} {produced.get(key)} != recorded {metadata[key]}")
    return produced


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    input_path, run_dir, step_id = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), sys.argv[3]
    materialize(input_path, run_dir, step_id)
    print("SNAPSHOT_MATERIALIZED", step_id, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
