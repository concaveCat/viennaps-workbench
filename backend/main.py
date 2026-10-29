"""FastAPI service for the localhost ViennaPS Workbench."""

from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.background import BackgroundTask

from . import storage
from . import materials as material_registry
from .models import Project, ProjectV6, ProjectV7, FluxRunRequest, SliceRequestV7, normalized_geometry_v3, parse_project, parse_replay, project_structure_hash, RunRequest
from .storage import atomic_json, read_json
from .streamzip import MAX_ARCHIVE, stream_zip_async
from .supervisor import ActiveRunError, ReplayMismatch, Supervisor
from .validation import (GPU_MULTIPARTICLE_FLUX_STRATEGY, GPU_MULTIPARTICLE_SPLIT_STRATEGY,
                         GPU_MULTIPARTICLE_NATIVE_STRATEGY, GPU_MULTIPARTICLE_NATIVE_FLUX_STRATEGY,
                         ProjectInputError, gpu_disk_verified,
                         validate_project, capability_verified, gpu_project_signature)
from .rates import uses_flux
from .specialized_models import specialized_catalog
from .runtime_limits import cpu_thread_limit
from .gpu_devices import cuda_devices


ROOT = storage.ROOT
API_GDS_MAX_BODY = 64 * 1024 * 1024
APP_VERSION = "0.1.0"
CAPABILITIES = ROOT / "docs" / "capabilities.json"
RESOURCE_POLICY = ROOT / "docs" / "resource-policy.json"
MATERIALS_CATALOG = ROOT / "docs" / "materials-catalog.json"
FRONTEND_DIST = ROOT / "frontend" / "dist"


def _version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def _json_project(project: Project) -> dict[str, Any]:
    return project.model_dump(mode="json", exclude_none=True)


def _validation_error(exc: ValidationError) -> JSONResponse:
    errors = []
    for item in exc.errors():
        path = ""
        for part in item["loc"]:
            path += f"[{part}]" if isinstance(part, int) else (str(part) if not path else f".{part}")
        errors.append({"path": path or "$", "message": item["msg"]})
    return JSONResponse(status_code=422, content={"detail": errors})


