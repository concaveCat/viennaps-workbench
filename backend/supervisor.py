"""Single-run process supervisor; the API process never imports ViennaPS."""

from __future__ import annotations

import importlib.metadata
import calendar
import hashlib
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .hashing import execution_hash, requested_input_hash
from . import storage
from . import materials as material_registry
from .validation import (GPU_MULTIPARTICLE_FLUX_STRATEGY, GPU_MULTIPARTICLE_SPLIT_STRATEGY,
                         GPU_MULTIPARTICLE_NATIVE_STRATEGY, GPU_MULTIPARTICLE_NATIVE_FLUX_STRATEGY,
                         select_engine, capability_verified,
                         gpu_project_signature)
from .native_build import configured_runtime
from .rates import uses_flux
from .gpu_devices import cuda_devices, resolve_gpu_device


ROOT = storage.ROOT
PYTHON = ROOT / ".venv" / "bin" / "python"
GUARD = ROOT / "scripts" / "bounded_child.py"
WORKER = ROOT / "worker" / "simulate.py"
RSS_MIB = 8192
LOG_LIMIT = 4 * 1024 * 1024
ARTIFACT_LIMIT = 512 * 1024 * 1024
TERMINAL = {"succeeded", "failed", "timed_out", "cancelled", "interrupted"}
ACTIVE = {"pending", "running", "cancelling"}


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _proc_identity(pid: int) -> dict[str, int] | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        fields = raw[raw.rfind(")") + 2 :].split()
        return {"pid": pid, "pgid": int(fields[2]), "startTicks": int(fields[19])}
    except (OSError, ValueError, IndexError):
        return None


def _group_members(pgid: int) -> list[int]:
    result = []
    try:
        entries = os.scandir("/proc")
    except OSError:
        return result
    with entries:
        for entry in entries:
            if not entry.name.isdigit():
                continue
            identity = _proc_identity(int(entry.name))
            if identity and identity["pgid"] == pgid:
                result.append(identity["pid"])
    return result


def _terminate_recorded(pid: int, start_ticks: int, pgid: int) -> bool:
    identity = _proc_identity(pid)
    if identity != {"pid": pid, "pgid": pgid, "startTicks": start_ticks}:
        return False
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not _group_members(pgid):
            return True
        time.sleep(0.05)
    if _group_members(pgid):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    return True


def _tail_text(path: Path, byte_limit: int = 8192) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - byte_limit))
            return stream.read(byte_limit).decode("utf-8", errors="replace")
    except OSError:
        return ""


@dataclass
class RunHandle:
    run_id: str
    run_dir: Path
    cancel_event: threading.Event = field(default_factory=threading.Event)
    process: subprocess.Popen[bytes] | None = None
    thread: threading.Thread | None = None
    external_failure: str | None = None


class ActiveRunError(RuntimeError):
    def __init__(self, run_id: str):
        self.run_id = run_id
        super().__init__("a workbench run is already active")


class ReplayMismatch(ValueError):
    pass


