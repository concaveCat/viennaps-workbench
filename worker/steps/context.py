"""Shared mutable state for the worker step loop.

``RunContext`` bundles the values the per-model executors read and the lists
they append to. Native ``Process``/model handles are deliberately *not* fields
here: the loop keeps them local so the per-step GPU teardown still sees them go
out of scope.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any


def simulate_module() -> Any:
    """Return the worker module that owns the shared native helpers.

    ``worker/simulate.py`` runs both as an imported module (tests, snapshot
    materialisers) and as the bounded child script. In script mode the
    canonical module is ``__main__``; importing it again as ``worker.simulate``
    would execute the whole worker module a second time and duplicate its
    module-level state.
    """
    main = sys.modules.get("__main__")
    if main is not None and Path(getattr(main, "__file__", "") or "").name == "simulate.py":
        return main
    module = sys.modules.get("worker.simulate")
    if module is not None:
        return module
    return import_module("worker.simulate")


@dataclass
class RunContext:
    project: dict[str, Any]
    ps: Any
    domain: Any
    run_dir: Path
    run_id: str
    config: dict[str, Any]
    actual_steps: list[dict[str, Any]]
    step_engines: dict[str, str]
    engine_preference: str
    run_seed: int | None
    selected_gpu_device: dict[str, Any] | None
    cuda_readback: dict[str, Any] | None
    gpu_sample: dict[str, Any] | None
    native_engine_fingerprint: str | None
    ps_version: str
    ls_version: str
    snapshots: list[dict[str, Any]]
    field_state: dict[str, Any]
    actual_execution: list[dict[str, Any]]
    intermediate_outputs: list[dict[str, Any]]

    def event(self, data: dict[str, Any]) -> None:
        simulate_module().event(self.run_dir, data)

    def save_snapshot(self, domain: Any, **kwargs: Any) -> dict[str, Any]:
        return simulate_module().save_snapshot(
            self.ps, domain, self.run_dir, run_id=self.run_id, **kwargs)

    def save_native_state(self, domain: Any, step_id: str) -> dict[str, Any]:
        return simulate_module().save_native_state(
            self.ps, domain, self.run_dir, project=self.project,
            run_id=self.run_id, step_id=step_id,
            viennaps_version=self.ps_version, viennals_version=self.ls_version)