class ApiContentTypeMiddleware:
    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/") or scope.get("method") not in {"POST", "PUT", "PATCH"}:
            await self.app(scope, receive, send)
            return
        content_length = next((value.decode("latin1") for key, value in scope.get("headers", []) if key.lower() == b"content-length"), None)
        transfer_encoding = next((value.decode("latin1").lower() for key, value in scope.get("headers", []) if key.lower() == b"transfer-encoding"), "")
        path = scope.get("path", "")
        if path == "/api/gds-assets" and scope.get("method") == "POST":
            content_type = next((value.decode("latin1").split(";", 1)[0].strip().lower() for key, value in scope.get("headers", []) if key.lower() == b"content-type"), "")
            if content_type != "application/octet-stream":
                response = JSONResponse(status_code=415, content={"detail": "GDS uploads must use application/octet-stream"})
                await response(scope, receive, send)
                return
            if content_length is not None:
                try:
                    if int(content_length) > API_GDS_MAX_BODY:
                        await self._reject_oversized(scope, receive, send, maximum=API_GDS_MAX_BODY)
                        return
                except ValueError:
                    await self._reject_oversized(scope, receive, send, maximum=API_GDS_MAX_BODY)
                    return
            # The upload route streams into a temporary file and enforces its own cap.
            await self.app(scope, receive, send)
            return
        if path == "/api/continuation-package/import" and scope.get("method") == "POST":
            content_type = next((value.decode("latin1").split(";", 1)[0].strip().lower() for key, value in scope.get("headers", []) if key.lower() == b"content-type"), "")
            if content_type != "application/zip":
                response = JSONResponse(status_code=415, content={"detail": "continuation packages must use application/zip"})
                await response(scope, receive, send)
                return
            # The route consumes and caps the body incrementally; do not buffer a
            # potentially large native state package in middleware memory.
            await self.app(scope, receive, send)
            return
        bodyless_cancel = scope.get("method") == "POST" and path.startswith("/api/runs/") and path.endswith("/cancel")
        if bodyless_cancel and content_length in (None, "0") and not transfer_encoding:
            await self.app(scope, receive, send)
            return
        content_type = next((value.decode("latin1").split(";", 1)[0].strip().lower() for key, value in scope.get("headers", []) if key.lower() == b"content-type"), "")
        if content_type != "application/json":
            response = JSONResponse(status_code=415, content={"detail": "API request bodies must use application/json"})
            await response(scope, receive, send)
            return
        # General JSON request bodies have no fixed byte cap. Let the route
        # consume them directly without buffering a second copy in middleware.
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject_oversized(scope: dict[str, Any], receive: Any, send: Any, *, maximum: int) -> None:
        response = JSONResponse(status_code=413, content={"detail": f"request body exceeds the {maximum // (1024 * 1024)} MiB limit"})
        await response(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    lock_fd = storage.acquire_instance_lock()
    supervisor = Supervisor(lock_fd)
    app.state.supervisor = supervisor
    try:
        yield
    finally:
        # Lifespan shutdown is already draining requests. Keeping close
        # synchronous avoids leaving a default-executor thread behind while
        # the server's event loop is being destroyed.
        supervisor.close()


app = FastAPI(title="ViennaPS Workbench", version=APP_VERSION, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.add_middleware(ApiContentTypeMiddleware)


@app.middleware("http")
async def localhost_only(request: Request, call_next: Any):
    port = int(os.environ.get("WORKBENCH_PORT", "8767"))
    host_header = request.headers.get("host", "").strip().lower()
    expected_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    if host_header not in expected_hosts:
        return JSONResponse(status_code=400, content={"detail": "request Host must be the active loopback address and port"})
    origin = request.headers.get("origin")
    if origin:
        expected_origin = f"http://{host_header}"
        if origin.rstrip("/").lower() != expected_origin:
            return JSONResponse(status_code=403, content={"detail": "cross-origin requests are not allowed"})
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def _supervisor(request: Request) -> Supervisor:
    return request.app.state.supervisor


def _project_or_422(payload: Any, *, kind: str = "project") -> tuple[Project, dict[str, Any], list[str]]:
    try:
        project, warnings = validate_project(payload, kind=kind)
    except ProjectInputError as exc:
        raise HTTPException(status_code=422, detail=[{"path": issue.path, "message": issue.message} for issue in exc.issues]) from exc
    return project, _json_project(project), warnings


@app.get("/api/health")
async def health(request: Request) -> dict[str, Any]:
    supervisor = _supervisor(request)
    active = None
    if supervisor.active and supervisor._status(supervisor.active.run_id) in {"pending", "running", "cancelling"}:
        active = supervisor.active.run_id
    return {
        "status": "ok",
        "applicationVersion": APP_VERSION,
        "python": sys.version.split()[0],
        "viennaps": _version("viennaps"),
        "viennals": _version("viennals"),
        "platform": platform.platform(),
        "activeRunId": active,
    }


@app.get("/api/capabilities")
async def capabilities() -> dict[str, Any]:
    capability_data = read_json(CAPABILITIES)
    resource_policy = read_json(RESOURCE_POLICY)
    resource_policy["run"]["cpuThreads"].update(cpu_thread_limit())
    try:
        gpu_matrix = read_json(ROOT / "docs" / "stage9-evidence" / "gpu-capability-matrix.json")
        capability_data["gpuModelRows"] = gpu_matrix.get("rows", [])
        capability_data["knownGpuProfileFailures"] = gpu_matrix.get("knownGpuProfileFailures", [])
        capability_data["knownNativeFailures"] = gpu_matrix.get("knownNativeFailures", [])
        capability_data["knownEngineLimitations"] = gpu_matrix.get("knownEngineLimitations", [])
    except (OSError, ValueError, AttributeError):
        capability_data["gpuModelRows"] = []
        capability_data["knownGpuProfileFailures"] = []
        capability_data["knownNativeFailures"] = []
        capability_data["knownEngineLimitations"] = []
    return {"capabilities": capability_data, "resourcePolicy": resource_policy}


@app.get("/api/gpu-devices")
def gpu_devices() -> dict[str, Any]:
    """Return CUDA-capable NVIDIA devices seen by a bounded host probe."""
    return cuda_devices()


@app.get("/api/materials")
async def materials() -> dict[str, Any]:
    try:
        catalog = material_registry.material_catalog()
        rows = catalog.get("materials")
        if not isinstance(rows, list) or any(not isinstance(row.get("id"), int) or not isinstance(row.get("name"), str) or not isinstance(row.get("color"), str) for row in rows):
            raise ValueError("invalid catalog entries")
        return catalog
    except (OSError, ValueError, AttributeError) as exc:
        raise HTTPException(status_code=503, detail="verified native material catalog is unavailable") from exc


@app.get("/api/specialized-models")
async def specialized_models() -> dict[str, Any]:
    """Return only the checked ViennaPS 4.6.2 native model allowlist."""
    return specialized_catalog()


@app.post("/api/materials", status_code=201)
async def material_create(payload: Any = Body(...)) -> dict[str, Any]:
    try:
        return material_registry.create_material(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"path": "material", "message": str(exc)}) from exc
    except OSError as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc


@app.patch("/api/materials/{name}")
async def material_metadata_update(name: str, payload: Any = Body(...)) -> dict[str, Any]:
    try:
        return material_registry.update_material(name, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="custom material not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"path": "metadata", "message": str(exc)}) from exc
    except OSError as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc


@app.delete("/api/materials/{name}")
async def material_delete(name: str, payload: Any = Body(...)) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {"project"} or not isinstance(payload["project"], dict):
        raise HTTPException(status_code=422, detail="body must contain the current project")
    try:
        deleted = material_registry.delete_material(name, payload["project"])
        return {"deleted": True, "name": deleted["name"], "id": deleted["id"]}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="custom material not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc


@app.post("/api/gds-assets", status_code=201)
async def gds_asset_upload(request: Request, file_name: str = Query(default="layout.gds", min_length=1, max_length=255)) -> dict[str, Any]:
    from .gds import GdsFormatError, inspect_gds, parse_gds
    if "/" in file_name or "\\" in file_name or any(ord(value) < 32 for value in file_name):
        raise HTTPException(status_code=422, detail="GDS file name must be a simple file name")
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > API_GDS_MAX_BODY:
                raise HTTPException(status_code=413, detail="GDS file exceeds the 64 MiB upload limit")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid Content-Length") from exc
    storage.initialize()
    temp_dir = Path(tempfile.mkdtemp(prefix="gds-upload-", dir=storage.VAR))
    upload_path = temp_dir / "upload.gds"
    size = 0
    digest = __import__("hashlib").sha256()
    try:
        with upload_path.open("wb") as output:
            async for chunk in request.stream():
                size += len(chunk)
                if size > API_GDS_MAX_BODY:
                    raise HTTPException(status_code=413, detail="GDS file exceeds the 64 MiB upload limit")
                digest.update(chunk)
                output.write(chunk)
        try:
            data = upload_path.read_bytes()
            library = parse_gds(data)
            summary = inspect_gds(data)
        except (OSError, GdsFormatError, UnicodeDecodeError) as exc:
            raise HTTPException(status_code=422, detail=f"GDSII file is invalid or unsupported: {exc}") from exc
        asset_id = digest.hexdigest()
        destination = storage.gds_asset_path(asset_id)
        if destination.exists():
            if storage.file_sha256(destination) != asset_id:
                raise HTTPException(status_code=500, detail="content-addressed GDS asset store has a SHA-256 mismatch")
        else:
            storage.check_disk_preflight(projected_bytes=size)
            os.replace(upload_path, destination)
        metadata = {"assetId": asset_id, "fileName": file_name, **summary}
        atomic_json(storage.GDS_ASSETS / f"{asset_id}.json", metadata)
        return metadata
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@app.get("/api/gds-assets/{asset_id}")
async def gds_asset_get(asset_id: str) -> dict[str, Any]:
    from .gds import inspect_gds
    try:
        path = storage.resolve_gds_asset(asset_id)
        metadata_path = storage.GDS_ASSETS / f"{asset_id}.json"
        metadata = read_json(metadata_path) if metadata_path.is_file() else {"fileName": path.name}
        return {**metadata, "assetId": asset_id, **inspect_gds(path.read_bytes())}
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="GDS asset not found or SHA-256 mismatch") from exc


@app.post("/api/gds-assets/{asset_id}/inspect")
async def gds_asset_inspect(asset_id: str, payload: Any = Body(...)) -> dict[str, Any]:
    from .gds import GdsFormatError, parse_gds
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="inspection body must be a JSON object")
    try:
        source = storage.resolve_gds_asset(asset_id)
        library = parse_gds(source.read_bytes())
        structure = payload["structure"]
        available = library.effective_layers(structure)
        dimension = payload["dimension"]
        delta = float(payload["gridDelta"])
        unit_scale = float(payload.get("unitScale", 1.0))
        position = payload.get("position", [0.0, 0.0])
        selected_layers = payload.get("selectedLayers", available)
        boundary = payload.get("boundary", "reflective")
        x_extent = payload.get("xExtent")
        y_extent = payload.get("yExtent")
        probe_height = float(payload.get("probeHeight", max(delta * 2, 1.0)))
        if type(dimension) is not int or dimension not in (2, 3) or not isinstance(structure, str):
            raise ValueError("dimension must be 2 or 3 and structure must be a string")
        if not (0 < delta <= 0.5) or not (0 < unit_scale <= 1_000_000):
            raise ValueError("gridDelta or GDS unit scale is outside the application range")
        if boundary not in ("reflective", "periodic") or not isinstance(position, list) or len(position) != 2:
            raise ValueError("boundary or GDS XY placement is invalid")
        if x_extent is not None and (not isinstance(x_extent, (int, float)) or isinstance(x_extent, bool)
                                     or not __import__("math").isfinite(float(x_extent)) or float(x_extent) <= 0):
            raise ValueError("xExtent must be positive and finite")
        if y_extent is not None and (not isinstance(y_extent, (int, float)) or isinstance(y_extent, bool)
                                     or not __import__("math").isfinite(float(y_extent)) or float(y_extent) <= 0):
            raise ValueError("yExtent must be positive and finite")
        if boundary == "periodic" and dimension == 3 and (x_extent is None or y_extent is None):
            raise ValueError("periodic 3D GDS inspection requires xExtent and yExtent")
        if not isinstance(selected_layers, list) or not selected_layers or any(type(value) is not int for value in selected_layers):
            raise ValueError("selectedLayers must be a nonempty list of integer layer IDs")
        if len(set(selected_layers)) != len(selected_layers) or set(selected_layers) - set(available):
            raise ValueError("selectedLayers must be a unique subset of the selected structure's layers")
        if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not __import__("math").isfinite(float(value)) for value in position):
            raise ValueError("GDS XY placement must contain finite coordinates")
        if not __import__("math").isfinite(probe_height) or probe_height <= 0:
            raise ValueError("3D probe height must be positive and finite")
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=f"missing inspection input {exc.args[0]}") from exc
    except (FileNotFoundError, GdsFormatError, ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid GDS inspection input: {exc}") from exc

    temp_dir = Path(tempfile.mkdtemp(prefix="gds-inspect-", dir=storage.VAR))
    config_path = temp_dir / "input.json"
    output_path = temp_dir / "native-inspection.json"
    record_path = temp_dir / "child-record.json"
    log_path = temp_dir / "native.log"
    atomic_json(config_path, {"sourcePath": str(source), "assetId": asset_id, "structure": structure,
                              "dimension": dimension, "gridDelta": delta, "boundary": boundary,
                              "xExtent": float(x_extent) if x_extent is not None else None,
                              "yExtent": float(y_extent) if y_extent is not None else None,
                              "unitScale": unit_scale, "position": [float(value) for value in position],
                              "selectedLayers": selected_layers, "probeHeight": probe_height})
    python = str(Path(sys.executable).resolve())
    command = [python, str(ROOT / "scripts/bounded_child.py"), "--cwd", str(ROOT),
               "--record", str(record_path), "--log-path", str(log_path), "--parent-pid", str(os.getpid()),
               "--timeout-seconds", "30", "--max-rss-mib", "2048", "--max-log-bytes", "4194304",
               "--terminate-grace-seconds", "5", "--", python, str(ROOT / "worker/inspect_gds_native.py"),
               str(config_path), str(output_path)]
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=40, check=False)
        if result.returncode != 0 or not output_path.is_file():
            log = log_path.read_text(encoding="utf-8", errors="replace")[-4000:] if log_path.is_file() else result.stderr[-1000:]
            raise HTTPException(status_code=422, detail=f"ViennaPS native GDS inspection failed under bounded_child.py: {log or 'worker returned no inspection result'}")
        report = read_json(output_path)
        guard = read_json(record_path) if record_path.is_file() else {}
        return {"inspection": report, "boundedChild": {"status": guard.get("status"), "returnCode": guard.get("returnCode"),
                                                          "elapsedSeconds": guard.get("elapsedSeconds"), "peakRssBytes": guard.get("peakRssBytes"),
                                                          "logBytes": guard.get("logBytes")}}
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail="ViennaPS native GDS inspection exceeded its bounded wall-clock limit") from exc
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@app.post("/api/projects/validate")
async def validate_endpoint(payload: Any = Body(...)) -> dict[str, Any]:
    project, normalized, warnings = _project_or_422(payload)
    from .hashing import requested_input_hash
    return {"valid": True, "warnings": warnings, "requestedInputHash": requested_input_hash(project), "normalizedProject": normalized}


