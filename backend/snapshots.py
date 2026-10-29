"""Materialise deferred step snapshots on first read.

The worker keeps step snapshots out of the run's critical path: it writes
``<step>.meta.json`` only, plus the native ``state-<step>.vpsd``. The full
snapshot document is regenerated here (in a bounded child, so the API process
never imports ViennaPS) and then cached as ``<step>.json``.

The regenerated document is byte-identical to what the eager writer produced:
both call ``worker.simulate.save_snapshot`` with the same project and Domain
state, and ``worker/materialize_snapshot.py`` re-checks the point/cell counts
against the recorded metadata.
"""
from __future__ import annotations

import fcntl
import subprocess
from pathlib import Path

from . import storage

ROOT = storage.ROOT
PYTHON = ROOT / ".venv" / "bin" / "python"
GUARD = ROOT / "scripts" / "bounded_child.py"
WORKER = ROOT / "worker" / "materialize_snapshot.py"


def ensure_materialized(run_dir: Path, step_id: str) -> Path:
    """Return ``<step>.json``, regenerating it from ``.vpsd`` when deferred."""
    target = run_dir / f"{step_id}.json"
    if target.is_file():
        return target
    if not (run_dir / f"{step_id}.meta.json").is_file():
        raise FileNotFoundError(f"snapshot {step_id} is not available")
    input_path = run_dir / "input.json"
    if not input_path.is_file():
        raise FileNotFoundError("run input is missing")

    # One materialisation per run at a time: the JSON write itself is atomic,
    # but the auxiliary artifacts written by save_snapshot are not.
    with (run_dir / ".snapshot-materialize.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if target.is_file():
            return target
        record = run_dir / f".materialize-{step_id}.guard.json"
        log = run_dir / f".materialize-{step_id}.log"
        command = [str(PYTHON), str(GUARD), "--cwd", str(ROOT),
                   "--record", str(record), "--log-path", str(log),
                   "--timeout-seconds", "180", "--max-rss-mib", "4096",
                   "--max-log-bytes", "200000", "--",
                   str(PYTHON), str(WORKER), str(input_path), str(run_dir), step_id]
        try:
            completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True,
                                       timeout=200, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"snapshot materialisation failed: {type(exc).__name__}: {exc}") from exc
        if completed.returncode != 0 or not target.is_file():
            detail = ""
            if log.is_file():
                detail = log.read_text(encoding="utf-8", errors="replace")[-800:]
            detail = detail or (completed.stderr or "")[-800:]
            raise RuntimeError(f"snapshot materialisation failed: {detail.strip()}")
        (run_dir / f"{step_id}.meta.json").unlink(missing_ok=True)
        record.unlink(missing_ok=True)
        log.unlink(missing_ok=True)
        return target