class Supervisor:
    def __init__(self, lock_fd: int):
        self.lock_fd = lock_fd
        self.lock = threading.RLock()
        self.active: RunHandle | None = None
        self._recover_interrupted()

    def close(self) -> None:
        with self.lock:
            handle = self.active
            if handle and self._status(handle.run_id) in ACTIVE:
                handle.cancel_event.set()
                proc = handle.process
                if proc and proc.poll() is None:
                    self._set_status(handle, "cancelling", cancelRequested=True)
                    self._send_owned_signal(handle, signal.SIGTERM)
        if handle and handle.thread:
            handle.thread.join(timeout=10)
        if handle and handle.process and handle.process.poll() is None:
            self._send_owned_signal(handle, signal.SIGKILL)
            handle.thread.join(timeout=3)
        os.close(self.lock_fd)

    def _recover_interrupted(self) -> None:
        for run_dir in storage.RUNS.iterdir():
            if not run_dir.is_dir():
                continue
            manifest_path = run_dir / "manifest.json"
            try:
                manifest = storage.read_json(manifest_path)
            except (OSError, ValueError):
                continue
            if manifest.get("status") not in ACTIVE:
                continue
            managed = manifest.get("supervisorProcess") or {}
            pid = managed.get("pid")
            start_ticks = managed.get("startTicks")
            pgid = managed.get("processGroupId")
            if pgid is None:
                pgid = managed.get("pgid")  # legacy development records
            if all(isinstance(item, int) for item in (pid, start_ticks, pgid)):
                _terminate_recorded(pid, start_ticks, pgid)
            manifest.update({"status": "interrupted", "finishedAt": now_iso(), "failureReason": "server restarted before the run reached a terminal state"})
            storage.atomic_json(manifest_path, manifest)

    def start(self, kind: str, project: dict[str, Any], *, replay: dict[str, Any] | None = None,
              analysis_step_id: str | None = None, slice_request: dict[str, Any] | None = None) -> dict[str, Any]:
        with self.lock:
            if self.active and self._status(self.active.run_id) in ACTIVE:
                raise ActiveRunError(self.active.run_id)
            storage.check_disk_preflight()
            request_hash = requested_input_hash_from_dict(project)
            if replay and replay.get("analysisMode") == "flux":
                kind = "flux"
                analysis_step_id = replay.get("analysisStepId")
            elif replay and replay.get("analysisMode") == "slice":
                kind = "slice"
                slice_request = replay.get("sliceRequest")
            if kind == "slice":
                if project.get("schemaVersion") != 7 or project.get("dimension") != 3 or project.get("steps"):
                    raise ReplayMismatch("native Slice requires a schema-7 3D geometry project without process steps")
                if not isinstance(slice_request, dict) or slice_request.get("axis") != 1 or not isinstance(slice_request.get("position"), (int, float)):
                    raise ReplayMismatch("native Slice requires its verified y-normal axis and finite position")
                request_hash = hashlib.sha256((request_hash + json.dumps(slice_request, sort_keys=True, separators=(",", ":"), allow_nan=False)).encode("utf-8")).hexdigest()
            if kind == "simulate":
                enabled_steps = [step for step in project["steps"] if step["enabled"]]
            elif kind == "flux":
                selected = next((step for step in project["steps"] if step.get("id") == analysis_step_id), None)
                if project.get("schemaVersion") != 7 or not selected or not selected.get("enabled") or not uses_flux(selected):
                    raise ReplayMismatch("single flux analysis requires an enabled schema-7 particle-process step with a nonzero rate relation")
                enabled_steps = [selected]
            else:
                enabled_steps = []
            uses_particle = any(uses_flux(step) for step in enabled_steps)
            if replay:
                expected_particle_ids = {step["id"] for step in enabled_steps if uses_flux(step)}
                if set(replay["stepsById"]) != expected_particle_ids:
                    raise ReplayMismatch("replay requirements contain missing or unexpected particle steps")
                run_seed = replay["seed"] if uses_particle else None
            elif uses_particle:
                random_policy = project["solver"]["random"]
                run_seed = int(random_policy["seed"]) if random_policy["mode"] == "fixed" else secrets.randbelow(2**32)
            else:
                run_seed = None
            ps_version = importlib.metadata.version("viennaps")
            ls_version = importlib.metadata.version("viennals")
            expected_versions = {"viennaps": ps_version, "viennals": ls_version}
            state_ref = project.get("continuationState")
            state_path: Path | None = None
            if state_ref is not None:
                if state_ref.get("viennapsVersion") != ps_version or state_ref.get("viennalsVersion") != ls_version:
                    raise ReplayMismatch("native Domain state requires a different ViennaPS/ViennaLS version")
                state_path = storage.resolve_domain_state(state_ref)
            if replay and (replay["viennapsVersion"] != ps_version or replay["viennalsVersion"] != ls_version):
                raise ReplayMismatch(f"replay requires ViennaPS {replay['viennapsVersion']} / ViennaLS {replay['viennalsVersion']}; installed is {ps_version} / {ls_version}")
            actual_steps = []
            analysis_mode = "flux" if kind == "flux" else "process"
            for step in enabled_steps:
                particle = uses_flux(step)
                engine = select_engine(project, step, analysis_mode=analysis_mode) if particle else "not-applicable"
                seed = run_seed if particle else None
                execution_strategy = None
                flux_strategy = None
                multi_ion_gpu = (project.get("schemaVersion") == 7
                                 and step.get("model") == "multiParticle"
                                 and sum(row.get("kind") == "ion" for row in step.get("species", [])) > 1
                                 and engine == "GPU_DISK")
                if multi_ion_gpu:
                    if kind == "flux":
                        flux_strategy = GPU_MULTIPARTICLE_NATIVE_FLUX_STRATEGY
                    else:
                        execution_strategy = GPU_MULTIPARTICLE_NATIVE_STRATEGY
                if replay and particle:
                    requirement = replay["stepsById"].get(step["id"])
                    if not requirement:
                        raise ReplayMismatch(f"replay requirements are missing step {step['id']}")
                    engine=requirement["engine"]
                    multi_ion_gpu = (project.get("schemaVersion") == 7
                                     and step.get("model") == "multiParticle"
                                     and sum(row.get("kind") == "ion" for row in step.get("species", [])) > 1
                                     and engine == "GPU_DISK")
                    execution_strategy = requirement.get("executionStrategy")
                    flux_strategy = requirement.get("fluxStrategy")
                    valid_strategy = (
                        execution_strategy in {GPU_MULTIPARTICLE_SPLIT_STRATEGY, GPU_MULTIPARTICLE_NATIVE_STRATEGY}
                        and flux_strategy is None if multi_ion_gpu and kind != "flux" else
                        flux_strategy in {GPU_MULTIPARTICLE_FLUX_STRATEGY, GPU_MULTIPARTICLE_NATIVE_FLUX_STRATEGY}
                        and execution_strategy is None if multi_ion_gpu and kind == "flux" else
                        execution_strategy is None and flux_strategy is None)
                    if not valid_strategy:
                        raise ReplayMismatch("replay MultiParticle GPU strategy does not match its process or single-pass flux mode")
                    capability_model=f"specialized:{step['nativeModel']}" if step.get("model")=="specialized" else step["model"]
                    boundary=project["domain"].get("boundaryXY",project["domain"].get("xBoundary"))
                    signature=gpu_project_signature(project,step)
                    if not engine.startswith("GPU_") and not capability_verified(project["dimension"],capability_model,step["operation"],boundary,engine,
                                               schema_version=project["schemaVersion"],
                                               execution_strategy=execution_strategy,
                                               flux_strategy=flux_strategy, **signature):
                        raise ReplayMismatch("unverified replay engine, geometry or material combination")
                    if requirement["engine"] != engine or requirement["seed"] != run_seed:
                        raise ReplayMismatch(f"replay engine or seed does not match step {step['id']}")
                actual_steps.append({"stepId": step["id"], "engine": engine, "seed": seed,
                                     **({"executionStrategy": execution_strategy} if execution_strategy else {}),
                                     **({"fluxStrategy": flux_strategy} if flux_strategy else {})})
            selected_gpu_device = None
            if any(item["engine"].startswith("GPU_") for item in actual_steps):
                inventory = cuda_devices()
                try:
                    selected_gpu_device = resolve_gpu_device(project.get("solver", {}).get("gpuDeviceId"), inventory)
                except RuntimeError as exc:
                    # Automatic mode may use an independently verified CPU
                    # engine when the host has no visible CUDA device. An
                    # explicit device choice or explicit GPU preference never
                    # silently changes device or engine.
                    if (replay is None and project["solver"]["enginePreference"] == "auto"
                            and project.get("solver", {}).get("gpuDeviceId") is None):
                        step_by_id = {step["id"]: step for step in enabled_steps}
                        for item in actual_steps:
                            if not item["engine"].startswith("GPU_"):
                                continue
                            step = step_by_id[item["stepId"]]
                            model = f"specialized:{step['nativeModel']}" if step.get("model") == "specialized" else step["model"]
                            boundary = project["domain"].get("boundaryXY", project["domain"].get("xBoundary"))
                            signature = gpu_project_signature(project, step)
                            if not capability_verified(project["dimension"], model, step["operation"], boundary,
                                                       "CPU_DISK", schema_version=project.get("schemaVersion"), **signature):
                                raise ReplayMismatch(str(exc)) from exc
                            item["engine"] = "CPU_DISK"
                            item.pop("executionStrategy", None)
                            item.pop("fluxStrategy", None)
                    else:
                        raise ReplayMismatch(str(exc)) from exc
                if not any(item["engine"].startswith("GPU_") for item in actual_steps):
                    selected_gpu_device = None
                elif selected_gpu_device is None:
                    raise ReplayMismatch("GPU_DISK requires a concrete CUDA device; no implicit fallback is allowed")
                else:
                    for item in actual_steps:
                        if item["engine"].startswith("GPU_"):
                            item["gpuDeviceId"] = selected_gpu_device["uuid"]
            requested_fingerprint = replay.get("nativeEngineFingerprint") if replay else None
            native_strategy = any(item.get("executionStrategy") == GPU_MULTIPARTICLE_NATIVE_STRATEGY
                                  or item.get("fluxStrategy") == GPU_MULTIPARTICLE_NATIVE_FLUX_STRATEGY
                                  for item in actual_steps)
            gpu_multi_steps = {step["id"]: step for step in enabled_steps if step.get("model") == "multiParticle"}
            gpu_multiparticle = any(item["stepId"] in gpu_multi_steps and item["engine"] == "GPU_DISK"
                                    for item in actual_steps)

            def _requires_patched_multi_particle(step: dict[str, Any]) -> bool:
                """Only multi-ion GPU steps need the patched runtime.

                The installed 4.6.2 GPU binding silently ignores every ion
                after the first, so more than one ion is the one case where
                stock would produce a wrong result. Everything else stays on
                stock: a neutral with an empty material-sticking map fails
                loudly with CUDA_ERROR_UNKNOWN instead, which is preferable
                to injecting the patched build while its DirectionalProcess
                visibility path still segfaults.
                """
                species = step.get("species") or []
                return sum(1 for row in species if row.get("kind") == "ion") > 1

            gpu_multiparticle_patch = any(
                item["stepId"] in gpu_multi_steps and item["engine"] == "GPU_DISK"
                and _requires_patched_multi_particle(gpu_multi_steps[item["stepId"]])
                for item in actual_steps)
            # Old replays without a fingerprint must retain their original
            # runtime and execution identity. New runs only inject the patched
            # build for the multi-particle shapes that actually need it.
            needs_native_patch = bool(requested_fingerprint) if replay else gpu_multiparticle_patch
            if replay and ((native_strategy and not needs_native_patch)
                           or (needs_native_patch and not gpu_multiparticle)):
                raise ReplayMismatch("replay native engine identity does not match its GPU MultiParticle steps")
            native_engine_fingerprint = None
            if needs_native_patch:
                try:
                    _, native_engine_fingerprint = configured_runtime(requested_fingerprint)
                except (OSError, RuntimeError, ValueError) as exc:
                    raise ReplayMismatch(str(exc)) from exc
                if requested_fingerprint and requested_fingerprint != native_engine_fingerprint:
                    raise ReplayMismatch("replay requires a different patched ViennaPS engine build")
            if replay and set(replay["stepsById"]) != {step["id"] for step in enabled_steps if uses_flux(step)}:
                raise ReplayMismatch("replay requirements contain missing or unexpected particle steps")
            run_id = secrets.token_hex(12)
            run_dir = storage.run_path(run_id)
            run_dir.mkdir(mode=0o700, parents=False)
            gds_asset_path = None
            geometry = project.get("geometry", {})
            if isinstance(geometry, dict) and geometry.get("kind") == "gds":
                source_gds = storage.resolve_gds_asset(geometry.get("assetId", ""))
                gds_asset_path = run_dir / "gds-source.gds"
                shutil.copyfile(source_gds, gds_asset_path)
                if storage.file_sha256(gds_asset_path) != geometry["assetId"]:
                    raise OSError("copied GDS asset does not match its SHA-256 reference")
            initial_state_path = None
            if state_path is not None:
                initial_state_path = run_dir / "domain-state.vpsd"
                shutil.copyfile(state_path, initial_state_path)
                if storage.file_sha256(initial_state_path) != state_ref["stateId"]:
                    raise OSError("copied native Domain state does not match its SHA-256 reference")
            input_config = {"kind": kind, "runId": run_id, "project": project, "runSeed": run_seed,
                            "expectedVersions": expected_versions, "stepEngines": {s["stepId"]:s["engine"] for s in actual_steps}}
            if native_engine_fingerprint is not None:
                input_config["nativeEngineFingerprint"] = native_engine_fingerprint
            input_config["stepExecutionStrategies"] = {
                item["stepId"]: item["executionStrategy"] for item in actual_steps
                if item.get("executionStrategy") is not None
            }
            input_config["stepFluxStrategies"] = {
                item["stepId"]: item["fluxStrategy"] for item in actual_steps
                if item.get("fluxStrategy") is not None
            }
            if selected_gpu_device is not None:
                input_config["selectedGpuDevice"] = selected_gpu_device
            custom_materials = project.get("materialDefinitions") or material_registry.worker_registry_payload()
            input_config["customMaterials"] = custom_materials
            if gds_asset_path is not None:
                input_config["gdsAssetPath"] = str(gds_asset_path)
            if kind == "flux":
                input_config["analysisStepId"] = analysis_step_id
            if kind == "slice":
                input_config["sliceRequest"] = slice_request
            if initial_state_path is not None:
                input_config["initialStatePath"] = str(initial_state_path)
            storage.atomic_json(run_dir / "input.json", input_config)
            storage.atomic_json(run_dir / "project.json", project)
            (run_dir / "run.log").touch(exist_ok=True)
            request_exec_hash = execution_hash(
                request_hash,
                run_seed=run_seed,
                cpu_threads=project["solver"]["cpuThreads"],
                step_engines=actual_steps,
                viennaps_version=ps_version,
                viennals_version=ls_version,
                native_engine_fingerprint=native_engine_fingerprint,
            )
            manifest = {
                "schemaVersion": project["schemaVersion"],
                "dimension": project["dimension"],
                "runId": run_id,
                "kind": kind,
                "status": "pending",
                "createdAt": now_iso(),
                "startedAt": None,
                "finishedAt": None,
                "elapsedSeconds": 0.0,
                "failureReason": None,
                "requestedInputHash": request_hash,
                "executionHash": None,
                "normalizedProject": project,
                "gdsAssetId": geometry.get("assetId") if isinstance(geometry, dict) and geometry.get("kind") == "gds" else None,
                "sliceRequest": slice_request if kind == "slice" else None,
                "requestedSolver": project["solver"],
                "selectedGpuDevice": selected_gpu_device,
                "materialRegistry": custom_materials,
                "outputSettings": project.get("outputSettings"),
                "actualSeed": run_seed,
                "cpuThreads": project["solver"]["cpuThreads"],
                "versions": {"application": "0.1.0", "python": sys.version.split()[0], "viennaps": ps_version, "viennals": ls_version},
                **({"nativeEngineFingerprint": native_engine_fingerprint} if native_engine_fingerprint else {}),
                "plannedExecution": actual_steps,
                "actualExecution": [],
                "plannedExecutionHash": request_exec_hash,
                "supervisorProcess": None,
                "currentStepId": None,
                "completedStepIds": [],
                "snapshots": [],
                "noOpSteps": [],
                "replay": replay is not None,
                "runMode": "from-domain-state" if state_ref is not None else "from-initial",
                "initialState": {key: value for key, value in (state_ref or {}).items() if key != "sourceRunId" and key != "sourceStepId"} if state_ref is not None else None,
            }
            replay_project = json.loads(json.dumps(project, ensure_ascii=False))
            replay_actual_seed = run_seed
            if replay_actual_seed is None:
                random_config = project["solver"]["random"]
                replay_actual_seed = int(random_config.get("seed", 0))
            replay_project["solver"]["random"] = {"mode": "fixed", "seed": replay_actual_seed}
            replay_engine = next((item["engine"] for item in actual_steps if item["engine"] in {"CPU_DISK", "GPU_DISK", "CPU_TRIANGLE", "GPU_TRIANGLE"}), "CPU_DISK")
            replay_project["solver"]["enginePreference"] = "gpu" if replay_engine.startswith("GPU_") else "cpu"
            if selected_gpu_device is not None and project["schemaVersion"] == 7:
                replay_project["solver"]["gpuDeviceId"] = selected_gpu_device["uuid"]
            replay_requirements = [
                {"stepId": item["stepId"], "engine": item["engine"], "seed": replay_actual_seed,
                 **({"executionStrategy": item["executionStrategy"]} if item.get("executionStrategy") else {}),
                 **({"fluxStrategy": item["fluxStrategy"]} if item.get("fluxStrategy") else {})}
                for item in actual_steps if item["engine"] in {"CPU_DISK", "GPU_DISK", "CPU_TRIANGLE", "GPU_TRIANGLE"}
            ]
            replay_document = {
                "format": "viennaps-replay", "schemaVersion": project["schemaVersion"], "normalizedProject": replay_project,
                "executionRequirements": {"viennapsVersion": ps_version, "viennalsVersion": ls_version,
                                          "cpuThreads": project["solver"]["cpuThreads"], "steps": replay_requirements,
                                          **({"nativeEngineFingerprint": native_engine_fingerprint} if native_engine_fingerprint else {})},
            }
            if project["schemaVersion"] == 6:
                replay_document["replayMode"] = "from-domain-state" if state_ref is not None else "from-initial"
            elif project["schemaVersion"] == 7:
                replay_document.update({"replayMode": "from-domain-state" if state_ref is not None else "from-initial",
                                        "analysisMode": "flux" if kind == "flux" else "slice" if kind == "slice" else "process"})
                if kind == "flux":
                    replay_document["analysisStepId"] = analysis_step_id
                if kind == "slice":
                    replay_document["sliceRequest"] = slice_request
            storage.atomic_json(run_dir / "replay-project.json", replay_document)
            storage.atomic_json(run_dir / "manifest.json", manifest)
            handle = RunHandle(run_id, run_dir)
            self.active = handle
            handle.thread = threading.Thread(target=self._run, args=(handle,), name=f"viennaps-{run_id}", daemon=True)
            handle.thread.start()
            return {"runId": run_id, "status": "pending", "requestedInputHash": request_hash}

    def _run(self, handle: RunHandle) -> None:
        run_dir = handle.run_dir
        try:
            if handle.cancel_event.is_set():
                self._finish(handle, "cancelled", failure="cancelled before worker start")
                return
            manifest = storage.read_json(run_dir / "manifest.json")
            project = manifest["normalizedProject"]
            timeout = int(project["solver"]["timeoutSeconds"])
            ready_path = run_dir / ".guard-ready"
            cmd = [
                str(PYTHON), str(GUARD), "--cwd", str(ROOT), "--record", str(run_dir / "child-record.json"),
                "--log-path", str(run_dir / "run.log"),
                "--ready-file", str(ready_path),
                "--parent-pid", str(os.getpid()),
                "--timeout-seconds", str(timeout), "--max-rss-mib", str(RSS_MIB), "--max-log-bytes", str(LOG_LIMIT),
                "--terminate-grace-seconds", "5", "--", str(PYTHON), str(WORKER), str(run_dir / "input.json"), str(run_dir),
            ]
            worker_env = os.environ.copy()
            expected_fingerprint = manifest.get("nativeEngineFingerprint")
            if expected_fingerprint:
                runtime_dir, actual_fingerprint = configured_runtime(expected_fingerprint)
                if actual_fingerprint != expected_fingerprint:
                    raise ReplayMismatch("patched ViennaPS engine changed before worker start")
                worker_env["PYTHONPATH"] = os.pathsep.join(
                    [str(runtime_dir), str(ROOT), worker_env.get("PYTHONPATH", "")])
            proc = subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    start_new_session=True, close_fds=True, env=worker_env)
            handle.process = proc
            ready_deadline = time.monotonic() + 10.0
            while not ready_path.is_file() and proc.poll() is None and time.monotonic() < ready_deadline:
                time.sleep(0.02)
            if not ready_path.is_file():
                if proc.poll() is None:
                    proc.kill()
                proc.wait()
                if handle.cancel_event.is_set():
                    self._finish(handle, "cancelled", failure="run cancelled before worker startup")
                else:
                    self._finish(handle, "failed", failure="worker guard did not become ready within 10 seconds")
                return
            identity = _proc_identity(proc.pid)
            with self.lock:
                self._set_status(handle, "running", startedAt=now_iso(), supervisorProcess={
                    "pid": proc.pid, "processGroupId": identity["pgid"] if identity else proc.pid,
                    "startTicks": identity["startTicks"] if identity else None,
                })
            last_storage_check = time.monotonic()
            while proc.poll() is None:
                self._read_events(handle)
                if time.monotonic() - last_storage_check >= 5:
                    last_storage_check = time.monotonic()
                    try:
                        storage.check_disk_preflight()
                        storage.check_artifact_budget(run_dir)
                    except OSError as exc:
                        handle.external_failure = str(exc)
                        self._send_owned_signal(handle, signal.SIGTERM)
                        break
                if handle.cancel_event.is_set() and proc.poll() is None:
                    self._send_owned_signal(handle, signal.SIGTERM)
                time.sleep(0.2)
            proc.wait()
            self._read_events(handle)
            self._complete_from_record(handle, proc.returncode)
        except BaseException as exc:
            try:
                self._finish(handle, "failed", failure=f"supervisor failure: {type(exc).__name__}: {exc}")
            except BaseException:
                pass

    def _read_events(self, handle: RunHandle) -> None:
        path = handle.run_dir / "events.ndjson"
        offset = int(getattr(handle, "event_offset", 0))
        try:
            with path.open("rb") as stream:
                stream.seek(offset)
                for raw in stream:
                    offset += len(raw)
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self._apply_event(handle, item)
        except FileNotFoundError:
            pass
        handle.event_offset = offset

    def _apply_event(self, handle: RunHandle, item: dict[str, Any]) -> None:
        with self.lock:
            manifest = storage.read_json(handle.run_dir / "manifest.json")
            event_type = item.get("type")
            if event_type == "step-started":
                manifest["currentStepId"] = item.get("stepId")
                manifest["currentStepName"] = item.get("name")
            elif event_type == "snapshot":
                entry = {key: item[key] for key in ("stepId", "name", "stepIndex", "snapshot", "vtk", "pointCount", "lineCount", "triangleCount", "topology", "dimension", "materialIds", "materialIdsByLevelSetIndex", "noOp", "measuredGeometry", "geometryReadback", "sliceReadback", "nativeState", "outputs") if key in item}
                if not any(existing.get("stepId") == entry.get("stepId") for existing in manifest["snapshots"]):
                    manifest["snapshots"].append(entry)
                if entry.get("stepId") != "initial" and entry.get("stepId") not in manifest["completedStepIds"]:
                    manifest["completedStepIds"].append(entry["stepId"])
                if entry.get("noOp"):
                    manifest["noOpSteps"].append({"stepId": entry["stepId"], "reason": "zero-rate step; process was not called"})
                manifest["currentStepId"] = None
                manifest["currentStepName"] = None
            elif event_type == "worker-failed":
                manifest["workerError"] = item.get("error")
            elif event_type == "step-configured":
                record = {key: item[key] for key in ("stepId", "operation", "model", "noOp", "actualEngine", "engineSelectionReason",
                                                      "requestedEnginePreference", "actualSeed", "actualRate", "duration", "actualParameters") if key in item}
                existing = manifest.setdefault("actualExecution", [])
                existing[:] = [old for old in existing if old.get("stepId") != record.get("stepId")]
                existing.append(record)
            elif event_type == "flux-result":
                manifest["fluxResult"] = {key: item[key] for key in ("fileName", "vtk", "meshIdentity", "pointCount", "fieldCount", "fieldNames", "actualEngine", "actualSeed") if key in item}
                manifest["currentStepId"] = None
                manifest["currentStepName"] = None
            elif event_type == "intermediate-output":
                output = item.get("output")
                if isinstance(output, dict) and isinstance(output.get("fileName"), str):
                    outputs = manifest.setdefault("intermediateOutputs", [])
                    outputs[:] = [old for old in outputs if old.get("fileName") != output["fileName"]]
                    outputs.append(output)
            storage.atomic_json(handle.run_dir / "manifest.json", manifest)

    def _complete_from_record(self, handle: RunHandle, return_code: int | None) -> None:
        run_dir = handle.run_dir
        try:
            child_record = storage.read_json(run_dir / "child-record.json")
        except (OSError, ValueError):
            child_record = {}
        try:
            worker_result = storage.read_json(run_dir / "worker-result.json")
        except (OSError, ValueError):
            worker_result = None
        if handle.external_failure:
            self._finish(handle, "failed", failure=handle.external_failure, childRecord=child_record)
            return
        if return_code == 0 and worker_result and not child_record.get("remainingOwnedGroupMembers"):
            try:
                storage.check_artifact_budget(run_dir)
                storage.check_disk_preflight()
            except OSError as exc:
                self._finish(handle, "failed", failure=str(exc), childRecord=child_record)
                return
            manifest = storage.read_json(run_dir / "manifest.json")
            if worker_result.get("nativeEngineFingerprint") != manifest.get("nativeEngineFingerprint"):
                self._finish(handle, "failed", failure="worker native engine build identity differs from the planned run", childRecord=child_record)
                return
            actual = worker_result.get("actualSteps", [])
            request_hash = manifest["requestedInputHash"]
            exec_hash = execution_hash(
                request_hash,
                run_seed=worker_result["actualSeed"],
                cpu_threads=worker_result["cpuThreads"],
                step_engines=actual,
                viennaps_version=worker_result["versions"]["viennaps"],
                viennals_version=worker_result["versions"]["viennals"],
                native_engine_fingerprint=manifest.get("nativeEngineFingerprint"),
            )
            self._finish(handle, "succeeded", workerResult=worker_result, childRecord=child_record,
                         actualExecution=worker_result.get("stepRecords", []), executionHash=exec_hash)
            return
        reason = child_record.get("terminationReason")
        if reason == "timeout":
            self._finish(handle, "timed_out", failure="run exceeded its configured wall-clock timeout", childRecord=child_record)
        elif reason == "supervisor_cancelled" or handle.cancel_event.is_set():
            self._finish(handle, "cancelled", failure="run cancelled by the user or server shutdown", childRecord=child_record)
        elif reason == "rss_limit":
            self._finish(handle, "failed", failure=f"observed process-group RSS exceeded the {RSS_MIB} MiB termination threshold", childRecord=child_record)
        elif reason == "log_limit":
            self._finish(handle, "failed", failure="combined stdout/stderr exceeded the 4 MiB log cap", childRecord=child_record)
        elif reason == "orphaned_child":
            self._finish(handle, "failed", failure="worker left a process in its owned process group", childRecord=child_record)
        else:
            current = storage.read_json(run_dir / "manifest.json")
            detail = current.get("workerError") or _tail_text(run_dir / "run.log") or f"worker exited with return code {return_code}"
            self._finish(handle, "failed", failure=detail[-8192:], childRecord=child_record)

    def _finish(self, handle: RunHandle, status: str, *, failure: str | None = None, **updates: Any) -> None:
        with self.lock:
            manifest = storage.read_json(handle.run_dir / "manifest.json")
            if manifest.get("status") in TERMINAL:
                return
            start_epoch = None
            if manifest.get("startedAt"):
                try:
                    start_epoch = calendar.timegm(time.strptime(manifest["startedAt"], "%Y-%m-%dT%H:%M:%SZ"))
                except ValueError:
                    pass
            manifest.update(updates)
            manifest["status"] = status
            manifest["finishedAt"] = now_iso()
            if start_epoch is not None:
                manifest["elapsedSeconds"] = round(max(0.0, time.time() - start_epoch), 3)
            if failure:
                manifest["failureReason"] = failure
            storage.atomic_json(handle.run_dir / "manifest.json", manifest)

    def _set_status(self, handle: RunHandle, status: str, **updates: Any) -> None:
        manifest = storage.read_json(handle.run_dir / "manifest.json")
        if manifest.get("status") in TERMINAL:
            return
        if manifest.get("status") == "cancelling" and status == "running":
            status = "cancelling"
        manifest.update(updates)
        manifest["status"] = status
        storage.atomic_json(handle.run_dir / "manifest.json", manifest)

    def _send_owned_signal(self, handle: RunHandle, signum: int) -> None:
        proc = handle.process
        if not proc:
            return
        try:
            if not (handle.run_dir / ".guard-ready").is_file():
                return
            manifest = storage.read_json(handle.run_dir / "manifest.json")
            identity = manifest.get("supervisorProcess") or {}
            current = _proc_identity(proc.pid)
            expected_pgid = identity.get("processGroupId", identity.get("pgid"))
            if current and current.get("startTicks") == identity.get("startTicks") and current.get("pgid") == expected_pgid:
                os.kill(proc.pid, signum)
        except (OSError, ValueError):
            pass

    def cancel(self, run_id: str) -> dict[str, Any]:
        manifest = self._manifest(run_id)
        with self.lock:
            status = manifest.get("status")
            if status in TERMINAL:
                return {"runId": run_id, "status": status}
            handle = self.active if self.active and self.active.run_id == run_id else None
            if not handle:
                return {"runId": run_id, "status": status}
            if handle.process and handle.process.poll() is not None:
                return {"runId": run_id, "status": status}
            handle.cancel_event.set()
            self._set_status(handle, "cancelling", cancelRequested=True)
            self._send_owned_signal(handle, signal.SIGTERM)
            return {"runId": run_id, "status": "cancelling"}

    def _manifest(self, run_id: str) -> dict[str, Any]:
        return storage.read_json(storage.run_path(run_id) / "manifest.json")

    def _status(self, run_id: str) -> str:
        try:
            return str(self._manifest(run_id).get("status"))
        except (OSError, ValueError):
            return "unknown"

    def get_run(self, run_id: str, *, cursor: int = 0) -> dict[str, Any]:
        run_dir = storage.run_path(run_id)
        manifest = storage.read_json(run_dir / "manifest.json")
        log_path = run_dir / "run.log"
        log_size = log_path.stat().st_size if log_path.exists() else 0
        if cursor < 0 or cursor > log_size:
            cursor = max(0, min(cursor, log_size))
        with log_path.open("rb") if log_path.exists() else open(os.devnull, "rb") as stream:
            stream.seek(cursor)
            data = stream.read(64 * 1024)
        next_cursor = cursor + len(data)
        try:
            child_record = storage.read_json(run_dir / "child-record.json")
            was_truncated = child_record.get("logBytesDiscarded", 0) > 0
            termination = {
                "reason": child_record.get("terminationReason"),
                "returnCode": child_record.get("returnCode"),
                "rssLimitMiB": child_record.get("rssLimitMiB"),
                "peakObservedRssBytes": child_record.get("peakObservedRssBytes"),
            }
        except (OSError, ValueError):
            was_truncated = log_size >= LOG_LIMIT
            termination = None
        return {
            "runId": run_id,
            "kind": manifest.get("kind"),
            "status": manifest.get("status"),
            "currentStepId": manifest.get("currentStepId"),
            "completedStepIds": manifest.get("completedStepIds", []),
            "stepTotal": sum(1 for step in manifest.get("normalizedProject", {}).get("steps", []) if step.get("enabled")) if manifest.get("kind") == "simulate" else 0,
            "startedAt": manifest.get("startedAt"),
            "elapsedSeconds": round(max(0.0, time.time() - calendar.timegm(time.strptime(manifest["startedAt"], "%Y-%m-%dT%H:%M:%SZ"))), 3) if manifest.get("status") in ACTIVE and manifest.get("startedAt") else manifest.get("elapsedSeconds", 0),
            "requestedInputHash": manifest.get("requestedInputHash"),
            "executionHash": manifest.get("executionHash"),
            "runMode": manifest.get("runMode", "from-initial"),
            "sliceRequest": manifest.get("sliceRequest"),
            "initialState": manifest.get("initialState"),
            "fluxResult": manifest.get("fluxResult"),
            "intermediateOutputs": manifest.get("intermediateOutputs", []),
            "failureReason": manifest.get("failureReason"),
            "termination": termination,
            "log": {"cursor": cursor, "nextCursor": next_cursor, "truncated": was_truncated, "text": data.decode("utf-8", errors="replace")},
            "snapshots": manifest.get("snapshots", []),
            "actualExecution": manifest.get("actualExecution", []),
            "completedAt": manifest.get("finishedAt"),
        }

    def get_manifest(self, run_id: str) -> dict[str, Any]:
        return self._manifest(run_id)


def requested_input_hash_from_dict(project: dict[str, Any]) -> str:
    """Revalidate a normalized stored project before hashing it."""
    from .models import parse_project
    return requested_input_hash(parse_project(project))