@app.get("/api/projects")
async def projects_list() -> dict[str, Any]:
    return {"projects": storage.list_projects()}


@app.post("/api/projects", status_code=201)
async def project_create(payload: Any = Body(...)) -> dict[str, Any]:
    _project, normalized, _warnings = _project_or_422(payload)
    try:
        storage.check_disk_preflight(projected_bytes=len(json.dumps(normalized, ensure_ascii=False).encode("utf-8")))
        return storage.create_project(normalized)
    except OSError as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc


@app.get("/api/projects/{project_id}")
async def project_get(project_id: str) -> dict[str, Any]:
    try:
        record = read_json(storage.project_path(project_id))
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    return {"id": record["id"], "project": record["project"], "updatedAt": record["updatedAt"]}


@app.put("/api/projects/{project_id}")
async def project_update(project_id: str, payload: Any = Body(...)) -> dict[str, Any]:
    _project, normalized, _warnings = _project_or_422(payload)
    try:
        storage.check_disk_preflight(projected_bytes=len(json.dumps(normalized, ensure_ascii=False).encode("utf-8")))
        record = storage.update_project(project_id, normalized)
        return {"id": record["id"], "project": record["project"], "updatedAt": record["updatedAt"]}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="project not found") from exc
    except OSError as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc


@app.post("/api/runs", status_code=202)
async def run_create(request: Request, payload: Any = Body(...)) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {"kind", "project"}:
        raise HTTPException(status_code=422, detail=[{"path": "$", "message": "body must contain exactly kind and project"}])
    try:
        run_request = RunRequest.model_validate(payload)
    except ValidationError as exc:
        return _validation_error(exc)
    try:
        project, normalized, _warnings = _project_or_422(run_request.project.model_dump(mode="python", exclude_none=True), kind=run_request.kind)
    except HTTPException:
        raise
    try:
        return _supervisor(request).start(run_request.kind, normalized)
    except ActiveRunError as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc), "runId": exc.run_id}) from exc
    except ReplayMismatch as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc


@app.post("/api/runs/flux", status_code=202)
async def flux_run_create(request: Request, payload: Any = Body(...)) -> dict[str, Any]:
    try:
        flux_request = FluxRunRequest.model_validate(payload)
    except ValidationError as exc:
        return _validation_error(exc)
    try:
        _parsed, normalized, _warnings = _project_or_422(flux_request.project.model_dump(mode="python", exclude_none=True))
    except HTTPException:
        raise
    step = next((item for item in normalized["steps"] if item["id"] == flux_request.stepId), None)
    if not step or not step.get("enabled") or not uses_flux(step):
        raise HTTPException(status_code=422, detail={"path": "stepId", "message": "select an enabled particle-process step with a nonzero rate relation"})
    try:
        return _supervisor(request).start("flux", normalized, analysis_step_id=flux_request.stepId)
    except ActiveRunError as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc), "runId": exc.run_id}) from exc
    except ReplayMismatch as exc:
        raise HTTPException(status_code=422, detail={"path": "stepId", "message": str(exc)}) from exc
    except OSError as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc


@app.post("/api/runs/slice", status_code=202)
async def slice_run_create(request: Request, payload: Any = Body(...)) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) != {"project", "sliceRequest"}:
        raise HTTPException(status_code=422, detail="body must contain exactly project and sliceRequest")
    try:
        slice_request = SliceRequestV7.model_validate(payload["sliceRequest"])
        parsed_project = parse_project(payload["project"])
    except ValidationError as exc:
        return _validation_error(exc)
    if parsed_project.schemaVersion != 7 or parsed_project.dimension != 3:
        raise HTTPException(status_code=422, detail={"path":"project", "message":"ViennaPS Slice requires a schema-7 3D geometry project"})
    project_payload = parsed_project.model_dump(mode="python", exclude_none=True)
    project_payload["steps"] = []
    try:
        _project, normalized, _warnings = _project_or_422(project_payload)
        return _supervisor(request).start("slice", normalized, slice_request=slice_request.model_dump(mode="json"))
    except HTTPException:
        raise
    except ActiveRunError as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc), "runId": exc.run_id}) from exc
    except ReplayMismatch as exc:
        raise HTTPException(status_code=422, detail={"path":"sliceRequest", "message":str(exc)}) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail="slice project references an unavailable native Domain state or GDS asset") from exc
    except OSError as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc


@app.post("/api/runs/replay", status_code=202)
async def run_replay(request: Request, payload: Any = Body(...)) -> dict[str, Any]:
    try:
        envelope = parse_replay(payload)
    except ValidationError as exc:
        return _validation_error(exc)
    project_payload = envelope.normalizedProject.model_dump(mode="python", exclude_none=True)
    analysis_mode = getattr(envelope, "analysisMode", "process")
    analysis_step_id = getattr(envelope, "analysisStepId", None)
    has_enabled = any(step["enabled"] for step in project_payload["steps"])
    kind = "flux" if analysis_mode == "flux" else "slice" if analysis_mode == "slice" else "simulate" if has_enabled else "preview"
    project, normalized, _warnings = _project_or_422(project_payload, kind="simulate" if kind == "flux" else kind)
    if envelope.schemaVersion != project.schemaVersion:
        raise HTTPException(status_code=422,detail={"path":"schemaVersion","message":"replay envelope and normalized project versions must match"})
    requirements = envelope.executionRequirements
    installed_ps = _version("viennaps")
    installed_ls = _version("viennals")
    if requirements.viennapsVersion != installed_ps or requirements.viennalsVersion != installed_ls:
        raise HTTPException(status_code=422, detail={"path": "executionRequirements", "message": f"requires ViennaPS {requirements.viennapsVersion} / ViennaLS {requirements.viennalsVersion}; installed is {installed_ps} / {installed_ls}"})
    if requirements.cpuThreads != project_payload["solver"]["cpuThreads"]:
        raise HTTPException(status_code=422, detail={"path": "executionRequirements.cpuThreads", "message": "thread count does not match the normalized project"})
    if project_payload["solver"]["random"]["mode"] != "fixed":
        raise HTTPException(status_code=422, detail={"path": "normalizedProject.solver.random", "message": "replay project must store the actual fixed seed"})
    if analysis_mode == "flux":
        selected_flux = next((step for step in project_payload["steps"] if step["id"] == analysis_step_id), None)
        if not selected_flux or not selected_flux.get("enabled") or not uses_flux(selected_flux):
            raise HTTPException(status_code=422, detail={"path": "analysisStepId", "message": "flux replay requires an enabled step whose installed ViennaPS model calculates particle flux"})
        particle_ids = {analysis_step_id}
    else:
        particle_ids = {step["id"] for step in project_payload["steps"] if step["enabled"] and uses_flux(step)}
    requirements_by_id: dict[str, dict[str, Any]] = {}
    seeds = set()
    for item in requirements.steps:
        allowed_engines = {"CPU_DISK","GPU_DISK"} if envelope.schemaVersion==1 else {"CPU_DISK","CPU_TRIANGLE","GPU_DISK","GPU_TRIANGLE"}
        if item.engine not in allowed_engines:
            raise HTTPException(status_code=422, detail={"path": "executionRequirements.steps", "message": "particle replay engine is not enabled for this schema and dimension"})
        if envelope.schemaVersion >= 3:
            requests_gpu = project_payload["solver"]["enginePreference"] == "gpu"
            requires_gpu = item.engine.startswith("GPU_")
            if requests_gpu != requires_gpu:
                expected = "GPU" if requests_gpu else "CPU"
                raise HTTPException(status_code=422, detail={"path": "executionRequirements.steps", "message": f"replay engine must match normalizedProject.solver.enginePreference ({expected})"})
        if envelope.schemaVersion in (2,3,4,5,6,7):
            step=next((s for s in project_payload["steps"] if s["id"]==item.stepId),None)
            replay_dimension=project_payload["dimension"]
            boundary=project_payload["domain"].get("boundaryXY",project_payload["domain"].get("xBoundary"))
            capability_model = f"specialized:{step['nativeModel']}" if step and step.get("model") == "specialized" else step["model"] if step else ""
            signature = gpu_project_signature(project_payload, step) if step else {}
            ion_count = sum(row.get("kind") == "ion" for row in step.get("species", [])) if step else 0
            multi_ion_gpu = (project_payload["schemaVersion"] == 7 and item.engine == "GPU_DISK"
                             and capability_model == "multiParticle" and ion_count > 1)
            valid_strategy = (
                item.executionStrategy in {GPU_MULTIPARTICLE_SPLIT_STRATEGY, GPU_MULTIPARTICLE_NATIVE_STRATEGY}
                and item.fluxStrategy is None if multi_ion_gpu and analysis_mode != "flux" else
                item.fluxStrategy in {GPU_MULTIPARTICLE_FLUX_STRATEGY, GPU_MULTIPARTICLE_NATIVE_FLUX_STRATEGY}
                and item.executionStrategy is None if multi_ion_gpu and analysis_mode == "flux" else
                item.executionStrategy is None and item.fluxStrategy is None)
            if not valid_strategy:
                raise HTTPException(status_code=422, detail={
                    "path": "executionRequirements.steps",
                    "message": "GPU MultiParticle replay must record the strategy for its process or single-pass flux mode",
                })
            if not step or (not item.engine.startswith("GPU_") and not capability_verified(replay_dimension,capability_model,step["operation"],boundary,item.engine,
                                                   schema_version=envelope.schemaVersion,
                                                   execution_strategy=item.executionStrategy,
                                                   flux_strategy=item.fluxStrategy, **signature)):
                raise HTTPException(status_code=422,detail={"path":"executionRequirements.steps","message":"unverified replay engine, geometry or material combination"})
        if item.stepId in requirements_by_id:
            raise HTTPException(status_code=422, detail={"path": "executionRequirements.steps", "message": "duplicate stepId"})
        requirements_by_id[item.stepId] = item.model_dump(mode="json", exclude_none=True)
        seeds.add(item.seed)
    if set(requirements_by_id) != particle_ids:
        raise HTTPException(status_code=422, detail={"path": "executionRequirements.steps", "message": "requirements must contain exactly the enabled singleParticle steps"})
    replay_engines = {item["engine"] for item in requirements_by_id.values()}
    if envelope.schemaVersion==1 and len(replay_engines) > 1:
        raise HTTPException(status_code=422, detail={"path": "executionRequirements.steps", "message": "v1 requires one concrete engine for all particle steps"})
    if len(seeds) > 1:
        raise HTTPException(status_code=422, detail={"path": "executionRequirements.steps", "message": "v1 requires the same explicit seed for every particle step"})
    if particle_ids:
        replay_seed = next(iter(seeds))
        if replay_seed != project_payload["solver"]["random"]["seed"]:
            raise HTTPException(status_code=422, detail={"path": "normalizedProject.solver.random.seed", "message": "seed does not match the step requirements"})
        replay_engine = next(iter(replay_engines))
        expected_preference = "gpu" if replay_engine == "GPU_DISK" else "cpu"
        if envelope.schemaVersion==1 and project_payload["solver"]["enginePreference"] != expected_preference:
            raise HTTPException(status_code=422, detail={"path": "normalizedProject.solver.enginePreference", "message": f"replay project must explicitly request {expected_preference}"})
    elif requirements.steps:
        raise HTTPException(status_code=422, detail={"path": "executionRequirements.steps", "message": "no particle steps are enabled"})
    replay_info = {"viennapsVersion": requirements.viennapsVersion, "viennalsVersion": requirements.viennalsVersion,
                   "stepsById": requirements_by_id, "seed": next(iter(seeds), project_payload["solver"]["random"]["seed"])}
    if requirements.nativeEngineFingerprint is not None:
        replay_info["nativeEngineFingerprint"] = requirements.nativeEngineFingerprint
    if envelope.schemaVersion in (6,7):
        replay_info["replayMode"] = envelope.replayMode
    if envelope.schemaVersion == 7:
        replay_info.update({"analysisMode": analysis_mode, "analysisStepId": analysis_step_id})
        if analysis_mode == "slice":
            replay_info["sliceRequest"] = envelope.sliceRequest.model_dump(mode="json")
    try:
        return _supervisor(request).start(kind, normalized, replay=replay_info)
    except ActiveRunError as exc:
        raise HTTPException(status_code=409, detail={"message": str(exc), "runId": exc.run_id}) from exc
    except ReplayMismatch as exc:
        raise HTTPException(status_code=422, detail={"path": "executionRequirements", "message": str(exc)}) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail="replay requires a native Domain state that is not available in this workbench") from exc
    except OSError as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc


@app.get("/api/runs")
async def runs_list() -> dict[str, Any]:
    runs = []
    for path in storage.RUNS.iterdir():
        if not path.is_dir():
            continue
        try:
            item = read_json(path / "manifest.json")
            runs.append({"runId": item["runId"], "kind": item["kind"], "status": item["status"],
                         "createdAt": item.get("createdAt"), "finishedAt": item.get("finishedAt"),
                         "requestedInputHash": item.get("requestedInputHash"), "failureReason": item.get("failureReason")})
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return {"runs": sorted(runs, key=lambda item: item.get("createdAt") or "", reverse=True)}


@app.get("/api/runs/{run_id}")
async def run_get(run_id: str, request: Request, cursor: int = Query(0, ge=0)) -> dict[str, Any]:
    try:
        return _supervisor(request).get_run(run_id, cursor=cursor)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


@app.post("/api/runs/{run_id}/cancel")
async def run_cancel(run_id: str, request: Request) -> dict[str, Any]:
    try:
        return _supervisor(request).cancel(run_id)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


@app.get("/api/runs/{run_id}/snapshots/{step_id}")
async def snapshot_get(run_id: str, step_id: str) -> dict[str, Any]:
    try:
        run_dir = storage.run_path(run_id)
        manifest = read_json(run_dir / "manifest.json")
        if not any(item.get("stepId") == step_id for item in manifest.get("snapshots", [])):
            raise FileNotFoundError("snapshot is not listed in the manifest")
        filename = f"{step_id}.json"
        if Path(filename).name != filename:
            raise FileNotFoundError("invalid snapshot name")
        if not (run_dir / filename).is_file():
            from . import snapshots as snapshot_store
            snapshot_store.ensure_materialized(run_dir, step_id)
        return read_json(run_dir / filename)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="snapshot not found") from exc


@app.get("/api/runs/{run_id}/flux-result")
async def flux_result_get(run_id: str) -> dict[str, Any]:
    try:
        run_dir = storage.run_path(run_id)
        manifest = read_json(run_dir / "manifest.json")
        reference = manifest.get("fluxResult")
        if not isinstance(reference, dict) or reference.get("fileName") != "flux.json":
            raise FileNotFoundError("run has no stored native flux result")
        return read_json(run_dir / "flux.json")
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="native flux result not found") from exc


@app.get("/api/runs/{run_id}/intermediate-outputs/{step_id}/{file_name}")
async def intermediate_output_get(run_id: str, step_id: str, file_name: str) -> FileResponse:
    try:
        run_dir = storage.run_path(run_id)
        manifest = read_json(run_dir / "manifest.json")
        if not step_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in step_id):
            raise FileNotFoundError("invalid intermediate step ID")
        if Path(file_name).name != file_name or file_name in {".", ".."} or "\\" in file_name:
            raise FileNotFoundError("invalid intermediate output name")
        expected_name = f"intermediate/{step_id}/{file_name}"
        reference = next((item for item in manifest.get("intermediateOutputs", [])
                         if isinstance(item, dict) and item.get("stepId") == step_id
                         and item.get("fileName") == expected_name), None)
        if reference is None:
            raise FileNotFoundError("intermediate output is not listed in the run manifest")
        path = run_dir / expected_name
        resolved = path.resolve(strict=True)
        resolved.relative_to(run_dir.resolve())
        if path.is_symlink() or not resolved.is_file() or resolved.stat().st_size > 128 * 1024 * 1024:
            raise FileNotFoundError("intermediate output is invalid or exceeds the download limit")
        if storage.file_sha256(resolved) != reference.get("sha256"):
            raise FileNotFoundError("intermediate output hash does not match the run manifest")
        media_type = "application/vnd.vtk+xml" if reference.get("format") == "VTP" else "application/octet-stream"
        return FileResponse(resolved, media_type=media_type, filename=file_name)
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="native intermediate output not found") from exc


@app.get("/api/runs/{run_id}/artifacts/{step_id}")
async def native_artifact_download(run_id: str, step_id: str, fileName: str = Query(min_length=1, max_length=512)) -> FileResponse:
    try:
        run_dir = storage.run_path(run_id)
        manifest = read_json(run_dir / "manifest.json")
        snapshot = next((item for item in manifest.get("snapshots", []) if item.get("stepId") == step_id), None)
        artifact = next((item for item in (snapshot or {}).get("outputs", []) if item.get("fileName") == fileName), None)
        if artifact is None:
            raise FileNotFoundError("artifact is not listed in the selected snapshot")
        relative = Path(fileName)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts) or "\\" in fileName:
            raise FileNotFoundError("artifact path is invalid")
        path = run_dir / relative
        resolved = path.resolve(strict=True)
        resolved.relative_to(run_dir.resolve())
        if path.is_symlink() or resolved.stat().st_size != artifact.get("sizeBytes"):
            raise FileNotFoundError("artifact is missing or its size changed")
        if storage.file_sha256(resolved) != artifact.get("sha256"):
            raise FileNotFoundError("artifact SHA-256 does not match the run manifest")
        media_type = "application/json" if artifact.get("format") == "JSON" else "application/vnd.vtk+xml" if artifact.get("format") in {"VTP", "VTU"} else "application/octet-stream"
        return FileResponse(resolved, media_type=media_type, filename=resolved.name, headers={"Cache-Control": "no-store"})
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="native output artifact not found or failed integrity verification") from exc


@app.get("/api/runs/{run_id}/continuation-project/{step_id}")
async def continuation_project_get(run_id: str, step_id: str) -> dict[str, Any]:
    """Create an empty schema-6 recipe whose initial Domain is a saved native state."""
    try:
        run_dir = storage.run_path(run_id)
        manifest = read_json(run_dir / "manifest.json")
        if manifest.get("status") not in {"succeeded", "failed", "timed_out", "cancelled", "interrupted"}:
            raise HTTPException(status_code=409, detail="continuation is available after the run reaches a terminal state")
        row = next((item for item in manifest.get("snapshots", []) if item.get("stepId") == step_id), None)
        state = row.get("nativeState") if row else None
        if not isinstance(state, dict):
            raise HTTPException(status_code=409, detail="this historical snapshot has no saved native Domain state")
        filename = state.get("fileName")
        if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith(".vpsd"):
            raise HTTPException(status_code=409, detail="historical native Domain state reference is invalid")
        source = run_dir / filename
        if not source.is_file() or storage.file_sha256(source) != state.get("stateId"):
            raise HTTPException(status_code=409, detail="historical native Domain state is missing or has a SHA-256 mismatch")
        if state.get("viennapsVersion") != _version("viennaps") or state.get("viennalsVersion") != _version("viennals"):
            raise HTTPException(status_code=409, detail="historical native Domain state engine versions do not match the installed ViennaPS/ViennaLS")
        original = parse_project(manifest["normalizedProject"])
        geometry = normalized_geometry_v3(original)
        expected_structure = project_structure_hash(original.dimension, original.units, original.domain, geometry)
        if state.get("structureHash") != expected_structure:
            raise HTTPException(status_code=409, detail="saved native state structure fingerprint does not match its source project")
        storage.register_domain_state(source, state["stateId"])
        reference = {key: value for key, value in state.items() if key not in {"fileName", "sourceRunId", "sourceStepId"}}
        solver = original.solver.model_dump(mode="json", exclude_none=True)
        solver["enginePreference"] = "cpu"
        result = {
            "schemaVersion": 7 if original.schemaVersion == 7 else 6,
            "name": f"{original.name[:72]} · 新工艺" if len(original.name) <= 72 else f"{original.name[:72]}新工艺",
            "dimension": original.dimension,
            "units": original.units.model_dump(mode="json"),
            "domain": original.domain.model_dump(mode="json"),
            "geometry": geometry,
            "steps": [],
            "solver": solver,
            "continuationState": reference,
        }
        if original.dimension == 2:
            result["view"] = original.view.model_dump(mode="json")
        else:
            result["view3D"] = original.view3D.model_dump(mode="json")
        _project, normalized, warnings = _project_or_422(result)
        return {"project": normalized, "continuation": {"sourceRunId": run_id, "sourceStepId": step_id, "engineVersions": {"viennaps": state["viennapsVersion"], "viennals": state["viennalsVersion"]}}, "warnings": warnings}
    except HTTPException:
        raise
    except (FileNotFoundError, OSError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=404, detail="saved continuation state not found") from exc


@app.post("/api/continuation-package/export")
async def continuation_package_export(payload: Any = Body(...)):
    if not isinstance(payload, dict) or set(payload) not in ({"project"}, {"project", "replay"}):
        raise HTTPException(status_code=422, detail="body must contain project and optional replay")
    _project, normalized, _warnings = _project_or_422(payload["project"])
    reference = normalized.get("continuationState")
    gds_geometry = normalized.get("geometry", {})
    gds_asset_id = gds_geometry.get("assetId") if gds_geometry.get("kind") == "gds" else None
    gds_source = None
    if reference is None and gds_asset_id is None:
        raise HTTPException(status_code=422, detail="portable project packages require a GDS asset or a saved native Domain state")
    source = None
    if reference is not None:
        if normalized.get("schemaVersion") not in (6,7):
            raise HTTPException(status_code=422, detail="portable native state packages require a schemaVersion 6 or 7 project")
        if reference["viennapsVersion"] != _version("viennaps") or reference["viennalsVersion"] != _version("viennals"):
            raise HTTPException(status_code=409, detail="native Domain state engine versions do not match the installed ViennaPS/ViennaLS")
        try:
            source = storage.resolve_domain_state(reference)
            storage.register_domain_state(source, reference["stateId"])
        except (FileNotFoundError, OSError) as exc:
            raise HTTPException(status_code=409, detail="referenced native Domain state is unavailable or has a SHA-256 mismatch") from exc
    if gds_asset_id is not None:
        try:
            gds_source = storage.resolve_gds_asset(gds_asset_id)
        except (FileNotFoundError, OSError) as exc:
            raise HTTPException(status_code=409, detail="referenced GDS asset is unavailable or has a SHA-256 mismatch") from exc

    replay_document = None
    if "replay" in payload:
        try:
            replay = parse_replay(payload["replay"])
        except ValidationError as exc:
            return _validation_error(exc)
        replay_project = replay.normalizedProject.model_dump(mode="json", exclude_none=True)
        if reference is not None:
            if replay.schemaVersion not in (6,7) or replay.normalizedProject.continuationState is None or replay.normalizedProject.continuationState.stateId != reference["stateId"]:
                raise HTTPException(status_code=422, detail="replay package must reference the same native Domain state")
        elif replay_project.get("geometry", {}).get("assetId") != gds_asset_id:
            raise HTTPException(status_code=422, detail="replay package must reference the same GDS source asset")
        if replay.executionRequirements.viennapsVersion != _version("viennaps") or replay.executionRequirements.viennalsVersion != _version("viennals"):
            raise HTTPException(status_code=409, detail="replay engine versions do not match the installed ViennaPS/ViennaLS")
        replay_document = replay.model_dump(mode="json", exclude_none=True)
        if replay_document["normalizedProject"].get("continuationState") is not None:
            replay_document["normalizedProject"]["continuationState"].pop("sourceRunId", None)
            replay_document["normalizedProject"]["continuationState"].pop("sourceStepId", None)

    if normalized.get("continuationState") is not None:
        normalized["continuationState"].pop("sourceRunId", None)
        normalized["continuationState"].pop("sourceStepId", None)
    temp_dir = Path(tempfile.mkdtemp(prefix="continuation-package-", dir=storage.VAR))
    project_file = temp_dir / "project.json"
    project_file.write_text(json.dumps(normalized, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    entries = [("project.json", project_file)]
    if source is not None:
        entries.append(("domain-state.vpsd", source))
    if gds_source is not None:
        entries.append((f"assets/{gds_asset_id}.gds", gds_source))
    if replay_document is not None:
        replay_file = temp_dir / "replay-project.json"
        replay_file.write_text(json.dumps(replay_document, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        entries.append(("replay-project.json", replay_file))
    package_key = reference["stateId"][:12] if reference is not None else gds_asset_id[:12]
    filename = f"viennaps-project-{package_key}.zip"
    return StreamingResponse(stream_zip_async(entries), media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'},
                             background=BackgroundTask(shutil.rmtree, temp_dir, ignore_errors=True))


@app.post("/api/continuation-package/import")
async def continuation_package_import(request: Request) -> dict[str, Any]:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_ARCHIVE:
                raise HTTPException(status_code=413, detail="continuation package exceeds the 512 MiB archive limit")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid Content-Length") from exc
    temp_dir = Path(tempfile.mkdtemp(prefix="continuation-import-", dir=storage.VAR))
    archive_path = temp_dir / "upload.zip"
    try:
        size = 0
        with archive_path.open("wb") as output:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_ARCHIVE:
                    raise HTTPException(status_code=413, detail="continuation package exceeds the 512 MiB archive limit")
                output.write(chunk)
        if not size:
            raise HTTPException(status_code=400, detail="continuation package is empty")
        try:
            archive = zipfile.ZipFile(archive_path)
        except zipfile.BadZipFile as exc:
            raise HTTPException(status_code=422, detail="continuation package is not a valid ZIP archive") from exc
        with archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            import re
            asset_names = [name for name in names if re.fullmatch(r"assets/[a-f0-9]{64}\.gds", name)]
            allowed = {"project.json", "domain-state.vpsd", "replay-project.json", *asset_names}
            if (len(names) != len(set(names)) or "project.json" not in names or set(names) - allowed
                    or len(asset_names) > 1):
                raise HTTPException(status_code=422, detail="package must contain project.json and only known native state, replay, and content-addressed GDS entries")
            if sum(item.file_size for item in infos) > MAX_ARCHIVE:
                raise HTTPException(status_code=413, detail="expanded continuation package exceeds the 512 MiB archive limit")
            by_name = {item.filename: item for item in infos}
            for item in infos:
                mode = (item.external_attr >> 16) & 0xFFFF
                if item.is_dir() or stat.S_ISLNK(mode) or item.flag_bits & 1:
                    raise HTTPException(status_code=422, detail="directories, symlinks and encrypted ZIP entries are not allowed")
            project_info = by_name["project.json"]
            try:
                project_payload = json.loads(archive.read(project_info).decode("utf-8"), parse_constant=lambda item: (_ for _ in ()).throw(ValueError(f"invalid JSON constant {item}")))
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail="project.json is not valid UTF-8 JSON") from exc
            if not isinstance(project_payload, dict):
                raise HTTPException(status_code=422, detail="package project must be a JSON object")
            try:
                parsed_project = parse_project(project_payload)
            except ValidationError as exc:
                return _validation_error(exc)
            normalized_pre_asset = parsed_project.model_dump(mode="json", exclude_none=True)
            geometry = normalized_pre_asset.get("geometry", {})
            asset_id = geometry.get("assetId") if geometry.get("kind") == "gds" else None
            state_payload = project_payload.get("continuationState")
            if state_payload is not None and (project_payload.get("schemaVersion") not in (6,7) or not isinstance(state_payload, dict)):
                raise HTTPException(status_code=422, detail="native Domain state packages require schemaVersion 6 or 7 and a valid continuationState")
            expected_asset_name = f"assets/{asset_id}.gds" if asset_id is not None else None
            if (asset_id is not None and asset_names != [expected_asset_name]) or (asset_id is None and asset_names):
                raise HTTPException(status_code=422, detail="package GDS asset path must exactly match the project assetId")
            if state_payload is not None and "domain-state.vpsd" not in names:
                raise HTTPException(status_code=422, detail="project continuationState requires domain-state.vpsd")
            if state_payload is None and "domain-state.vpsd" in names:
                raise HTTPException(status_code=422, detail="package native Domain state is not referenced by project.json")
            if state_payload is None and asset_id is None:
                raise HTTPException(status_code=422, detail="package must contain a GDS asset or a referenced native Domain state")
            if state_payload is not None:
                project_payload["continuationState"].pop("sourceRunId", None)
                project_payload["continuationState"].pop("sourceStepId", None)
            if asset_id is not None:
                asset_info = by_name[expected_asset_name]
                if asset_info.file_size <= 0 or asset_info.file_size > API_GDS_MAX_BODY:
                    raise HTTPException(status_code=413, detail="packaged GDS asset must be between 1 byte and 64 MiB")
                asset_temp = temp_dir / "asset.gds"
                copied = 0
                with archive.open(asset_info, "r") as source_stream, asset_temp.open("wb") as target_stream:
                    while block := source_stream.read(1024 * 1024):
                        copied += len(block)
                        if copied > API_GDS_MAX_BODY:
                            raise HTTPException(status_code=413, detail="expanded GDS asset exceeds the 64 MiB limit")
                        target_stream.write(block)
                if copied != asset_info.file_size or storage.file_sha256(asset_temp) != asset_id:
                    raise HTTPException(status_code=422, detail="packaged GDS asset does not match project geometry.assetId")
                destination = storage.gds_asset_path(asset_id)
                if destination.exists() and storage.file_sha256(destination) != asset_id:
                    raise HTTPException(status_code=500, detail="content-addressed GDS asset store has a SHA-256 mismatch")
                if not destination.exists():
                    os.replace(asset_temp, destination)
                from .gds import inspect_gds
                gds_metadata = {"assetId":asset_id,"fileName":geometry.get("fileName", "layout.gds"), **inspect_gds(destination.read_bytes())}
                atomic_json(storage.GDS_ASSETS / f"{asset_id}.json", gds_metadata)
            _project, normalized, _warnings = _project_or_422(project_payload)
            state_ref = normalized.get("continuationState")
            state_path = None
            if state_ref is not None:
                if state_ref["viennapsVersion"] != _version("viennaps") or state_ref["viennalsVersion"] != _version("viennals"):
                    raise HTTPException(status_code=409, detail="native Domain state engine versions do not match the installed ViennaPS/ViennaLS")
                state_info = by_name["domain-state.vpsd"]
                if state_info.file_size <= 0:
                    raise HTTPException(status_code=422, detail="domain-state.vpsd is empty")
                state_path = temp_dir / "domain-state.vpsd"
                copied = 0
                with archive.open(state_info, "r") as source_stream, state_path.open("wb") as target_stream:
                    while block := source_stream.read(1024 * 1024):
                        copied += len(block)
                        if copied > MAX_ARCHIVE:
                            raise HTTPException(status_code=413, detail="expanded native Domain state exceeds the 512 MiB limit")
                        target_stream.write(block)
                if copied != state_info.file_size or storage.file_sha256(state_path) != state_ref["stateId"]:
                    raise HTTPException(status_code=422, detail="domain-state.vpsd does not match continuationState.stateId")
            replay_document = None
            if "replay-project.json" in by_name:
                replay_info = by_name["replay-project.json"]
                try:
                    replay_payload = json.loads(archive.read(replay_info).decode("utf-8"), parse_constant=lambda item: (_ for _ in ()).throw(ValueError(f"invalid JSON constant {item}")))
                    replay = parse_replay(replay_payload)
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError, ValidationError) as exc:
                    raise HTTPException(status_code=422, detail="replay-project.json is invalid") from exc
                if state_ref is not None:
                    if replay.schemaVersion not in (6,7) or replay.normalizedProject.continuationState is None or replay.normalizedProject.continuationState.stateId != state_ref["stateId"]:
                        raise HTTPException(status_code=422, detail="replay project must reference the packaged native Domain state")
                elif replay.normalizedProject.model_dump(mode="json", exclude_none=True).get("geometry", {}).get("assetId") != asset_id:
                    raise HTTPException(status_code=422, detail="replay project must reference the packaged GDS asset")
                if replay.executionRequirements.viennapsVersion != _version("viennaps") or replay.executionRequirements.viennalsVersion != _version("viennals"):
                    raise HTTPException(status_code=409, detail="replay engine versions do not match the installed ViennaPS/ViennaLS")
                replay_document = replay.model_dump(mode="json", exclude_none=True)
            if state_path is not None:
                storage.register_domain_state(state_path, state_ref["stateId"])
            return {"project": normalized, "replay": replay_document}
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@app.get("/api/runs/{run_id}/replay-project")
async def replay_project_get(run_id: str) -> dict[str, Any]:
    try:
        return read_json(storage.run_path(run_id) / "replay-project.json")
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="replay project not found") from exc


def _archive_entries(run_dir: Path, manifest: dict[str, Any]) -> list[tuple[str, Path]]:
    candidates = [(name, run_dir / name) for name in ("project.json", "replay-project.json", "manifest.json", "run.log")]
    gds_asset_id = manifest.get("gdsAssetId")
    if isinstance(gds_asset_id, str) and len(gds_asset_id) == 64 and (run_dir / "gds-source.gds").is_file():
        candidates.append((f"assets/{gds_asset_id}.gds", run_dir / "gds-source.gds"))
        candidates.extend((name, run_dir / name) for name in ("gds-selected.gds", "gds-transform.json"))
    if (run_dir / "domain-state.vpsd").is_file():
        candidates.append(("domain-state.vpsd", run_dir / "domain-state.vpsd"))
    flux_result = manifest.get("fluxResult")
    if isinstance(flux_result, dict):
        for key in ("fileName", "vtk"):
            name = flux_result.get(key)
            if isinstance(name, str) and Path(name).name == name and name in {"flux.json", "flux.vtp"}:
                candidates.append((name, run_dir / name))
    for output in manifest.get("intermediateOutputs", []):
        name = output.get("fileName") if isinstance(output, dict) else None
        if not isinstance(name, str) or "\\" in name:
            continue
        relative = Path(name)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            continue
        path = run_dir / relative
        try:
            path.resolve().relative_to(run_dir.resolve())
        except (OSError, ValueError):
            continue
        candidates.append((relative.as_posix(), path))
    for item in manifest.get("snapshots", []):
        for key in ("snapshot", "vtk"):
            name = item.get(key)
            if not isinstance(name, str) or Path(name).name != name:
                continue
            candidates.append((name, run_dir / name))
        for output in item.get("outputs", []):
            name = output.get("fileName") if isinstance(output, dict) else None
            if not isinstance(name, str) or "\\" in name:
                continue
            relative = Path(name)
            if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
                continue
            path = run_dir / relative
            try:
                path.resolve().relative_to(run_dir.resolve())
            except (OSError, ValueError):
                continue
            if (not path.is_symlink() and path.is_file()
                    and storage.file_sha256(path) == output.get("sha256")):
                candidates.append((relative.as_posix(), path))
        native_state = item.get("nativeState")
        if isinstance(native_state, dict):
            name = native_state.get("fileName")
            if isinstance(name, str) and Path(name).name == name and name.endswith(".vpsd"):
                candidates.append((name, run_dir / name))
    unique: dict[str, Path] = {}
    total = 0
    budget = 22
    for name, path in candidates:
        if name in unique or not path.is_file():
            continue
        size = path.stat().st_size
        encoded_length = len(name.encode("utf-8"))
        total += size
        budget += size + (size >> 12) + (size >> 14) + (size >> 25) + 13 + 92 + encoded_length * 2
        unique[name] = path
    if total > MAX_ARCHIVE or budget > MAX_ARCHIVE:
        raise HTTPException(status_code=413, detail="run result package exceeds the 512 MiB archive limit")
    return list(unique.items())


@app.get("/api/runs/{run_id}/download")
async def run_download(run_id: str):
    try:
        run_dir = storage.run_path(run_id)
        manifest = read_json(run_dir / "manifest.json")
        if manifest.get("status") not in {"succeeded", "failed", "timed_out", "cancelled", "interrupted"}:
            raise HTTPException(status_code=409, detail="run result package is available after the run reaches a terminal state")
        from . import snapshots as snapshot_store
        for item in manifest.get("snapshots", []):
            step_id = item.get("stepId")
            if isinstance(step_id, str):
                snapshot_store.ensure_materialized(run_dir, step_id)
        entries = _archive_entries(run_dir, manifest)
        filename = f"viennaps-run-{run_id}.zip"
        return StreamingResponse(stream_zip_async(entries), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    except HTTPException:
        raise
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


if FRONTEND_DIST.is_dir() and (FRONTEND_DIST / "index.html").is_file():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
else:
    @app.get("/")
    async def frontend_not_built():
        return JSONResponse(status_code=503, content={"detail": "frontend build is not present; run scripts/build-frontend.sh after installing the locked npm dependencies"})

