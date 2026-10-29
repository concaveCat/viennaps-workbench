"""One-run ViennaPS adapter. Run only as a bounded child of the API supervisor."""

from __future__ import annotations

import argparse
import copy
import ctypes
import ctypes.util
import csv
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from importlib.metadata import version
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.rates import has_motion, is_geometry_step, uses_flux, signed_rates, signed_directional_rates
from backend.models import normalized_geometry_v3, parse_project, project_structure_hash
from backend.validation import fin_construction_decision
from backend.gpu_devices import cuda_devices, resolve_gpu_device
from worker.specialized import build_specialized_model
MATERIAL_CATALOG = json.loads((ROOT / "docs" / "materials-catalog.json").read_text(encoding="utf-8"))
MATERIAL_ROWS = MATERIAL_CATALOG["materials"]
MATERIAL_NAMES = {int(row["id"]): row["name"] for row in MATERIAL_ROWS}
MATERIAL_IDS = {row["name"]: int(row["id"]) for row in MATERIAL_ROWS}
MATERIAL_COLORS = {int(row["id"]): row["color"] for row in MATERIAL_ROWS}
MAX_RUN_BYTES = 512 * 1024 * 1024
GPU_MULTIPARTICLE_SPLIT_SWEEPS = 4
GPU_MULTIPARTICLE_SPLIT_STRATEGY = "symmetric-operator-splitting-v1"
GPU_MULTIPARTICLE_FLUX_STRATEGY = "per-species-native-mesh-merge-v1"
GPU_MULTIPARTICLE_NATIVE_STRATEGY = "native-multi-ion-v1"
GPU_MULTIPARTICLE_NATIVE_FLUX_STRATEGY = "native-multi-ion-flux-v1"


def configure_native_materials(ps: Any, custom_rows: list[dict[str, Any]]) -> None:
    """Recreate the persisted 4.6.2 registry prefix in every isolated worker."""
    global MATERIAL_ROWS, MATERIAL_NAMES, MATERIAL_IDS, MATERIAL_COLORS
    rows = list(MATERIAL_CATALOG["materials"])
    builtin_names = {row["name"] for row in rows}
    builtin_ids = {int(row["id"]) for row in rows}
    registry = ps.MaterialRegistry.instance()
    for index, row in enumerate(custom_rows):
        name = row.get("name")
        if (row.get("customId") != index or row.get("id") != 177 + index or not isinstance(name, str)
                or name in builtin_names or row.get("id") in builtin_ids):
            raise RuntimeError("persisted custom material registry is not a stable, collision-free native ID prefix")
        material = registry.registerMaterial(name)
        if not material.isCustom() or int(material.customId()) != index or int(material.legacyId()) != int(row["id"]):
            raise RuntimeError(f"ViennaPS assigned unexpected native identity to custom material {name!r}")
        info = registry.getInfo(material)
        info.color_hex = int(str(row["color"]).lstrip("#"), 16)
        category_name = str(row.get("category", "Generic"))
        try:
            category = ps.MaterialCategory[category_name]
        except KeyError as exc:
            raise RuntimeError(f"custom material {name!r} has an unknown MaterialCategory {category_name!r}") from exc
        info.category = category
        if row.get("densityGcm3") is not None:
            info.density_gcm3 = float(row["densityGcm3"])
        info.conductive = bool(row.get("conductive", False))
        registry.setInfo(material, info)
        readback = registry.getInfo(material)
        if (int(readback.color_hex) != int(str(row["color"]).lstrip("#"), 16)
                or int(readback.category) != int(category)
                or bool(readback.conductive) != bool(row.get("conductive", False))
                or (row.get("densityGcm3") is not None and not math.isclose(float(readback.density_gcm3), float(row["densityGcm3"]), rel_tol=0, abs_tol=1e-12))):
            raise RuntimeError(f"ViennaPS did not retain custom MaterialInfo for {name!r}")
        rows.append({"name": name, "id": int(row["id"]), "color": row["color"], "category": category_name})
    MATERIAL_ROWS = rows
    MATERIAL_NAMES = {int(row["id"]): row["name"] for row in rows}
    MATERIAL_IDS = {row["name"]: int(row["id"]) for row in rows}
    MATERIAL_COLORS = {int(row["id"]): row["color"] for row in rows}


def atomic_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    # Snapshot JSON contains large point/cell arrays. Compact serialization
    # avoids spending time on indentation and reduces per-run artifact size.
    # Keep the encode-then-write form: measured on a 7.4 MB snapshot,
    # json.dump(stream) is ~3.8x slower than json.dumps() + write_text()
    # (293 ms vs 77 ms), because the stream path writes in small chunks.
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def check_artifact_budget(run_dir: Path) -> None:
    total = 0
    for item in run_dir.rglob("*"):
        try:
            # The supervisor atomically replaces manifest.json while this
            # bounded worker writes snapshots. A temporary manifest can
            # disappear between is_file() and stat(); temporary files are not
            # retained artifacts and must not count against the run budget.
            if item.name.endswith(".tmp") or not item.is_file():
                continue
            total += item.stat().st_size
            if total > MAX_RUN_BYTES:
                raise RuntimeError("run artifacts exceeded the 512 MiB application cap")
        except OSError:
            # A concurrent atomic replace may remove a file after rglob has
            # yielded its path. Continue with the remaining committed files.
            continue


def cuda_runtime_readback(expected_device: dict[str, Any]) -> dict[str, Any]:
    library = ctypes.util.find_library("cudart")
    if not library:
        raise RuntimeError("GPU_DISK was selected but the CUDA Runtime library cannot be loaded")
    runtime = ctypes.CDLL(library)

    def call(name: str, *args: Any) -> int:
        function = getattr(runtime, name)
        function.restype = ctypes.c_int
        return int(function(*args))

    count = ctypes.c_int()
    count_status = call("cudaGetDeviceCount", ctypes.byref(count))
    if count_status != 0 or count.value != 1:
        raise RuntimeError(f"selected CUDA UUID must be the only runtime-visible device; count status={count_status}, count={count.value}")
    set_status = call("cudaSetDevice", 0)
    current = ctypes.c_int(-1)
    current_status = call("cudaGetDevice", ctypes.byref(current))
    pci_bus = ctypes.create_string_buffer(32)
    pci_status = call("cudaDeviceGetPCIBusId", pci_bus, len(pci_bus), 0)
    observed_bus = pci_bus.value.decode("ascii", errors="replace") if pci_status == 0 else None
    synchronize_status = call("cudaDeviceSynchronize")

    def normalize_bus(value: object) -> str:
        parts = str(value or "").lower().split(":")
        if len(parts) != 3:
            return ""
        return f"{parts[0][-4:]}:{parts[1]}:{parts[2]}"

    if set_status != 0 or current_status != 0 or pci_status != 0 or synchronize_status != 0:
        raise RuntimeError(f"CUDA Runtime device readback failed: set={set_status}, current={current_status}, pci={pci_status}, synchronize={synchronize_status}")
    if normalize_bus(observed_bus) != normalize_bus(expected_device.get("pciBusId")):
        raise RuntimeError(f"CUDA Runtime selected PCI device {observed_bus}, expected {expected_device.get('pciBusId')} ({expected_device.get('uuid')})")
    return {"runtimeLibrary": library, "visibleDeviceCount": count.value,
            "logicalDevice": current.value, "pciBusId": observed_bus,
            "uuid": expected_device["uuid"], "name": expected_device["name"],
            "setDeviceStatus": set_status, "synchronizeStatus": synchronize_status,
            "readbackStatus": "verified"}


def reset_native_gpu_context(ps: Any, expected_device: dict[str, Any]) -> dict[str, Any]:
    """Release ViennaCore's process-global CUDA/OptiX context before another GPU step."""
    if not hasattr(ps, "gpu") or not hasattr(ps.gpu, "Context"):
        raise RuntimeError("GPU recipe needs ViennaCore's exposed Context lifecycle API")
    context = ps.gpu.Context.getContextFromRegistry(0)
    if context is None:
        return {"status": "already-absent", "logicalDeviceId": 0,
                "gpuUuid": expected_device["uuid"]}
    logical_device_id = int(context.deviceID)
    context.destroy()
    del context
    import gc
    gc.collect()
    remaining = [int(value) for value in ps.gpu.Context.getRegisteredDeviceIDs()]
    if logical_device_id in remaining:
        raise RuntimeError("ViennaCore retained its CUDA/OptiX context after destroy()")
    return {"status": "destroyed", "logicalDeviceId": logical_device_id,
            "gpuUuid": expected_device["uuid"], "registeredDevicesAfter": remaining}


def nvidia_process_readback(expected_uuid: str) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,gpu_uuid,used_memory",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"processId": os.getpid(), "gpuUuid": expected_uuid, "processListed": False,
                "processListStatus": "unavailable", "queryError": f"{type(exc).__name__}: {exc}"}
    if result.returncode != 0:
        return {"processId": os.getpid(), "gpuUuid": expected_uuid, "processListed": False,
                "processListStatus": "unavailable", "queryReturnCode": result.returncode,
                "queryError": result.stderr.strip()[:500]}
    rows = []
    for values in csv.reader(io.StringIO(result.stdout), skipinitialspace=True):
        if len(values) == 3:
            pid_text, uuid, memory = [value.strip() for value in values]
            if pid_text.isdigit():
                rows.append({"pid": int(pid_text), "gpuUuid": uuid,
                             "usedMemoryMiB": int(memory) if memory.isdigit() else None})
    own = [row for row in rows if row["pid"] == os.getpid()]
    matching = [row for row in own if row["gpuUuid"] == expected_uuid]
    if own and not matching:
        raise RuntimeError(f"nvidia-smi confirmed worker PID {os.getpid()} on an unexpected GPU; selected={expected_uuid}, observed={own}")
    if len(own) != len(matching):
        raise RuntimeError(f"nvidia-smi confirmed worker PID {os.getpid()} on more than the selected GPU; selected={expected_uuid}, observed={own}")
    if not matching:
        return {"processId": os.getpid(), "gpuUuid": None, "expectedGpuUuid": expected_uuid,
                "processListed": False,
                "processListStatus": "not-observed", "observedProcessCount": len(rows),
                "visibilityNote": "nvidia-smi did not enumerate this CUDA worker; CUDA Runtime PCI/UUID readback and the pinned ViennaPS GPU engine are recorded separately"}
    return {"processId": os.getpid(), "gpuUuid": expected_uuid,
            "usedMemoryMiB": matching[0]["usedMemoryMiB"], "processListed": True,
            "processListStatus": "verified", "observedProcessCount": len(rows)}


def nvidia_gpu_sample() -> dict[str, Any]:
    """One-shot GPU utilisation/clocks sample; never fails a run.

    The process list from `nvidia-smi --query-compute-apps` does not always
    enumerate the CUDA worker (WSL2), so utilisation is sampled separately to
    answer "was the GPU actually busy" without guessing.
    """
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,clocks.sm,pstate",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=6, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}
    devices = []
    for values in csv.reader(io.StringIO(result.stdout), skipinitialspace=True):
        if len(values) >= 4:
            utilization, memory, clock, pstate = (value.strip() for value in values[:4])
            devices.append({"utilizationPercent": int(utilization) if utilization.isdigit() else None,
                            "memoryUsedMiB": int(memory) if memory.isdigit() else None,
                            "clockSmMHz": int(clock) if clock.isdigit() else None,
                            "pstate": pstate})
    if result.returncode != 0 or not devices:
        return {"status": "unavailable", "returnCode": result.returncode,
                "error": (result.stderr or "").strip()[:200]}
    return {"status": "sampled", "devices": devices}


def event(run_dir: Path, data: dict[str, Any]) -> None:
    record = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **data}
    with (run_dir / "events.ndjson").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        stream.flush()


# Phase timings: the worker otherwise records only one aggregate
# process.apply() duration, which hides setup, snapshot and teardown cost.
# Recorded by default so every run is auditable; set
# VIENNAPS_WORKBENCH_PROFILE=0 to disable (adds ~10 perf_counter calls).
PROFILE_ENABLED = os.environ.get("VIENNAPS_WORKBENCH_PROFILE") != "0"
# Reuse the CUDA/OptiX context across consecutive GPU steps unless explicitly
# disabled. An in-process CUDA Runtime call (cuda_runtime_readback) between two
# GPU steps is what breaks the second OptiX acceleration-structure build
# (optixAccelBuild 7001 Invalid value); steps that are followed by another GPU
# step therefore skip that call and use nvidia-smi evidence instead.
GPU_CONTEXT_REUSE = os.environ.get("VIENNAPS_WORKBENCH_GPU_CONTEXT_REUSE") != "0"
# Step snapshots are materialised on first read (see backend/snapshots.py).
# The initial snapshot stays eager: the viewer needs it immediately.
LAZY_SNAPSHOTS = os.environ.get("VIENNAPS_WORKBENCH_LAZY_SNAPSHOTS") != "0"
PROFILE_PHASES: list[dict[str, Any]] = []


class phase:
    """Time a worker phase; records nothing unless profiling is enabled."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.started = time.perf_counter()

    def stop(self) -> None:
        if PROFILE_ENABLED:
            PROFILE_PHASES.append({"phase": self.name,
                                   "seconds": round(time.perf_counter() - self.started, 6)})

    def __enter__(self) -> "phase":
        self.started = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> bool:
        self.stop()
        return False


def _data_scalars(data: Any) -> dict[str, list[float]]:
    result = {}
    for index in range(data.getScalarDataSize()):
        label = str(data.getScalarDataLabel(index))
        result[label] = [float(value) for value in data.getScalarData(index)]
    return result


def _mesh_data_arrays(data: Any) -> dict[str, dict[str, list[Any]]]:
    """Copy every native scalar/vector array without changing its association."""
    scalars = {}
    vectors = {}
    for index in range(data.getScalarDataSize()):
        label = str(data.getScalarDataLabel(index))
        scalars[label] = [float(value) for value in data.getScalarData(index)]
    for index in range(data.getVectorDataSize()):
        label = str(data.getVectorDataLabel(index))
        vectors[label] = [[float(value) for value in row] for row in data.getVectorData(index)]
    return {"scalars": scalars, "vectors": vectors}


def native_mesh_report(mesh: Any) -> dict[str, Any]:
    points = [[float(axis) for axis in point] for point in mesh.getNodes()]
    cells = {
        "lines": [[int(value) for value in row] for row in mesh.getLines()],
        "triangles": [[int(value) for value in row] for row in mesh.getTriangles()],
        "tetras": [[int(value) for value in row] for row in mesh.getTetras()],
        "hexas": [[int(value) for value in row] for row in mesh.getHexas()],
        "vertices": [[int(value) for value in row] for row in mesh.getVerticies()],
    }
    topology = {name: value for name, value in cells.items() if value}
    point_data = _mesh_data_arrays(mesh.getPointData())
    cell_data = _mesh_data_arrays(mesh.getCellData())
    fields = []
    for association, data in (("pointData", point_data), ("cellData", cell_data)):
        for kind in ("scalars", "vectors"):
            for name, values in data[kind].items():
                fields.append({"name": name, "association": association, "components": 1 if kind == "scalars" else 3,
                               "tuples": len(values), "nativeUnit": None,
                               "unitStatus": "not-provided-by-native-mesh-interface"})
    mesh_identity_payload = {"points": points, "topology": topology}
    mesh_identity = hashlib.sha256(json.dumps(mesh_identity_payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return {"points": points, **topology, "pointData": point_data, "cellData": cell_data,
            "meshIdentity": mesh_identity, "fields": fields}


def configure_advection(ps: Any, project: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    params = ps.AdvectionParameters()
    params.timeStepRatio = float(project["solver"]["timeStepRatio"])
    actual: dict[str, Any] = {"timeStepRatio": float(params.timeStepRatio)}
    if project["schemaVersion"] >= 7:
        requested = project["solver"]["advection"]
        params.spatialScheme = getattr(ps.ls.SpatialSchemeEnum, requested["spatialScheme"])
        params.temporalScheme = getattr(ps.ls.TemporalSchemeEnum, requested["temporalScheme"])
        for name in ("adaptiveTimeStepping", "adaptiveTimeStepSubdivisions", "calculateIntermediateVelocities",
                     "checkDissipation", "dissipationAlpha", "ignoreVoids", "velocityOutput"):
            setattr(params, name, requested[name])
        actual.update({name: getattr(params, name) for name in (
            "adaptiveTimeStepping", "adaptiveTimeStepSubdivisions",
            "calculateIntermediateVelocities", "checkDissipation", "dissipationAlpha", "ignoreVoids", "velocityOutput")})
        actual["spatialScheme"] = params.spatialScheme.name
        actual["spatialSchemeValue"] = int(params.spatialScheme)
        actual["temporalScheme"] = params.temporalScheme.name
        actual["temporalSchemeValue"] = int(params.temporalScheme)
        actual["nativeAdvectionMetadata"] = params.toMetaData()
    return params, actual


def configure_rays(ps: Any, project: dict[str, Any], seed: int) -> tuple[Any, dict[str, Any]]:
    params = ps.RayTracingParameters()
    params.raysPerPoint = int(project["solver"]["raysPerPoint"])
    params.rngSeed = int(seed)
    params.useRandomSeeds = False
    actual: dict[str, Any] = {"raysPerPoint": int(params.raysPerPoint), "rngSeed": int(params.rngSeed),
                              "useRandomSeeds": bool(params.useRandomSeeds)}
    if project["schemaVersion"] >= 7:
        requested = project["solver"]["rayTracing"]
        for name in ("diskRadius", "ignoreFluxBoundaries", "maxBoundaryHits", "maxReflections",
                     "minNodeDistanceFactor", "smoothingNeighbors"):
            setattr(params, name, requested[name])
        params.normalizationType = getattr(ps.NormalizationType, requested["normalizationType"])
        actual.update({name: getattr(params, name) for name in (
            "diskRadius", "ignoreFluxBoundaries", "maxBoundaryHits", "maxReflections",
            "minNodeDistanceFactor", "smoothingNeighbors")})
        actual["normalizationType"] = params.normalizationType.name
        actual["normalizationTypeValue"] = int(params.normalizationType)
        actual["nativeRayTracingMetadata"] = params.toMetaData()
    return params, actual


def mesh_report(mesh: Any) -> dict[str, Any]:
    nodes = [[float(axis) for axis in node] for node in mesh.getNodes()]
    lines = [[int(index) for index in line] for line in mesh.getLines()]
    if any(len(point) != 3 or not all(math.isfinite(value) for value in point) for point in nodes):
        raise RuntimeError("ViennaPS returned a non-finite or non-3D point in the 2D mesh")
    if any(len(line) != 2 or line[0] == line[1] or any(index < 0 or index >= len(nodes) for index in line) for line in lines):
        raise RuntimeError("ViennaPS returned invalid line connectivity")
    cell_data = _data_scalars(mesh.getCellData())
    material_ids = cell_data.get("MaterialIds")
    if material_ids is None or len(material_ids) != len(lines):
        raise RuntimeError("ViennaPS surface mesh has no aligned MaterialIds cell array")
    if any(int(value) not in MATERIAL_NAMES for value in material_ids):
        raise RuntimeError("ViennaPS surface mesh contains an unknown catalog material ID")
    point_arrays = _mesh_data_arrays(mesh.getPointData())
    cell_arrays = _mesh_data_arrays(mesh.getCellData())
    return {
        "points": nodes,
        "lines": lines,
        "cellData": cell_data,
        "pointData": point_arrays["scalars"],
        "cellVectorData": cell_arrays["vectors"],
        "pointVectorData": point_arrays["vectors"],
    }


def trench_has_two_walls(mesh: dict[str, Any], delta: float, substrate_id: int = 10) -> bool:
    nodes = mesh["points"]
    ids = mesh["cellData"]["MaterialIds"]
    sides = set()
    for (first, second), material_id in zip(mesh["lines"], ids):
        if int(material_id) != substrate_id:
            continue
        a, b = nodes[first], nodes[second]
        if abs(a[1] - b[1]) <= 0.5 * delta:
            continue
        middle_x = 0.5 * (a[0] + b[0])
        if middle_x < -1e-9:
            sides.add("negative")
        elif middle_x > 1e-9:
            sides.add("positive")
    return sides == {"negative", "positive"}


def measure_initial_geometry(mesh: dict[str, Any], geometry: dict[str, Any], delta: float) -> dict[str, float]:
    points = mesh["points"]
    if geometry["kind"] == "plane":
        return {"heightMedian": float(sorted(point[1] for point in points)[len(points) // 2])}
    ids = mesh["cellData"]["MaterialIds"]
    top_segments = []
    for (first, second), _material_id in zip(mesh["lines"], ids):
        a, b = points[first], points[second]
        if abs(a[1]) <= delta * 0.05 and abs(b[1]) <= delta * 0.05:
            top_segments.append((a[0], b[0]))
    left_points = [x for pair in top_segments for x in pair if x < -1e-9]
    right_points = [x for pair in top_segments for x in pair if x > 1e-9]
    # The initial trench has only the substrate at negative h; mask points are
    # at or above the zero datum. The returned raster contour is authoritative.
    if not left_points or not right_points:
        raise RuntimeError("cannot measure the initial trench opening from its top interface")
    measured_width = min(right_points) - max(left_points)
    measured_depth = -min(point[1] for point in points)
    requested_width = float(geometry["width"])
    requested_depth = float(geometry["depth"])
    tolerance = 2 * delta + 1e-9
    if abs(measured_width - requested_width) > tolerance:
        raise RuntimeError(f"rasterized trench opening {measured_width:.6g} um differs from requested width by more than 2 grid intervals")
    if abs(measured_depth - requested_depth) > tolerance:
        raise RuntimeError(f"rasterized trench depth {measured_depth:.6g} um differs from requested depth by more than 2 grid intervals")
    return {"openingWidth": measured_width, "depth": measured_depth}


def material_map(domain: Any) -> list[int]:
    mapping = domain.getMaterialMap()
    return [int(mapping.getMaterialIdAtIdx(index)) for index in range(mapping.size())]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def output_artifact(path: Path, run_dir: Path, run_id: str, step_id: str, kind: str, fmt: str,
                    **details: Any) -> dict[str, Any]:
    relative = path.resolve().relative_to(run_dir.resolve()).as_posix()
    return {"kind": kind, "fileName": relative,
            "url": f"/api/runs/{run_id}/artifacts/{step_id}?fileName={relative}",
            "format": fmt, "sizeBytes": path.stat().st_size, "sha256": file_sha256(path), **details}


def _levelset_index(path: Path) -> int:
    match = re.search(r"_layer(\d+)\.(?:lvst|vtp)$", path.name)
    if not match:
        raise RuntimeError(f"native level-set output filename is malformed: {path.name}")
    return int(match.group(1))


def save_native_state(ps: Any, domain: Any, run_dir: Path, *, project: dict[str, Any], run_id: str,
                      step_id: str, viennaps_version: str, viennals_version: str) -> dict[str, Any]:
    path = run_dir / f"state-{step_id}.vpsd"
    ps.Writer(domain, str(path)).apply()
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError("ViennaPS Writer did not create a nonempty native Domain state")
    mapping = material_map(domain)
    parsed = parse_project(project)
    geometry = normalized_geometry_v3(parsed)
    ref = {
        "formatVersion": 1,
        "stateId": file_sha256(path),
        "structureHash": project_structure_hash(project["dimension"], parsed.units, parsed.domain, geometry),
        "dimension": int(project["dimension"]),
        "gridDelta": float(domain.getGridDelta()),
        "xExtent": float(project["domain"]["xExtent"]),
        **({"yExtent": float(project["domain"]["yExtent"])} if project["dimension"] == 3 else {}),
        "boundary": project["domain"].get("xBoundary", project["domain"].get("boundaryXY")),
        "boundaryConditions": ["reflective" if str(value) == "0" else "periodic" if str(value) == "2" else "infinite" if str(value) == "1" else f"unknown:{value}" for value in domain.getBoundaryConditions()],
        "materialIdsByLevelSetIndex": mapping,
        "levelSetCount": int(domain.getNumberOfLevelSets()),
        "viennapsVersion": viennaps_version,
        "viennalsVersion": viennals_version,
        "sourceRunId": run_id,
        "sourceStepId": step_id,
    }
    check_artifact_budget(run_dir)
    return {"fileName": path.name, **ref}


def material_handle(ps: Any, name: str):
    registry = ps.MaterialRegistry.instance()
    material = registry.findMaterial(name)
    if material is None:
        raise RuntimeError(f"material {name!r} is absent from the native MaterialRegistry")
    return registry.getMaterial(name)


def build_multi_particle_model(ps: Any, step: dict[str, Any]) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    """Build the installed 4.6.2 model and retain a bounded callback audit."""
    model = ps.MultiParticleProcess()
    labels: list[str] = []
    native_species: list[dict[str, Any]] = []
    for species in step["species"]:
        label = str(species["label"])
        labels.append(label)
        if species["kind"] == "ion":
            parameters = {key: species[key] for key in (
                "sourcePower", "thetaRMin", "thetaRMax", "minAngle", "B_sp", "meanEnergy",
                "sigmaEnergy", "thresholdEnergy", "inflectAngle", "n")}
            model.addIonParticle(
                sourcePower=float(parameters["sourcePower"]),
                thetaRMin=float(parameters["thetaRMin"]), thetaRMax=float(parameters["thetaRMax"]),
                minAngle=float(parameters["minAngle"]), B_sp=float(parameters["B_sp"]),
                meanEnergy=float(parameters["meanEnergy"]), sigmaEnergy=float(parameters["sigmaEnergy"]),
                thresholdEnergy=float(parameters["thresholdEnergy"]), inflectAngle=float(parameters["inflectAngle"]),
                n=int(parameters["n"]), label=label,
            )
            native_species.append({"kind": "ion", "label": label, **parameters})
        elif species["kind"] == "neutral":
            sticking_by_material = species.get("stickingProbabilityByMaterial", {})
            native_sticking = {material_handle(ps, name): float(probability)
                               for name, probability in sticking_by_material.items()}
            model.addNeutralParticle(
                native_sticking,
                defaultStickingProbability=float(species["defaultStickingProbability"]), label=label,
            )
            native_species.append({
                "kind": "neutral", "label": label,
                "defaultStickingProbability": float(species["defaultStickingProbability"]),
                "stickingProbabilityByMaterial": {name: float(value) for name, value in sticking_by_material.items()},
                "stickingMaterialIds": {name: MATERIAL_IDS[name] for name in sticking_by_material},
            })
        else:
            raise RuntimeError(f"unsupported multi-particle species kind {species['kind']!r}")

    relation = step["rateCoefficients"]
    defaults = {label: float(relation["defaultBySpecies"][label]) for label in labels}
    overrides = {material: {label: float(value) for label, value in row.items()}
                 for material, row in relation.get("byMaterial", {}).items()}
    native_material_names = {
        str(material_handle(ps, name)): name for name in MATERIAL_IDS
    }
    callback_state: dict[str, Any] = {
        "count": 0, "materials": {}, "samples": [],
        "fluxSums": [0.0] * len(labels), "fluxMaxima": [0.0] * len(labels),
    }
    operation_sign = -1.0 if step["operation"] == "etch" else 1.0

    def rate_function(fluxes: Any, material: Any) -> float:
        material_repr = str(material)
        material_name = native_material_names.get(material_repr)
        if material_name is None:
            raise RuntimeError(f"ViennaPS rate callback returned an unregistered material {material_repr}")
        values = [float(value) for value in fluxes]
        if len(values) != len(labels):
            raise RuntimeError(
                f"ViennaPS rate callback flux vector length {len(values)} does not match "
                f"the {len(labels)} configured species"
            )
        if any(not math.isfinite(value) for value in values):
            raise RuntimeError("ViennaPS rate callback returned a non-finite species flux")
        for index, value in enumerate(values):
            callback_state["fluxSums"][index] += value
            callback_state["fluxMaxima"][index] = max(callback_state["fluxMaxima"][index], value)
        coefficients = overrides.get(material_name, {})
        magnitude = sum(values[index] * coefficients.get(label, defaults[label])
                        for index, label in enumerate(labels))
        signed = operation_sign * magnitude
        if not math.isfinite(signed):
            raise RuntimeError("multi-particle rate relation returned a non-finite velocity")
        callback_state["count"] += 1
        callback_state["materials"][material_name] = callback_state["materials"].get(material_name, 0) + 1
        if len(callback_state["samples"]) < 256:
            sample = {"material": material_name, "fluxes": values,
                      "coefficients": {label: coefficients.get(label, defaults[label]) for label in labels},
                      "returnedVelocity": signed}
            if os.environ.get("VIENNAPS_DEBUG_NATIVE_CALLBACKS") == "1":
                sample["nativeMaterial"] = material_repr
            callback_state["samples"].append(sample)
        return signed

    model.setRateFunction(rate_function)
    actual = {
        "nativeProcess": "viennaps.MultiParticleProcess",
        "speciesOrder": labels,
        "species": native_species,
        "rateFunction": {
            "kind": "weighted-sum-table", "formula": "operationSign * sum(flux[i] * coefficient[material][species[i]])",
            "operationSign": operation_sign, "defaultBySpecies": defaults, "byMaterial": overrides,
            "nativeFluxUnits": "not-declared-by-installed-binding",
            "returnedVelocityUnit": "um/s workbench convention; coefficients perform the conversion",
        },
        "nativeSpeciesFieldNames": [f"{label}{index}" for index, label in enumerate(labels)],
    }
    return model, actual, callback_state


def multi_particle_species_step(step: dict[str, Any], species: dict[str, Any]) -> dict[str, Any]:
    """Project one species and its matching material-rate columns for a native sub-process."""
    label = str(species["label"])
    relation = step["rateCoefficients"]
    return {**step, "species": [species], "rateCoefficients": {
        "defaultBySpecies": {label: float(relation["defaultBySpecies"][label])},
        "byMaterial": {
            material: {label: float(values[label])}
            for material, values in relation.get("byMaterial", {}).items() if label in values
        },
    }}


def multi_particle_step_summary(step: dict[str, Any]) -> dict[str, Any]:
    labels = [str(row["label"]) for row in step["species"]]
    relation = step["rateCoefficients"]
    return {
        "nativeProcess": "viennaps.MultiParticleProcess",
        "speciesOrder": labels,
        "species": step["species"],
        "rateFunction": {
            "kind": "weighted-sum-table",
            "formula": "operationSign * sum(flux[i] * coefficient[material][species[i]])",
            "operationSign": -1.0 if step["operation"] == "etch" else 1.0,
            "defaultBySpecies": relation["defaultBySpecies"],
            "byMaterial": relation.get("byMaterial", {}),
            "nativeFluxUnits": "not-declared-by-installed-binding",
            "returnedVelocityUnit": "um/s workbench convention; coefficients perform the conversion",
        },
    }


def _vtk_appended_payload(root: ET.Element) -> str:
    appended = root.find("AppendedData")
    if appended is None:
        return ""
    text = "".join((appended.text or "").split())
    if not text.startswith("_"):
        raise RuntimeError("native VTP appended-data block has no VTK sentinel")
    return text[1:]


def _vtk_appended_chunks(root: ET.Element) -> dict[ET.Element, str]:
    arrays = [array for array in root.findall(".//DataArray") if array.attrib.get("format") == "appended"]
    payload = _vtk_appended_payload(root)
    offsets: dict[ET.Element, int] = {}
    for array in arrays:
        try:
            offset = int(array.attrib["offset"])
        except (KeyError, ValueError) as exc:
            raise RuntimeError("native VTP appended DataArray has an invalid offset") from exc
        if offset < 0 or offset >= len(payload):
            raise RuntimeError("native VTP appended DataArray offset is outside its payload")
        offsets[array] = offset
    sorted_offsets = sorted(set(offsets.values()))
    chunks: dict[ET.Element, str] = {}
    for array, start in offsets.items():
        end = next((offset for offset in sorted_offsets if offset > start), len(payload))
        if end <= start:
            raise RuntimeError("native VTP appended DataArray has an empty payload")
        chunks[array] = payload[start:end]
    if arrays and not payload:
        raise RuntimeError("native VTP declares appended DataArrays without an appended payload")
    return chunks


def _vtk_data_array_signature(array: ET.Element, chunks: dict[ET.Element, str]) -> tuple[Any, ...]:
    attributes = tuple(sorted((key, value) for key, value in array.attrib.items() if key != "offset"))
    value: Any = chunks.get(array) if array.attrib.get("format") == "appended" else (array.text or "").strip()
    return attributes, value


def merge_native_flux_vtp(species_paths: list[Path], output_path: Path) -> None:
    """Merge real per-species VTP fields while preserving their native association.

    The method requires every native calculation to return an identical mesh.
    It retains the first VTK geometry and adds the other calls' named point/cell
    arrays. It never resamples fields or associates them with another mesh.
    """
    if not species_paths:
        raise RuntimeError("per-species ViennaPS flux merge received no native VTP files")
    roots = [ET.parse(path).getroot() for path in species_paths]
    root_chunks = [_vtk_appended_chunks(root) for root in roots]
    first = roots[0]
    appended_payload = _vtk_appended_payload(first)
    if first.attrib.get("type") != "PolyData":
        raise RuntimeError("per-species ViennaPS flux output is not VTK PolyData")
    first_piece = first.find(".//Piece")
    if first_piece is None:
        raise RuntimeError("per-species ViennaPS flux output has no PolyData Piece")

    def geometry_signature(root: ET.Element, chunks: dict[ET.Element, str]) -> tuple[Any, ...]:
        piece = root.find(".//Piece")
        if root.attrib.get("type") != "PolyData" or piece is None:
            raise RuntimeError("per-species ViennaPS flux output is not valid PolyData")
        sections = []
        for name in ("Points", "Verts", "Lines", "Strips", "Polys"):
            node = piece.find(name)
            if node is None:
                sections.append((name, None))
            else:
                arrays = tuple((array.tag, _vtk_data_array_signature(array, chunks))
                               for array in node.findall("DataArray"))
                sections.append((name, (tuple(sorted(node.attrib.items())), arrays)))
        return (root.attrib.get("type"), tuple(sorted(piece.attrib.items())), tuple(sections))

    expected_geometry = geometry_signature(first, root_chunks[0])
    for root, chunks in zip(roots[1:], root_chunks[1:]):
        if geometry_signature(root, chunks) != expected_geometry:
            raise RuntimeError("per-species ViennaPS calculateFlux calls returned different mesh geometry or topology")

    for association in ("FieldData", "PointData", "CellData"):
        target = first_piece.find(association)
        if target is None:
            target = ET.SubElement(first_piece, association)
        names: dict[str, ET.Element] = {}
        for array in target.findall("DataArray"):
            name = array.attrib.get("Name")
            if not name or name in names:
                raise RuntimeError(f"native {association} contains a missing or duplicate DataArray name")
            names[name] = array
        for root, chunks in zip(roots[1:], root_chunks[1:]):
            piece = root.find(".//Piece")
            source = piece.find(association) if piece is not None else None
            if source is None:
                continue
            for array in source.findall("DataArray"):
                name = array.attrib.get("Name")
                if not name:
                    raise RuntimeError(f"native {association} contains a DataArray without a name")
                prior = names.get(name)
                if prior is not None:
                    if _vtk_data_array_signature(prior, root_chunks[0]) != _vtk_data_array_signature(array, chunks):
                        raise RuntimeError(f"per-species native flux arrays collide with different values: {association}.{name}")
                    continue
                copied = copy.deepcopy(array)
                if copied.attrib.get("format") == "appended":
                    segment = chunks.get(array)
                    if not segment:
                        raise RuntimeError(f"native {association}.{name} has no appended VTK data block")
                    copied.attrib["offset"] = str(len(appended_payload))
                    appended_payload += segment
                names[name] = copied
                target.append(copied)
    appended = first.find("AppendedData")
    if appended is not None:
        appended.text = "\n   _" + appended_payload + "\n  "
    elif appended_payload:
        raise RuntimeError("native VTP has appended DataArrays but no AppendedData element")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(first).write(output_path, encoding="utf-8", xml_declaration=True)


def merge_native_flux_reports(species_reports: list[dict[str, Any]]) -> dict[str, Any]:
    if not species_reports:
        raise RuntimeError("per-species ViennaPS flux merge received no native mesh reports")
    base = species_reports[0]
    for report in species_reports[1:]:
        if report["meshIdentity"] != base["meshIdentity"]:
            raise RuntimeError("per-species ViennaPS calculateFlux calls returned different mesh identity")
    merged = {key: value for key, value in base.items()}
    for association in ("pointData", "cellData"):
        merged[association] = {kind: {} for kind in ("scalars", "vectors")}
        for report in species_reports:
            for kind in ("scalars", "vectors"):
                for name, values in report[association][kind].items():
                    prior = merged[association][kind].get(name)
                    if prior is not None and prior != values:
                        raise RuntimeError(f"per-species native flux arrays collide with different values: {association}.{name}")
                    merged[association][kind][name] = values
    merged["fields"] = []
    for association, arrays in (("pointData", merged["pointData"]), ("cellData", merged["cellData"])):
        for kind in ("scalars", "vectors"):
            for name, values in arrays[kind].items():
                merged["fields"].append({"name": name, "association": association,
                                         "components": 1 if kind == "scalars" else 3,
                                         "tuples": len(values), "nativeUnit": None,
                                         "unitStatus": "not-provided-by-native-mesh-interface"})
    return merged


def calculate_multi_particle_flux_by_species(ps: Any, domain: Any, project: dict[str, Any], step: dict[str, Any],
                                             engine: str, seed: int, run_dir: Path) -> dict[str, Any]:
    """Run native calculateFlux once per particle, then merge only identical meshes."""
    if engine != "GPU_DISK":
        raise RuntimeError("per-species MultiParticle flux strategy is currently verified only for GPU_DISK")
    species_dir = run_dir / "flux-species"
    species_dir.mkdir(parents=True, exist_ok=False)
    reports: list[dict[str, Any]] = []
    species_records: list[dict[str, Any]] = []
    for index, species in enumerate(step["species"]):
        label = str(species["label"])
        species_step = multi_particle_species_step(step, species)
        model, multi_actual, callback_state = build_multi_particle_model(ps, species_step)
        process = ps.Process(domain, model, float(step.get("duration") or 0.0))
        process.setFluxEngineType(ps.FluxEngineType.GPU_DISK)
        rays, ray_actual = configure_rays(ps, project, int(seed))
        process.setParameters(rays)
        native_path = species_dir / f"species-{index:02d}.vtp"
        started = time.perf_counter()
        mesh = process.calculateFlux()
        elapsed = time.perf_counter() - started
        report = native_mesh_report(mesh)
        if not report["points"]:
            raise RuntimeError(f"ViennaPS calculateFlux returned an empty mesh for species {label!r}")
        for association in ("pointData", "cellData"):
            for kind in ("scalars", "vectors"):
                for field_name, values in report[association][kind].items():
                    if any(not math.isfinite(value) for row in values for value in (row if isinstance(row, list) else [row])):
                        raise RuntimeError(f"ViennaPS flux field {field_name!r} for {label!r} contains non-finite values")
        writer = ps.ls.VTKWriter(mesh, ps.ls.FileFormatEnum.VTP, str(native_path))
        writer.apply()
        if not native_path.is_file() or native_path.stat().st_size <= 0:
            raise RuntimeError(f"ViennaLS VTKWriter did not create native flux mesh for {label!r}")
        reports.append(report)
        species_records.append({"label": label, "kind": species["kind"], "actualSeed": int(seed),
                                "actualEngine": engine, "rayTracing": ray_actual,
                                "elapsedSeconds": elapsed, "meshIdentity": report["meshIdentity"],
                                "nativeVtk": native_path.relative_to(run_dir).as_posix(),
                                "fields": report["fields"],
                                "nativeModel": multi_actual,
                                "rateCallbackCount": int(callback_state["count"])})
    merged_report = merge_native_flux_reports(reports)
    return {"report": merged_report, "species": species_records,
            "speciesPaths": [species_dir / f"species-{index:02d}.vtp" for index in range(len(species_records))],
            "execution": {"strategy": GPU_MULTIPARTICLE_FLUX_STRATEGY,
                          "speciesOrder": [row["label"] for row in species_records],
                          "speciesCount": len(species_records),
                          "allNativeMeshesIdentical": True}}


def apply_gpu_multi_particle_split(ps: Any, domain: Any, project: dict[str, Any], step: dict[str, Any],
                                   *, seed: int, run_dir: Path, intermediate_dir: Path,
                                   selected_gpu_device: dict[str, Any]) -> dict[str, Any]:
    """Apply a measured symmetric sequence of single-species GPU_DISK processes.

    ViennaPS 4.6.2's GPU binding drops ion species after the first. Each native
    sub-process here therefore receives exactly one species and its own rate
    column. This is a deterministic numerical approximation to the combined
    MultiParticleProcess and is gated by a versioned, exact-signature evidence
    row in backend.validation.
    """
    species = list(step["species"])
    if len(species) < 2 or sum(row["kind"] == "ion" for row in species) < 2:
        raise RuntimeError("GPU MultiParticle splitting requires at least two ion species")
    count = len(species)
    order = ([(index, 0.5) for index in range(count - 1)]
             + [(count - 1, 1.0)]
             + [(index, 0.5) for index in reversed(range(count - 1))])
    duration = float(step["duration"])
    base_seed = int(seed)
    material_callback_counts: dict[str, int] = {}
    callback_samples: list[dict[str, Any]] = []
    callbacks_by_species = {str(row["label"]): 0 for row in species}
    flux_sums_by_species = {str(row["label"]): 0.0 for row in species}
    flux_maxima_by_species = {str(row["label"]): 0.0 for row in species}
    stage_rows: list[dict[str, Any]] = []
    gpu_stage_rows: list[dict[str, Any]] = []
    total_apply_seconds = 0.0
    previous_cwd = Path.cwd()
    try:
        for sweep in range(GPU_MULTIPARTICLE_SPLIT_SWEEPS):
            for species_index, fraction in order:
                particle = species[species_index]
                label = str(particle["label"])
                stage_index = len(stage_rows)
                stage_seed = (base_seed + stage_index) & 0xFFFFFFFF
                stage_duration = duration * fraction / GPU_MULTIPARTICLE_SPLIT_SWEEPS
                native_step = multi_particle_species_step(step, particle)
                model, model_actual, callback_state = build_multi_particle_model(ps, native_step)
                process = ps.Process(domain, model, stage_duration)
                process.setFluxEngineType(ps.FluxEngineType.GPU_DISK)
                advection, advection_actual = configure_advection(ps, project)
                process.setParameters(advection)
                rays, ray_actual = configure_rays(ps, project, stage_seed)
                process.setParameters(rays)
                stage_dir = intermediate_dir / f"_gpu-split-stage-{stage_index:04d}"
                stage_dir.mkdir(parents=True, exist_ok=False)
                try:
                    os.chdir(stage_dir)
                    process.setIntermediateOutputPath(str(stage_dir))
                    started = time.perf_counter()
                    process.apply()
                    elapsed = time.perf_counter() - started
                finally:
                    os.chdir(previous_cwd)
                if callback_state["count"] <= 0:
                    raise RuntimeError(f"ViennaPS did not invoke the {label!r} split rate relation")
                expected_sign = -1 if step["operation"] == "etch" else 1
                for sample in callback_state["samples"]:
                    value = float(sample["returnedVelocity"])
                    if (expected_sign < 0 and value > 0) or (expected_sign > 0 and value < 0):
                        raise RuntimeError(f"{step['operation']} split callback returned the wrong native velocity sign")
                callbacks_by_species[label] += int(callback_state["count"])
                if len(callback_state["fluxSums"]) != 1 or len(callback_state["fluxMaxima"]) != 1:
                    raise RuntimeError("GPU MultiParticle split callback did not return one flux channel")
                flux_sums_by_species[label] += float(callback_state["fluxSums"][0])
                flux_maxima_by_species[label] = max(
                    flux_maxima_by_species[label], float(callback_state["fluxMaxima"][0]))
                for material_name, number in callback_state["materials"].items():
                    material_callback_counts[material_name] = material_callback_counts.get(material_name, 0) + int(number)
                callback_samples.extend({**row, "splitSpecies": label, "splitStage": stage_index}
                                        for row in callback_state["samples"][:max(0, 256 - len(callback_samples))])
                flattened_outputs = []
                for output_path in sorted(stage_dir.iterdir()):
                    if not output_path.is_file():
                        continue
                    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", output_path.name)
                    flattened = intermediate_dir / f"split-{stage_index:04d}-{label}-{safe_name}"
                    shutil.copyfile(output_path, flattened)
                    flattened_outputs.append(flattened.name)
                shutil.rmtree(stage_dir)
                total_apply_seconds += elapsed
                gpu_stage_rows.append({"stage": stage_index, "species": label,
                                       "engine": "GPU_DISK",
                                       "selectedDeviceUuid": selected_gpu_device["uuid"]})
                stage_rows.append({
                    "stage": stage_index, "sweep": sweep, "speciesIndex": species_index,
                    "species": label, "kind": particle["kind"], "durationFraction": fraction,
                    "durationSeconds": stage_duration, "seed": stage_seed, "engine": "GPU_DISK",
                    "gpuDeviceUuid": selected_gpu_device["uuid"], "processApplySeconds": elapsed,
                    "callbackCount": int(callback_state["count"]),
                    "callbackMaterials": callback_state["materials"],
                    "nativeModel": model_actual, "advection": advection_actual, "rays": ray_actual,
                    "intermediateFiles": flattened_outputs,
                })
        accumulated = {label: sum(row["durationSeconds"] for row in stage_rows if row["species"] == label)
                       for label in callbacks_by_species}
        if any(not math.isclose(value, duration, rel_tol=0.0, abs_tol=max(1e-12, duration * 1e-10))
               for value in accumulated.values()):
            raise RuntimeError("GPU MultiParticle split did not preserve the requested integrated duration per species")
    finally:
        os.chdir(previous_cwd)
    return {
        "elapsedSeconds": total_apply_seconds,
        "callbackState": {"count": sum(callbacks_by_species.values()),
                          "materials": material_callback_counts, "samples": callback_samples,
                          "fluxSums": [flux_sums_by_species[str(row["label"])] for row in species],
                          "fluxMaxima": [flux_maxima_by_species[str(row["label"])] for row in species],
                          "callbackCountBySpecies": callbacks_by_species},
        "gpuStageSelection": gpu_stage_rows,
        "execution": {"strategy": GPU_MULTIPARTICLE_SPLIT_STRATEGY,
                      "sweeps": GPU_MULTIPARTICLE_SPLIT_SWEEPS,
                      "composition": "symmetric Strang sequence: first n-1 species half-step, last species full-step, reverse first n-1 half-step",
                      "durationPolicy": "each species receives the requested step duration per sweep; total integrated species duration equals the requested duration",
                      "speciesOrder": [str(row["label"]) for row in species],
                      "nativeProcessCount": len(stage_rows),
                      "stageSeeds": [row["seed"] for row in stage_rows],
                      "stageDeviceUuids": sorted({row["gpuDeviceUuid"] for row in stage_rows}),
                      "stages": stage_rows,
                      "callbacksBySpecies": callbacks_by_species},
    }


def _stack_geometry(ps: Any, domain: Any, project: dict[str, Any]) -> None:
    g=project["geometry"]; dim=project["dimension"]
    height=float(g.get("baseHeight",0.0))
    for index,layer in enumerate(g["layers"]):
        height += float(layer["thickness"])
        ps.MakePlane(domain,height=height,material=material_handle(ps,layer["material"]),addToExisting=True).apply()
    pattern=g["pattern"]
    if pattern["kind"]=="none": return
    top=g["layers"][-1]; top_thickness=float(top["thickness"])
    eps=float(project["domain"]["gridDelta"])*1e-3
    factory=ps.GeometryFactory(domain.getSetup(),"WorkbenchStackPattern")
    pos=[0.0]*dim
    pos[-1]=height-top_thickness-eps
    angle=-float(pattern.get("taperAngle",0.0))
    if pattern["kind"]=="trench":
        stencil=factory.makeBoxStencil(pos,float(pattern["width"]),top_thickness+2*eps,angle)
    else:
        stencil=factory.makeCylinderStencil(pos,float(pattern["radius"]),top_thickness+2*eps,angle)
    domain.applyBooleanOperation(stencil,ps.ls.BooleanOperationEnum.RELATIVE_COMPLEMENT,False)


def _boolean_fin_2d(ps: Any, domain: Any, geometry: dict[str, Any], material: Any,
                    mask_material: Any) -> None:
    # Use the explicit 2D binding: when a 3D project is set up for extrusion,
    # the top-level ps.MakePlane alias resolves to d3 and rejects a d2 domain.
    ps.d2.MakePlane(domain, height=0.0, material=material, addToExisting=False).apply()
    factory = ps.d2.GeometryFactory(domain.getSetup(), "WorkbenchBooleanFin")
    stencil = factory.makeBoxStencil(
        [0.0, 0.0], float(geometry["width"]), float(geometry["height"]),
        -float(geometry["taperAngle"]),
    )
    domain.applyBooleanOperation(stencil, ps.ls.BooleanOperationEnum.UNION, False)
    mask_height = float(geometry.get("maskHeight", 0.0))
    if mask_height > 0:
        mask = factory.makeBoxStencil(
            [0.0, float(geometry["height"])],
            float(geometry["width"]), mask_height, -float(geometry["maskTaperAngle"]),
        )
        domain.insertNextLevelSetAsMaterial(mask, mask_material)


def _boolean_fin_3d(ps: Any, domain: Any, geometry: dict[str, Any], material: Any,
                    mask_material: Any, y_center: float, y_extent: float) -> None:
    ps.d3.MakePlane(domain, height=0.0, material=material, addToExisting=False).apply()
    factory = ps.d3.GeometryFactory(domain.getSetup(), "WorkbenchBooleanFin")
    fin_stencil = factory.makeBoxStencil(
        [0.0, y_center, 0.0], 2.0 * float(geometry["width"]),
        float(geometry["height"]), -float(geometry["taperAngle"]), y_extent,
    )
    domain.applyBooleanOperation(fin_stencil, ps.ls.BooleanOperationEnum.UNION, False)
    mask_height = float(geometry.get("maskHeight", 0.0))
    if mask_height > 0:
        mask = factory.makeBoxStencil(
            [0.0, y_center, float(geometry["height"])],
            2.0 * float(geometry["width"]), mask_height,
            -float(geometry["maskTaperAngle"]), y_extent,
        )
        domain.insertNextLevelSetAsMaterial(mask, mask_material)


def make_v3_geometry(ps: Any, domain: Any, project: dict[str,Any], *, config: dict[str,Any] | None = None,
                     run_dir: Path | None = None) -> dict[str,Any] | None:
    g=project["geometry"]
    if g["kind"]=="stack":
        # The bulk substrate is the bottom half-space; the first plane marks
        # its top interface, and subsequent planes add requested films.
        ps.MakePlane(domain,height=float(g.get("baseHeight",0.0)),material=material_handle(ps,g["substrateMaterial"]),addToExisting=False).apply()
        _stack_geometry(ps,domain,project)
        return None
    if g["kind"]=="fin":
        construction_decision = fin_construction_decision(project)
        construction = construction_decision["effective"]
        requested_construction = construction_decision["requested"]
        fallback_models = construction_decision["fallbackModels"]
        fallback = requested_construction != construction
        extrusion_route = construction_decision["extrusionRoute"]
        extrusion_fallback_models = construction_decision["extrusionFallbackModels"]
        args = {"finWidth": float(g["width"]), "finHeight": float(g["height"]),
                "finTaperAngle": float(g["taperAngle"]), "maskHeight": float(g["maskHeight"]),
                "maskTaperAngle": float(g["maskTaperAngle"]), "halfFin": False,
                "material": material_handle(ps,g["substrateMaterial"]),
                "maskMaterial": material_handle(ps,g["maskMaterial"])}
        if project["dimension"] == 2:
            if construction == "boolean":
                _boolean_fin_2d(ps, domain, g, args["material"], args["maskMaterial"])
            else:
                ps.d2.MakeFin(domain, **args).apply()
            return {"nativeGeometry": ("viennaps.MakePlane + viennaps.d2.GeometryFactory + Domain.applyBooleanOperation"
                                       if construction == "boolean" else "viennaps.d2.MakeFin"),
                    "construction": construction,
                    "requestedConstruction": requested_construction,
                    "automaticFallback": fallback,
                    "fallbackModels": fallback_models,
                    "constructionFallbackModels": construction_decision["constructionFallbackModels"],
                    "constructionFallbackReason": construction_decision["constructionFallbackReason"],
                    "extrusionRoute": extrusion_route,
                    "automaticExtrusionFallback": False,
                    "extrusionFallbackModels": extrusion_fallback_models,
                    "arguments": {key:value for key,value in args.items() if key not in ("material", "maskMaterial")},
                    "substrateMaterial":g["substrateMaterial"],"maskMaterial":g["maskMaterial"]}
        if g.get("extrusion") is not None and extrusion_route == "direct-stencil":
            extent = [float(value) for value in g["extrusion"]["extent"]]
            y_extent = extent[1] - extent[0]
            if y_extent <= 0:
                raise RuntimeError("Fin extrusion y extent must have positive length")
            _boolean_fin_3d(ps, domain, g, args["material"], args["maskMaterial"],
                            0.5 * (extent[0] + extent[1]), y_extent)
            return {"nativeGeometry": "viennaps.MakePlane + viennaps.d3.GeometryFactory + Domain.applyBooleanOperation (extrusion extent stencil)",
                    "construction": "boolean", "requestedConstruction": requested_construction,
                    "automaticFallback": fallback, "fallbackModels": fallback_models,
                    "constructionFallbackModels": construction_decision["constructionFallbackModels"],
                    "constructionFallbackReason": construction_decision["constructionFallbackReason"],
                    "extrusionRoute": extrusion_route, "automaticExtrusionFallback": True,
                    "extrusionFallbackModels": extrusion_fallback_models,
                    "finArguments": {key:value for key,value in args.items() if key not in ("material", "maskMaterial")},
                    "extrusionExtent": g["extrusion"]["extent"], "extrusionAxis": 1,
                    "substrateMaterial": g["substrateMaterial"], "maskMaterial": g["maskMaterial"],
                    "boundaryReadback": [str(value) for value in domain.getBoundaryConditions()]}
        if g.get("extrusion") is not None:
            bc = ps.BoundaryType.REFLECTIVE_BOUNDARY if project["domain"]["boundaryXY"] == "reflective" else ps.BoundaryType.PERIODIC_BOUNDARY
            input_domain = ps.d2.Domain(float(project["domain"]["gridDelta"]), float(project["domain"]["xExtent"]), bc)
            if construction == "boolean":
                _boolean_fin_2d(ps, input_domain, g, args["material"], args["maskMaterial"])
            else:
                ps.d2.MakeFin(input_domain, **args).apply()
            extrude = ps.Extrude()
            extrude.setInputDomain(input_domain)
            extrude.setOutputDomain(domain)
            extrude.setExtent([float(value) for value in g["extrusion"]["extent"]])
            extrude.setExtrusionAxis(1)
            extrude.setBoundaryConditions([bc, bc, ps.BoundaryType.INFINITE_BOUNDARY])
            extrude.apply()
            return {"nativeGeometry":("viennaps.MakePlane + viennaps.d2.GeometryFactory + Domain.applyBooleanOperation + viennaps.Extrude"
                                       if construction == "boolean" else "viennaps.d2.MakeFin + viennaps.Extrude"),
                    "construction":construction,
                    "requestedConstruction": requested_construction,
                    "automaticFallback": fallback,
                    "fallbackModels": fallback_models,
                    "constructionFallbackModels": construction_decision["constructionFallbackModels"],
                    "constructionFallbackReason": construction_decision["constructionFallbackReason"],
                    "extrusionRoute": extrusion_route,
                    "automaticExtrusionFallback": False,
                    "extrusionFallbackModels": extrusion_fallback_models,
                    "finArguments":{key:value for key,value in args.items() if key not in ("material", "maskMaterial")},
                    "extrusionExtent":g["extrusion"]["extent"],"extrusionAxis":1,
                    "substrateMaterial":g["substrateMaterial"],"maskMaterial":g["maskMaterial"],
                    "boundaryReadback":[str(value) for value in domain.getBoundaryConditions()]}
        if construction == "boolean":
            _boolean_fin_3d(ps, domain, g, args["material"], args["maskMaterial"],
                            0.0, float(project["domain"]["yExtent"]))
        else:
            ps.d3.MakeFin(domain, **args).apply()
        return {"nativeGeometry":("viennaps.MakePlane + viennaps.d3.GeometryFactory + Domain.applyBooleanOperation"
                                   if construction == "boolean" else "viennaps.d3.MakeFin"),
                "construction":construction,
                "requestedConstruction": requested_construction,
                "automaticFallback": fallback,
                "fallbackModels": fallback_models,
                "constructionFallbackModels": construction_decision["constructionFallbackModels"],
                "constructionFallbackReason": construction_decision["constructionFallbackReason"],
                "extrusionRoute": extrusion_route,
                "automaticExtrusionFallback": False,
                "extrusionFallbackModels": extrusion_fallback_models,
                "arguments":{key:value for key,value in args.items() if key not in ("material", "maskMaterial")},
                "substrateMaterial":g["substrateMaterial"],"maskMaterial":g["maskMaterial"]}
    if g["kind"]=="gds":
        if config is None or run_dir is None:
            raise RuntimeError("GDS geometry requires an isolated run input and artifact directory")
        from backend.gds import add_periodic_extent_support, parse_gds
        source = Path(config.get("gdsAssetPath", "")).resolve()
        try:
            source.relative_to(run_dir.resolve())
        except ValueError as exc:
            raise RuntimeError("GDS asset copy must remain inside the current run directory") from exc
        if not source.is_file() or file_sha256(source) != g["assetId"]:
            raise RuntimeError("GDS source asset is missing or does not match the project's SHA-256")
        library = parse_gds(source.read_bytes())
        selected_layers = [int(row["layer"]) for row in g["layers"]]
        physical_scale = library.meters_per_database_unit / library.user_units_per_database_unit * 1_000_000
        effective_scale = physical_scale * float(g["unitScale"])
        selected_bytes, transform = library.select_and_transform(
            g["structure"], unit_scale=effective_scale, position_um=g["position"][:2],
            selected_layers=selected_layers)
        support_metadata: dict[str, Any] = {}
        boundary_name = project["domain"]["xBoundary"] if project["dimension"] == 2 else project["domain"]["boundaryXY"]
        if boundary_name == "periodic":
            delta = float(project["domain"]["gridDelta"])
            x_extent = float(project["domain"]["xExtent"])
            edge = ps.BoundaryType.PERIODIC_BOUNDARY
            if project["dimension"] == 2:
                probe_geometry = ps.d2.GDSGeometry(delta)
                probe_geometry.setBoundaryConditions([edge, ps.BoundaryType.INFINITE_BOUNDARY])
                probe_path = run_dir / "gds-periodic-bounds-probe.gds"
                probe_path.write_bytes(selected_bytes)
                ps.d2.GDSReader(probe_geometry, str(probe_path)).apply()
                source_bounds = [float(value) for value in probe_geometry.getBounds()]
                tolerance = max(1e-8, delta * 1e-6)
                if source_bounds[0] < -x_extent / 2.0 - tolerance or source_bounds[1] > x_extent / 2.0 + tolerance:
                    raise RuntimeError("periodic GDS geometry extends outside the requested x domain; enlarge the domain or adjust GDS placement")
                del probe_geometry
                selected_bytes, support_metadata = add_periodic_extent_support(
                    selected_bytes, g["structure"], dimension=2, x_extent_um=x_extent,
                    cross_section_y_bounds_um=(source_bounds[2], source_bounds[3]), grid_delta_um=delta)
                probe_path.unlink(missing_ok=True)
            else:
                y_extent = float(project["domain"]["yExtent"])
                probe_geometry = ps.d3.GDSGeometry(delta)
                probe_geometry.setBoundaryConditions([edge, edge, ps.BoundaryType.INFINITE_BOUNDARY])
                probe_path = run_dir / "gds-periodic-bounds-probe.gds"
                probe_path.write_bytes(selected_bytes)
                ps.d3.GDSReader(probe_geometry, str(probe_path)).apply()
                source_bounds = [float(value) for value in probe_geometry.getBounds()]
                tolerance = max(1e-8, delta * 1e-6)
                if (source_bounds[0] < -x_extent / 2.0 - tolerance
                        or source_bounds[1] > x_extent / 2.0 + tolerance
                        or source_bounds[2] < -y_extent / 2.0 - tolerance
                        or source_bounds[3] > y_extent / 2.0 + tolerance):
                    raise RuntimeError("periodic GDS geometry extends outside the requested XY domain; enlarge the domain or adjust GDS placement")
                del probe_geometry
                selected_bytes, support_metadata = add_periodic_extent_support(
                    selected_bytes, g["structure"], dimension=3, x_extent_um=x_extent,
                    y_extent_um=y_extent, grid_delta_um=delta)
                probe_path.unlink(missing_ok=True)
        selected_path = run_dir / "gds-selected.gds"
        selected_path.write_bytes(selected_bytes)
        if project["dimension"] == 2:
            geometry = ps.d2.GDSGeometry(float(project["domain"]["gridDelta"]))
            lateral = ps.BoundaryType.REFLECTIVE_BOUNDARY if project["domain"]["xBoundary"] == "reflective" else ps.BoundaryType.PERIODIC_BOUNDARY
            geometry.setBoundaryConditions([lateral, ps.BoundaryType.INFINITE_BOUNDARY])
            ps.d2.GDSReader(geometry, str(selected_path)).apply()
            native_bounds = [float(value) for value in geometry.getBounds()]
            tolerance = max(1e-7, float(support_metadata.get("coordinateToleranceUm", 0.0)) * 1.1)
            if support_metadata and (abs(native_bounds[0] + float(project["domain"]["xExtent"]) / 2.0) > tolerance
                                     or abs(native_bounds[1] - float(project["domain"]["xExtent"]) / 2.0) > tolerance):
                raise RuntimeError("native periodic 2D GDS bounds do not match the requested x period")
            actual_layers = sorted(int(value) for value in geometry.getAllLayers())
            selected_native_layers = sorted(set(actual_layers) & set(selected_layers))
            if selected_native_layers != sorted(selected_layers):
                raise RuntimeError(f"native GDS layer readback {selected_native_layers} differs from selected layers {sorted(selected_layers)}")
            for mapping in g["layers"]:
                level_set = geometry.layerToLevelSet(int(mapping["layer"]), True)
                domain.insertNextLevelSetAsMaterial(level_set, material_handle(ps, mapping["material"]), False)
        else:
            geometry = ps.d3.GDSGeometry(float(project["domain"]["gridDelta"]))
            lateral = ps.BoundaryType.REFLECTIVE_BOUNDARY if project["domain"]["boundaryXY"] == "reflective" else ps.BoundaryType.PERIODIC_BOUNDARY
            geometry.setBoundaryConditions([lateral, lateral, ps.BoundaryType.INFINITE_BOUNDARY])
            ps.d3.GDSReader(geometry, str(selected_path)).apply()
            native_bounds = [float(value) for value in geometry.getBounds()]
            tolerance = max(1e-7, float(support_metadata.get("coordinateToleranceUm", 0.0)) * 1.1)
            if support_metadata and (abs(native_bounds[0] + float(project["domain"]["xExtent"]) / 2.0) > tolerance
                                     or abs(native_bounds[1] - float(project["domain"]["xExtent"]) / 2.0) > tolerance
                                     or abs(native_bounds[2] + float(project["domain"]["yExtent"]) / 2.0) > tolerance
                                     or abs(native_bounds[3] - float(project["domain"]["yExtent"]) / 2.0) > tolerance):
                raise RuntimeError("native periodic 3D GDS bounds do not match the requested XY periods")
            actual_layers = sorted(int(value) for value in geometry.getAllLayers())
            selected_native_layers = sorted(set(actual_layers) & set(selected_layers))
            if selected_native_layers != sorted(selected_layers):
                raise RuntimeError(f"native GDS layer readback {selected_native_layers} differs from selected layers {sorted(selected_layers)}")
            for mapping in g["layers"]:
                base_height = float(mapping["baseHeight"]) + float(g["position"][2])
                level_set = geometry.layerToLevelSet(int(mapping["layer"]), base_height, float(mapping["height"]),
                                                     bool(mapping.get("mask", False)), True)
                domain.insertNextLevelSetAsMaterial(level_set, material_handle(ps, mapping["material"]), False)
        transform.update({"sourceAssetId":g["assetId"],"selectedStructure":g["structure"],
                          "selectedLayers":selected_layers,"nativeLayers":actual_layers,
                          "selectedNativeLayers":selected_native_layers,
                          **support_metadata,
                          "supportLayers":sorted(set(actual_layers) - set(selected_layers)),
                          "nativeUserUnitUm":physical_scale,"userUnitScaleAdjustment":float(g["unitScale"]),
                          "nativeBounds":[float(value) for value in geometry.getBounds()],
                          "dimension":project["dimension"],
                          "coordinateMapping":"GDS XY -> Workbench x,h in 2D; GDS XY -> x,y and mapped layer heights -> z in 3D",
                          "layerMappings":g["layers"],"filteredFile":"gds-selected.gds",
                          "filteredFileSha256":hashlib.sha256(selected_bytes).hexdigest()})
        atomic_json(run_dir / "gds-transform.json", transform)
        return {"nativeGeometry":"viennaps.GDSReader + GDSGeometry.layerToLevelSet",
                **transform}
    if g["kind"]=="plane":
        ps.MakePlane(domain,height=float(g["height"]),material=material_handle(ps,g["substrateMaterial"]),addToExisting=False).apply()
    elif g["kind"]=="trench":
        ps.MakeTrench(domain=domain,trenchWidth=float(g["width"]),trenchDepth=float(g["depth"]),trenchTaperAngle=float(g["taperAngle"]),maskHeight=float(g["maskHeight"]),maskTaperAngle=float(g["maskTaperAngle"]),halfTrench=False,material=material_handle(ps,g["substrateMaterial"]),maskMaterial=material_handle(ps,g.get("maskMaterial","Mask"))).apply()
    else:
        ps.MakeHole(domain=domain,holeRadius=float(g["radius"]),holeDepth=float(g["depth"]),holeTaperAngle=float(g["taperAngle"]),maskHeight=float(g["maskHeight"]),maskTaperAngle=float(g["maskTaperAngle"]),holeShape=ps.HoleShape.FULL,material=material_handle(ps,g["substrateMaterial"]),maskMaterial=material_handle(ps,g.get("maskMaterial","Mask"))).apply()
    return None


def protected_material_names(project: dict[str,Any], step: dict[str,Any] | None = None) -> list[str]:
    if project["schemaVersion"] in (4,5,6,7) and step is not None and step.get("rateMode")=="material":
        return []
    g=project["geometry"]
    if g["kind"] in ("trench","hole") and float(g.get("maskHeight",0))>0:
        return [g.get("maskMaterial","Mask")]
    if g["kind"]=="stack":
        return list(dict.fromkeys(layer["material"] for layer in g["layers"] if layer["role"]=="mask"))
    if g["kind"]=="gds":
        return list(dict.fromkeys(layer["material"] for layer in g["layers"] if layer.get("mask", False)))
    return []


def material_surface_domain(ps: Any, domain: Any, requested: dict[str, Any]) -> tuple[Any, list[int]]:
    """Build a render-only domain with adjacent equal-material interfaces merged.

    Keep the simulation domain and its full material map intact. When several
    consecutive level sets have the same material, only the newest surface is
    a physical material boundary; older surfaces are internal to that material.
    """
    levelsets = domain.getLevelSets()
    material_ids = material_map(domain)
    if len(levelsets) != len(material_ids):
        raise RuntimeError("ViennaPS level-set count does not match its material map")

    if requested["dimension"] == 3:
        from mesh3d import make_domain
        render_domain = make_domain(ps, requested)
    else:
        render_domain = _make_render_domain_2d(ps, requested)

    for index, material_id in enumerate(material_ids):
        if index + 1 < len(material_ids) and material_id == material_ids[index + 1]:
            continue
        render_domain.insertNextLevelSetAsMaterial(levelsets[index], ps.Material(material_id), False)
    return render_domain, material_ids


def _make_render_domain_2d(ps, requested):
    boundary = (
        ps.BoundaryType.REFLECTIVE_BOUNDARY
        if requested["domain"]["xBoundary"] == "reflective"
        else ps.BoundaryType.PERIODIC_BOUNDARY
    )
    render_domain = ps.Domain(
        float(requested["domain"]["gridDelta"]),
        float(requested["domain"]["xExtent"]),
        boundary,
    )

    return render_domain


def save_snapshot(
    ps: Any,
    domain: Any,
    run_dir: Path,
    *,
    run_id: str,
    step_id: str,
    step_name: str,
    step_index: int,
    requested: dict[str, Any],
    no_op: bool = False,
    extra: dict[str, Any] | None = None,
    extra_outputs: list[dict[str, Any]] | None = None,
    defer_payload: bool = False,
) -> dict[str, Any]:
    """Extract the surface once and persist one snapshot JSON per step.

    ``extra`` fields are merged into the snapshot payload before the single
    atomic write, and ``extra_outputs`` rows are appended to ``outputs``.
    Callers must not re-read and re-serialise the file afterwards: the old
    write -> json.loads -> write cycle cost a full copy of a multi-megabyte
    snapshot per step for no change other than one added key.
    """
    v3 = requested["schemaVersion"] >= 3
    if v3:
        render_domain, levelset_ids = domain, material_map(domain)
    else:
        render_domain, levelset_ids = material_surface_domain(ps, domain, requested)
    is3d = requested["dimension"] == 3
    mesh = render_domain.getSurfaceMesh(addInterfaces=True, sharpCorners=False, minNodeDistanceFactor=0.01)
    if is3d:
        from mesh3d import mesh_report as mesh_report_3d, measure_initial, bounds, readback
    report = mesh_report_3d(mesh) if is3d else mesh_report(mesh)
    measured_geometry = None
    if step_id == "initial" and requested["geometry"]["kind"] in ("plane", "trench", "hole"):
        if is3d:
            measured_geometry = measure_initial(report, requested["geometry"], requested["domain"]["gridDelta"], MATERIAL_IDS[requested["geometry"]["substrateMaterial"]] if v3 else 10)
        else:
            measured_geometry = measure_initial_geometry(report, requested["geometry"], requested["domain"]["gridDelta"])
    if not is3d and requested["geometry"]["kind"] == "trench" and step_id == "initial":
        if not trench_has_two_walls(report, requested["domain"]["gridDelta"], MATERIAL_IDS[requested["geometry"]["substrateMaterial"]] if v3 else 10):
            raise RuntimeError("initial trench contour is incomplete: both positive and negative Si sidewalls are required")
    vtk_name = f"{step_id}.vtk"
    snapshot_name = f"{step_id}.json"
    output_settings = requested.get("outputSettings") or {}
    surface_vtk_enabled = bool(output_settings.get("surfaceMeshVtk", False))
    if surface_vtk_enabled:
        # The surface mesh was already extracted above. Writing it through the
        # level-set writer avoids a second ToSurfaceMesh pass; the bytes are
        # identical to Domain.saveSurfaceMesh (same writer, same arguments).
        ps.ls.VTKWriter(mesh, str(run_dir / vtk_name)).apply()
    output_rows: list[dict[str, Any]] = list(extra_outputs or [])
    artifact_dir = run_dir / "artifacts"
    if any(output_settings.get(key, False) for key in ("levelSets", "levelSetMeshes", "volumeMesh", "nativeMetadata")):
        artifact_dir.mkdir(parents=True, exist_ok=True)
    if output_settings.get("levelSets"):
        prefix = artifact_dir / f"{step_id}-levelsets.vtk"
        render_domain.saveLevelSets(str(prefix))
        paths = sorted(artifact_dir.glob(prefix.name + "_layer*.lvst"), key=_levelset_index)
        if len(paths) != len(levelset_ids):
            raise RuntimeError(f"ViennaPS saveLevelSets produced {len(paths)} files for {len(levelset_ids)} level sets")
        for index, path in enumerate(paths):
            output_rows.append(output_artifact(path, run_dir, run_id, step_id, "levelSet", "VIENNALS_LVST",
                                               levelSetIndex=index, materialId=levelset_ids[index],
                                               gridDelta=float(domain.getGridDelta()), association="native level-set grid"))
    if output_settings.get("levelSetMeshes"):
        prefix = artifact_dir / f"{step_id}-levelset-mesh.vtk"
        # ViennaPS 4.6.2 Domain.saveLevelSetMesh() calls getLevelSetMesh(),
        # which expands the stored level sets in place. Export from a deep
        # copy so optional visualization output cannot change the Domain that
        # is subsequently persisted as .vpsd.
        mesh_export_domain = ps.Domain(render_domain)
        mesh_export_domain.saveLevelSetMesh(str(prefix), 1)
        paths = sorted(artifact_dir.glob(prefix.name + "_layer*.vtp"), key=_levelset_index)
        if len(paths) != len(levelset_ids):
            raise RuntimeError(f"ViennaPS saveLevelSetMesh produced {len(paths)} files for {len(levelset_ids)} level sets")
        for index, path in enumerate(paths):
            output_rows.append(output_artifact(path, run_dir, run_id, step_id, "levelSetMesh", "VTP",
                                               levelSetIndex=index, materialId=levelset_ids[index],
                                               association="native level-set mesh; not the surface mesh"))
    if output_settings.get("volumeMesh"):
        if not is3d:
            raise RuntimeError("native volume-mesh output is only available for 3D domains")
        prefix = artifact_dir / f"{step_id}-volume.vtk"
        volume_export_domain = ps.Domain(render_domain)
        volume_export_domain.saveVolumeMesh(str(prefix), 0.01)
        path = artifact_dir / f"{prefix.name}_volume.vtu"
        if not path.is_file() or path.stat().st_size <= 0:
            raise RuntimeError("ViennaPS saveVolumeMesh did not produce its native VTU file")
        output_rows.append(output_artifact(path, run_dir, run_id, step_id, "volumeMesh", "VTU",
                                           association="native volume mesh", dimension=3))
    # Keep the native surface snapshot for the viewer. A full VTU is written
    # only when explicitly requested above; never create one just to derive a
    # second, closed material surface for the ordinary surface view.
    display_points = report["points"]
    display_triangles = report["triangles"] if is3d else report["lines"]
    display_material_ids = [int(value) for value in report["cellData"]["MaterialIds"]]
    if surface_vtk_enabled:
        output_rows.insert(0, output_artifact(
            run_dir / vtk_name, run_dir, run_id, step_id, "surfaceMesh", "VTK_LEGACY",
            dimension=requested["dimension"], topology="triangles" if is3d else "lines",
            pointCount=len(display_points), cellCount=len(display_triangles),
            association="native domain surface mesh",
        ))
    if output_settings.get("nativeMetadata"):
        native_metadata = domain.getMetaData()
        if any(not isinstance(name, str) or any(not math.isfinite(float(value)) for value in values)
               for name, values in native_metadata.items()):
            raise RuntimeError("ViennaPS Domain metadata contains an invalid name or non-finite value")
        metadata_path = artifact_dir / f"{step_id}-domain-metadata.json"
        atomic_json(metadata_path, {
            "schemaVersion": 1,
            "source": "viennaps.Domain.getMetaData",
            "metadataLevel": str(domain.getMetaDataLevel()),
            "nativeUnit": None,
            "association": "domain metadata; native binding declares no point/cell association",
            "values": native_metadata,
            "dimension": int(requested["dimension"]),
            "gridDelta": float(domain.getGridDelta()),
            "materialIdsByLevelSetIndex": levelset_ids,
        })
        output_rows.append(output_artifact(metadata_path, run_dir, run_id, step_id, "nativeMetadata", "JSON",
                                           association="domain metadata", nativeUnit=None,
                                           metadataLevel=str(domain.getMetaDataLevel())))
    check_artifact_budget(run_dir)
    ids = display_material_ids
    if is3d:
        surface_bounds = bounds(display_points)
    elif report["points"]:
        surface_bounds = [
            [min(float(point[0]) for point in report["points"]), min(float(point[1]) for point in report["points"]), 0.0],
            [max(float(point[0]) for point in report["points"]), max(float(point[1]) for point in report["points"]), 0.0],
        ]
    else:
        surface_bounds = None
    materials: dict[str, dict[str, Any]] = {}
    for index, material_id in enumerate(levelset_ids):
        key = str(material_id)
        row = {"name": MATERIAL_NAMES.get(material_id, "unknown"), "levelSetIndexes": [], "sourceStepId": None}
        if v3: row["color"] = MATERIAL_COLORS[material_id]
        materials.setdefault(key, row)
        materials[key]["levelSetIndexes"].append(index)
    for material_id in sorted(set(ids)):
        key = str(material_id)
        if key not in materials:
            row = {"name": MATERIAL_NAMES.get(material_id, "unknown"), "levelSetIndexes": [], "sourceStepId": None}
            if v3 and material_id in MATERIAL_COLORS:
                row["color"] = MATERIAL_COLORS[material_id]
            materials[key] = row
    cells_key = "triangles" if is3d else "lines"
    count_key = "triangleCount" if is3d else "lineCount"
    snapshot = {
        "schemaVersion": requested["schemaVersion"],
        "runId": run_id,
        "stepId": step_id,
        "stepName": step_name,
        "stepIndex": step_index,
        "axisMap": {"x": 0, "y": 1, "z": 2} if is3d else {"x": 0, "h": 1, "z": 2},
        "coordinateUnit": "um",
        "points": display_points,
        cells_key: display_triangles,
        "cellData": {"materialIds": ids},
        "materials": materials,
        "mesh": {**({"vtk": vtk_name} if surface_vtk_enabled else {}),
                 "pointCount": len(display_points), count_key: len(display_triangles)},
        "gridDelta": float(domain.getGridDelta()),
        "materialIdsByLevelSetIndex": levelset_ids,
        "sameMaterialInternalInterfacesHidden": False if v3 else True,
        "outputs": output_rows,
        "noOp": no_op,
        "measuredGeometry": measured_geometry,
    }
    if is3d:
        snapshot.update(dimension=3,topology="triangles",surfaceBounds=surface_bounds,domainReadback=readback(domain,requested,report))
        snapshot["materialSurfaceStatus"] = "unavailable"
        substrate_name = requested["geometry"].get("substrateMaterial")
        substrate_id = MATERIAL_IDS.get(substrate_name) if isinstance(substrate_name, str) else None
        if substrate_id is not None:
            snapshot["substrateMaterialId"] = substrate_id
        snapshot["mesh"]["topology"]="triangles"
    elif v3:
        snapshot.update(dimension=2, topology="lines", surfaceBounds=surface_bounds)
        snapshot["mesh"]["topology"]="lines"
    if v3 and requested["geometry"]["kind"]=="stack":
        snapshot["stackReadback"]={"requestedBaseHeight":float(requested["geometry"].get("baseHeight",0.0)),
                                   "requestedLayers":requested["geometry"]["layers"],
                                   "nativeMaterialMap":levelset_ids,
                                   "pattern":requested["geometry"]["pattern"]}
    if extra:
        snapshot.update(extra)
    metadata = {**({"dimension":3,"topology":"triangles"} if is3d else {}), "stepId": step_id, "name": step_name, "stepIndex": step_index, "snapshot": snapshot_name,
                **({"vtk": vtk_name} if surface_vtk_enabled else {}), "outputs": output_rows,
                "pointCount": len(display_points), count_key: len(display_triangles), "materialIds": sorted(set(ids)),
                "materialIdsByLevelSetIndex": levelset_ids, "noOp": no_op, "measuredGeometry": measured_geometry,
                "surfaceBounds": surface_bounds, **(extra or {})}
    if defer_payload:
        # Keep the run's critical path free of the multi-megabyte JSON encode
        # and write. The document is regenerated from the step's .vpsd state
        # the first time a consumer asks for it (backend.snapshots).
        atomic_json(run_dir / f"{step_id}.meta.json",
                    {"schemaVersion": 1, "deferred": True, "metadata": metadata})
    else:
        atomic_json(run_dir / snapshot_name, snapshot)
    check_artifact_budget(run_dir)
    return metadata


def _event_path(step: dict[str, Any]) -> str:
    return str(step["id"])


def apply_geometry_operation(ps: Any, domain: Any, project: dict[str, Any], step: dict[str, Any]) -> dict[str, Any]:
    model = step["model"]
    if model == "removeMaterial":
        material = material_handle(ps, step["material"])
        domain.removeMaterial(material)
        return {"geometryOperation": "removeMaterial", "material": step["material"], "materialId": MATERIAL_IDS[step["material"]]}
    if model == "planarize":
        cutoff = float(step["cutoffHeight"])
        ps.Planarize(domain, cutoff).apply()
        return {"geometryOperation": "planarize", "cutoffHeight": cutoff}
    if model == "boolean":
        factory = ps.GeometryFactory(domain.getSetup(), f"WorkbenchBoolean_{step['id']}")
        position = [float(value) for value in step["position"]]
        width = float(step["width"])
        height = float(step["height"])
        angle = float(step["angle"])
        if project["dimension"] == 2:
            operand = factory.makeBoxStencil(position[:2], width, height, angle)
        else:
            operand = factory.makeBoxStencil(position, width, height, angle, float(step["depth"]))
        enum_name = {"union": "UNION", "intersection": "INTERSECT", "difference": "RELATIVE_COMPLEMENT"}[step["booleanOperation"]]
        operation = getattr(ps.ls.BooleanOperationEnum, enum_name)
        domain.applyBooleanOperation(operand, operation, bool(step["applyToAll"]))
        return {"geometryOperation": "boolean", "booleanOperation": step["booleanOperation"],
                "operand": "box", "position": position, "width": width, "height": height,
                "depth": float(step["depth"]) if project["dimension"] == 3 else None,
                "angle": angle, "applyToAll": bool(step["applyToAll"]), "nativeBooleanEnum": enum_name}
    raise RuntimeError(f"unsupported geometry operation {model!r}")


def calculate_single_flux(ps: Any, domain: Any, project: dict[str, Any], step: dict[str, Any],
                          engine: str, seed: int, run_dir: Path,
                          flux_strategy: str | None = None) -> dict[str, Any]:
    if step["operation"] == "deposit":
        domain.duplicateTopLevelSet(material_handle(ps, step["depositMaterial"]))
    split_flux = None
    multi_actual = None
    specialized_actual = None
    callback_state = None
    special_process_parameters: list[Any] = []
    process_duration = float(step.get("duration") or 0.0)
    if step["model"] == "specialized":
        model, special_process_parameters, specialized_actual, process_duration = build_specialized_model(
            ps, domain, project, step, lambda name: material_handle(ps, name), MATERIAL_IDS)
        rate = None
    elif step["model"] == "multiParticle":
        ion_count = sum(row.get("kind") == "ion" for row in step.get("species", []))
        if engine == "GPU_DISK" and ion_count > 1 and flux_strategy == GPU_MULTIPARTICLE_FLUX_STRATEGY:
            split_flux = calculate_multi_particle_flux_by_species(ps, domain, project, step, engine, seed, run_dir)
            multi_actual = multi_particle_step_summary(step)
            callback_state = None
        else:
            model, multi_actual, callback_state = build_multi_particle_model(ps, step)
        rate = None
    else:
        rate, material_rates = signed_rates(step)
        if step.get("rateMode") == "material":
            model = ps.SingleParticleProcess(rate=rate, stickingProbability=float(step["stickingProbability"]),
                                             sourceExponent=float(step["sourceExponent"]), maskMaterials=[])
            for name, material_rate in material_rates.items():
                model.setMaterialRate(material_handle(ps, name), material_rate)
        elif project["schemaVersion"] >= 3:
            masks = [material_handle(ps, name) for name in protected_material_names(project, step)]
            model = ps.SingleParticleProcess(rate=rate, stickingProbability=float(step["stickingProbability"]),
                                             sourceExponent=float(step["sourceExponent"]), maskMaterials=masks)
        else:
            model = ps.SingleParticleProcess(rate=rate, stickingProbability=float(step["stickingProbability"]),
                                             sourceExponent=float(step["sourceExponent"]), maskMaterial=ps.Material.Mask)
    if engine not in {"CPU_DISK", "CPU_TRIANGLE", "GPU_DISK", "GPU_TRIANGLE"}:
        raise RuntimeError("single flux calculation has no verified concrete engine")
    if split_flux is None:
        process = ps.Process(domain, model, process_duration)
        process.setFluxEngineType(getattr(ps.FluxEngineType, engine))
        rays, ray_actual = configure_rays(ps, project, seed)
        process.setParameters(rays)
        for process_parameters in special_process_parameters:
            process.setParameters(process_parameters)
        started = time.perf_counter()
        flux_mesh = process.calculateFlux()
        elapsed = time.perf_counter() - started
        report = native_mesh_report(flux_mesh)
    else:
        rays, ray_actual = configure_rays(ps, project, seed)
        report = split_flux["report"]
        elapsed = sum(float(row["elapsedSeconds"]) for row in split_flux["species"])
    if not report["points"]:
        raise RuntimeError("ViennaPS Process.calculateFlux returned an empty mesh")
    if flux_strategy == GPU_MULTIPARTICLE_NATIVE_FLUX_STRATEGY:
        expected_fields = {f"{row['label']}{index}" for index, row in enumerate(step["species"])}
        present_fields = set(report["pointData"]["scalars"]) | set(report["cellData"]["scalars"])
        if missing := expected_fields - present_fields:
            raise RuntimeError(f"native GPU MultiParticle flux is missing species fields: {sorted(missing)}")
    for association in ("pointData", "cellData"):
        for kind in ("scalars", "vectors"):
            for label, values in report[association][kind].items():
                if any(not math.isfinite(value) for row in values for value in (row if isinstance(row, list) else [row])):
                    raise RuntimeError(f"ViennaPS flux field {label!r} contains non-finite values")
    vtk_name = "flux.vtp"
    vtk_path = run_dir / vtk_name
    if split_flux is None:
        writer = ps.ls.VTKWriter(flux_mesh, ps.ls.FileFormatEnum.VTP, str(vtk_path))
        writer.apply()
    else:
        merge_native_flux_vtp(split_flux["speciesPaths"], vtk_path)
    if not vtk_path.is_file() or vtk_path.stat().st_size <= 0:
        raise RuntimeError("ViennaLS VTKWriter did not create the native flux mesh export")
    result = {
        "schemaVersion": 1,
        "source": "viennaps.Process.calculateFlux",
        "dimension": int(project["dimension"]),
        "axisMap": {"x": 0, "y": 1, "z": 2} if project["dimension"] == 3 else {"x": 0, "h": 1, "z": 2},
        "coordinateUnit": "um",
        "fieldUnitPolicy": "native mesh labels do not declare units; nativeUnit is null rather than inferred",
        "meshIdentity": report["meshIdentity"],
        "mesh": {"pointCount": len(report["points"]),
                 "cellCounts": {name: len(rows) for name, rows in {key: report.get(key, []) for key in ("lines", "triangles", "tetras", "hexas", "vertices")}.items()},
                 "vtk": vtk_name, "format": "VTP"},
        "points": report["points"],
        "topology": {key: report[key] for key in ("lines", "triangles", "tetras", "hexas", "vertices") if key in report},
        "pointData": report["pointData"],
        "cellData": report["cellData"],
        "fields": report["fields"],
        "execution": {"stepId": step["id"], "stepName": step["name"], "model": step["model"],
                      "operation": step["operation"], "actualRate": rate, "actualEngine": engine,
                      "actualSeed": seed, "cpuThreads": int(project["solver"]["cpuThreads"]),
                      "rays": ray_actual, "elapsedSeconds": elapsed,
                      "processDuration": "not-used-by-single-pass-calculateFlux"},
        "versions": {"viennaps": version("viennaps"), "viennals": version("viennals")},
    }
    if multi_actual is not None:
        result["execution"]["multiParticle"] = multi_actual
        if callback_state is not None:
            result["execution"]["rateCallback"] = callback_state
        if split_flux is not None:
            result["execution"]["multiParticleFlux"] = {
                **split_flux["execution"], "species": split_flux["species"],
            }
        elif flux_strategy == GPU_MULTIPARTICLE_NATIVE_FLUX_STRATEGY:
            result["execution"]["multiParticleFlux"] = {
                "strategy": flux_strategy, "nativeProcessCount": 1,
                "species": [{"label": row["label"], "kind": row["kind"],
                             "actualEngine": engine, "actualSeed": int(seed)} for row in step["species"]],
            }
    if specialized_actual is not None:
        result["execution"]["nativeModel"] = {**specialized_actual, "actualSeed": seed,
                                                 "actualEngine": engine,
                                                 "cpuThreads": int(project["solver"]["cpuThreads"])}
    atomic_json(run_dir / "flux.json", result)
    check_artifact_budget(run_dir)
    return {"fileName": "flux.json", "vtk": vtk_name, "meshIdentity": report["meshIdentity"],
            "pointCount": len(report["points"]), "fieldCount": len(report["fields"]),
            "fieldNames": [field["name"] for field in report["fields"]], "actualEngine": engine, "actualSeed": seed}


def describe_intermediate_vtp(path: Path, *, step_id: str, run_id: str,
                              run_dir: Path, project: dict[str, Any]) -> dict[str, Any]:
    """Record native VTP arrays and topology without remapping them to a surface mesh."""
    root = ET.parse(path).getroot()
    if root.attrib.get("type") != "PolyData":
        raise RuntimeError(f"ViennaLS intermediate output {path.name} is not VTK PolyData")
    piece = root.find(".//Piece")
    if piece is None:
        raise RuntimeError(f"ViennaLS intermediate output {path.name} has no PolyData Piece")
    point_count = int(piece.attrib.get("NumberOfPoints", "0"))
    cell_counts = {name: int(piece.attrib.get(f"NumberOf{name}", "0"))
                   for name in ("Verts", "Lines", "Strips", "Polys")}
    fields = []
    for container, association in (("PointData", "pointData"), ("CellData", "cellData")):
        data = piece.find(container)
        if data is None:
            continue
        tuples = point_count if association == "pointData" else sum(cell_counts.values())
        for array in data.findall("DataArray"):
            name = array.attrib.get("Name")
            if not name:
                continue
            components = int(array.attrib.get("NumberOfComponents", "1"))
            array_tuples = int(array.attrib.get("NumberOfTuples", str(tuples)))
            if components < 1 or array_tuples != tuples:
                raise RuntimeError(f"ViennaLS intermediate field {name!r} does not align with its native {association} mesh")
            fields.append({"name": name, "association": association, "components": components,
                           "tuples": array_tuples, "nativeType": array.attrib.get("type"),
                           "nativeUnit": None, "unitStatus": "not-provided-by-native-vtk-array"})
    if point_count <= 0 or not fields:
        raise RuntimeError(f"ViennaLS intermediate output {path.name} has no points or named data arrays")
    relative = path.resolve().relative_to(run_dir.resolve())
    return {"stepId": step_id, "fileName": relative.as_posix(),
            "url": f"/api/runs/{run_id}/intermediate-outputs/{step_id}/{path.name}",
            "format": "VTP", "pointCount": point_count, "cellCounts": cell_counts,
            "coordinateUnit": "um", "axisMap": {"x": 0, "y": 1, "z": 2} if project["dimension"] == 3 else {"x": 0, "h": 1, "z": 2},
            "fields": fields, "sizeBytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "associationPolicy": "native VTP pointData/cellData retained; not projected onto the domain surface"}


def execute(input_path: Path, run_dir: Path) -> None:
    input_path = input_path.resolve()
    run_dir = run_dir.resolve()
    input_path.relative_to(ROOT)
    run_dir.relative_to(ROOT)
    config = json.loads(input_path.read_text(encoding="utf-8"), parse_constant=lambda item: (_ for _ in ()).throw(ValueError(f"invalid JSON constant {item}")))
    project = config["project"]
    kind = config["kind"]
    run_id = config["runId"]
    run_seed = int(config["runSeed"]) if config.get("runSeed") is not None else None
    engine_preference = project["solver"]["enginePreference"]
    actual_steps = [step for step in project["steps"] if step["enabled"]] if kind == "simulate" else []
    step_engines = config.get("stepEngines", {})
    if kind == "flux" and config.get("analysisStepId"):
        step_engines = {**step_engines, config["analysisStepId"]: step_engines.get(config["analysisStepId"])}
    uses_cuda_engine = any(isinstance(engine, str) and engine.startswith("GPU_") for engine in step_engines.values())
    selected_gpu_device = None
    cuda_readback = None
    gpu_sample: dict[str, Any] | None = None
    if uses_cuda_engine:
        configured_device = config.get("selectedGpuDevice")
        if isinstance(configured_device, dict) and configured_device.get("uuid"):
            # The supervisor already enumerated and pinned this device; its
            # nvidia-smi inventory call costs 0.27-0.75 s on this host. The
            # CUDA Runtime readback below still verifies PCI bus and UUID.
            inventory = {"status": "available", "devices": [configured_device],
                         "preferredGpuDeviceId": configured_device.get("uuid"), "reason": None,
                         "probe": {"source": "supervisor-selected-device"}}
        else:
            inventory = cuda_devices()
        requested_device = project.get("solver", {}).get("gpuDeviceId")
        if configured_device and requested_device is None:
            requested_device = configured_device.get("uuid")
        selected_gpu_device = resolve_gpu_device(
            requested_device, inventory)
        if configured_device and configured_device.get("uuid") != selected_gpu_device["uuid"]:
            raise RuntimeError("worker device selection differs from the saved project's explicit GPU UUID")
        os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
        os.environ["CUDA_VISIBLE_DEVICES"] = selected_gpu_device["uuid"]
    flux_step = None
    if kind == "flux":
        flux_step_id = config.get("analysisStepId")
        flux_step = next((step for step in project["steps"] if step.get("id") == flux_step_id), None)
        if project["schemaVersion"] < 7 or not flux_step or not flux_step.get("enabled") or not uses_flux(flux_step):
            raise RuntimeError("single flux calculation requires an enabled schema-7 step with a native particle flux model")

    import viennaps as ps

    native_engine_fingerprint = config.get("nativeEngineFingerprint")
    if native_engine_fingerprint is not None:
        from backend.native_build import fingerprint_package
        observed = fingerprint_package(Path(ps.__file__).resolve().parent)
        if observed != native_engine_fingerprint:
            raise RuntimeError("worker loaded a different patched ViennaPS engine build")

    if uses_cuda_engine:
        cuda_readback = cuda_runtime_readback(selected_gpu_device)

    if project.get("schemaVersion", 1) >= 3:
        # Schema-3+ project rates and coordinates use the declared um/s unit
        # system for every native model, not only the dedicated processes.
        ps.Length.setUnit("um")
        ps.Time.setUnit("s")

    ps.setDimension(int(project["dimension"]))
    ps.setNumThreads(int(project["solver"]["cpuThreads"]))
    ps_version = version("viennaps")
    ls_version = version("viennals")
    if ps_version != config["expectedVersions"]["viennaps"] or ls_version != config["expectedVersions"]["viennals"]:
        raise RuntimeError(f"installed package version changed: ViennaPS {ps_version}, ViennaLS {ls_version}")
    configure_native_materials(ps, config.get("customMaterials", []))
    if project.get("schemaVersion") == 7 and project.get("outputSettings", {}).get("nativeMetadata"):
        metadata_level = ps.MetaDataLevel.FULL
    else:
        metadata_level = None

    initial_state_path = None
    state_ref = project.get("continuationState")
    if state_ref is not None:
        if project["schemaVersion"] not in (6, 7):
            raise RuntimeError("native Domain continuation requires schemaVersion 6 or 7")
        initial_state_path = Path(config.get("initialStatePath", "")).resolve()
        try:
            initial_state_path.relative_to(ROOT)
        except ValueError as exc:
            raise RuntimeError("initial Domain state path escapes the workbench root") from exc
        if not initial_state_path.is_file() or file_sha256(initial_state_path) != state_ref["stateId"]:
            raise RuntimeError("initial Domain state file is missing or its SHA-256 does not match the project reference")
        if state_ref["viennapsVersion"] != ps_version or state_ref["viennalsVersion"] != ls_version:
            raise RuntimeError("initial Domain state engine versions do not match the installed ViennaPS/ViennaLS")

    geometry_readback = None
    initial_geometry_phase = phase("initialDomainAndGeometry")
    if project["dimension"] == 3:
        from mesh3d import make_domain, make_geometry
        import mesh3d
        mesh3d.configure_material_catalog(MATERIAL_ROWS)
        domain=make_domain(ps,project)
        if initial_state_path is not None:
            ps.Reader(domain, str(initial_state_path)).apply()
        elif project["schemaVersion"] >= 3:
            geometry_readback = make_v3_geometry(ps,domain,project,config=config,run_dir=run_dir)
        else:
            make_geometry(ps,domain,project["geometry"])
        readback_delta=float(domain.getGridDelta())
    else:
        boundary = ps.BoundaryType.REFLECTIVE_BOUNDARY if project["domain"]["xBoundary"] == "reflective" else ps.BoundaryType.PERIODIC_BOUNDARY
        domain = ps.Domain(float(project["domain"]["gridDelta"]), float(project["domain"]["xExtent"]), boundary)
        if initial_state_path is not None:
            ps.Reader(domain, str(initial_state_path)).apply()
        else:
            geometry = project["geometry"]
            if project["schemaVersion"] >= 3:
                geometry_readback = make_v3_geometry(ps,domain,project,config=config,run_dir=run_dir)
            elif geometry["kind"] == "plane":
                ps.MakePlane(domain, height=float(geometry["height"]), material=ps.Material.Si, addToExisting=False).apply()
            else:
                ps.MakeTrench(
                    domain=domain,
                    trenchWidth=float(geometry["width"]),
                    trenchDepth=float(geometry["depth"]),
                    trenchTaperAngle=float(geometry["taperAngle"]),
                    maskHeight=float(geometry["maskHeight"]),
                    maskTaperAngle=float(geometry["maskTaperAngle"]),
                    halfTrench=False,
                    material=ps.Material.Si,
                    maskMaterial=ps.Material.Mask,
                ).apply()
        readback_delta = float(domain.getGridDelta())
        if not math.isclose(readback_delta, float(project["domain"]["gridDelta"]), rel_tol=0.0, abs_tol=1e-12):
            raise RuntimeError(f"Domain gridDelta readback {readback_delta} differs from the requested value")
    initial_geometry_phase.stop()

    if metadata_level is not None:
        domain.enableMetaData(metadata_level)

    if state_ref is not None:
        observed_map = material_map(domain)
        if observed_map != state_ref["materialIdsByLevelSetIndex"]:
            raise RuntimeError("Reader material map differs from the saved Domain state reference")
        if int(domain.getNumberOfLevelSets()) != state_ref["levelSetCount"]:
            raise RuntimeError("Reader level-set count differs from the saved Domain state reference")
        if not math.isclose(float(domain.getGridDelta()), state_ref["gridDelta"], rel_tol=0.0, abs_tol=1e-12):
            raise RuntimeError("Reader gridDelta differs from the saved Domain state reference")
        observed_boundaries = ["reflective" if str(value) == "0" else "periodic" if str(value) == "2" else "infinite" if str(value) == "1" else f"unknown:{value}" for value in domain.getBoundaryConditions()]
        if observed_boundaries != state_ref["boundaryConditions"]:
            raise RuntimeError("Reader boundary conditions differ from the saved Domain state reference")

    # In ViennaPS 4.6.2 getBoundaryConditions() is only safe after a geometry
    # has been applied. Its enum instances also must not be converted with
    # int(); their stable string values are recorded after checking the enum.
    boundary_values = [str(value) for value in domain.getBoundaryConditions()]
    expected_x_boundary = "0" if project["domain"].get("xBoundary", project["domain"].get("boundaryXY")) == "reflective" else "2"
    if boundary_values != ([expected_x_boundary,expected_x_boundary,"1"] if project["dimension"]==3 else [expected_x_boundary,"1"]):
        raise RuntimeError(f"Domain boundary readback {boundary_values} differs from the requested x boundary and infinite h boundary")

    print("note: 'Process time: X / Y s' lines below report simulated process time, "
          "not wall-clock seconds.", flush=True)
    event(run_dir, {"type": "initializing", "runId": run_id})
    initial_name = f"新工艺起始状态 · {state_ref['sourceStepId'] if state_ref and state_ref.get('sourceStepId') else '导入的 Domain 状态'}" if state_ref else "初始结构"
    with phase("initialSnapshot"):
        initial = save_snapshot(ps, domain, run_dir, run_id=run_id, step_id="initial", step_name=initial_name, step_index=0,
                                requested=project,
                                extra={"geometryReadback": geometry_readback} if geometry_readback is not None else None)
    with phase("initialNativeState"):
        initial["nativeState"] = save_native_state(ps, domain, run_dir, project=project, run_id=run_id,
                                                   step_id="initial", viennaps_version=ps_version, viennals_version=ls_version)
    snapshots = [initial]
    actual_execution: list[dict[str, Any]] = []
    intermediate_outputs: list[dict[str, Any]] = []
    event(run_dir, {"type": "snapshot", **initial})
    flux_result = None
    slice_result = None
    if kind == "slice":
        slice_request = config.get("sliceRequest")
        if project["dimension"] != 3 or not isinstance(slice_request, dict) or slice_request.get("axis") != 1:
            raise RuntimeError("native Slice requires a 3D source and the verified y-normal axis")
        slice_position = float(slice_request["position"])
        if not math.isfinite(slice_position) or abs(slice_position) > float(project["domain"]["yExtent"]) / 2:
            raise RuntimeError("native Slice position must lie inside the 3D y domain")
        ps.setDimension(2)
        slice_boundary = ps.BoundaryType.REFLECTIVE_BOUNDARY if project["domain"]["boundaryXY"] == "reflective" else ps.BoundaryType.PERIODIC_BOUNDARY
        sliced_domain = ps.d2.Domain(float(project["domain"]["gridDelta"]), float(project["domain"]["xExtent"]), slice_boundary)
        ps.Slice(domain, sliced_domain, 1, slice_position).apply()
        if int(sliced_domain.getNumberOfLevelSets()) <= 0:
            raise RuntimeError("ViennaPS Slice returned no level sets at the requested y position")
        slice_project = json.loads(json.dumps(project, ensure_ascii=False))
        slice_project["dimension"] = 2
        slice_project["domain"] = {"gridDelta":float(project["domain"]["gridDelta"]),
                                    "xExtent":float(project["domain"]["xExtent"]),
                                    "xBoundary":project["domain"]["boundaryXY"]}
        slice_project.pop("view3D", None)
        slice_readback = {"nativeOperation":"viennaps.Slice", "axis":1,
                          "axisLabel":"y-normal; output plane uses x/z coordinates mapped to x/h",
                          "position":slice_position, "sourceDimension":3, "outputDimension":2,
                          "viewerClipping":False, "levelSetCount":int(sliced_domain.getNumberOfLevelSets()),
                          "materialIdsByLevelSetIndex":material_map(sliced_domain)}
        slice_result = save_snapshot(ps, sliced_domain, run_dir, run_id=run_id, step_id="true-slice",
                                     step_name="ViennaPS 真实 3D 几何切片", step_index=1, requested=slice_project,
                                     extra={"sliceReadback": slice_readback})
        snapshots.append(slice_result)
        event(run_dir, {"type":"snapshot", **slice_result})
        ps.setDimension(3)
    if flux_step is not None:
        selected_engine = config.get("stepEngines", {}).get(flux_step["id"])
        if selected_engine is None:
            raise RuntimeError("single flux calculation has no selected concrete engine")
        if run_seed is None:
            raise RuntimeError("single flux calculation requires a recorded fixed random seed")
        flux_multi_ion = (flux_step.get("model") == "multiParticle"
                          and sum(row.get("kind") == "ion" for row in flux_step.get("species", [])) > 1
                          and selected_engine == "GPU_DISK")
        actual_flux_strategy = config.get("stepFluxStrategies", {}).get(flux_step["id"])
        if (flux_multi_ion and actual_flux_strategy not in {GPU_MULTIPARTICLE_FLUX_STRATEGY, GPU_MULTIPARTICLE_NATIVE_FLUX_STRATEGY}
                or not flux_multi_ion and actual_flux_strategy is not None):
            raise RuntimeError("single-pass GPU MultiParticle flux strategy does not match the exact worker configuration")
        if actual_flux_strategy == GPU_MULTIPARTICLE_NATIVE_FLUX_STRATEGY and not native_engine_fingerprint:
            raise RuntimeError("native multi-ion GPU flux requires a pinned engine build fingerprint")
        expected_flux_strategy = actual_flux_strategy
        if config.get("stepExecutionStrategies", {}).get(flux_step["id"]) is not None:
            raise RuntimeError("single-pass flux must not claim the process-apply operator-splitting strategy")
        event(run_dir, {"type": "step-started", "stepId": flux_step["id"], "index": 1, "total": 1, "name": flux_step["name"]})
        signed_rate = None if flux_step["model"] in {"multiParticle", "specialized"} else signed_rates(flux_step)[0]
        flux_result = calculate_single_flux(ps, domain, project, flux_step, selected_engine, run_seed, run_dir,
                                            flux_strategy=actual_flux_strategy)
        if selected_engine.startswith("GPU_"):
            post_flux_cuda_readback = cuda_runtime_readback(selected_gpu_device)
            gpu_usage = nvidia_process_readback(selected_gpu_device["uuid"])
            if gpu_sample is None:
                gpu_sample = nvidia_gpu_sample()
            flux_result["cudaDeviceReadback"] = {**cuda_readback, "afterCalculateFlux": post_flux_cuda_readback,
                                                  "process": gpu_usage, "gpuSample": gpu_sample}
            flux_path = run_dir / flux_result["fileName"]
            flux_payload = json.loads(flux_path.read_text(encoding="utf-8"))
            flux_payload["execution"]["cudaDeviceReadback"] = flux_result["cudaDeviceReadback"]
            if expected_flux_strategy is not None:
                flux_payload["execution"]["gpuDeviceId"] = selected_gpu_device["uuid"]
                flux_payload["execution"]["multiParticleFlux"]["gpuDeviceId"] = selected_gpu_device["uuid"]
                for species_record in flux_payload["execution"]["multiParticleFlux"]["species"]:
                    species_record["gpuDeviceId"] = selected_gpu_device["uuid"]
            atomic_json(flux_path, flux_payload)
            actual_execution.append({"stepId": flux_step["id"], "engine": selected_engine,
                                     "seed": run_seed, "gpuDeviceId": selected_gpu_device["uuid"],
                                     **({"fluxStrategy": expected_flux_strategy} if expected_flux_strategy else {})})
        else:
            actual_execution.append({"stepId": flux_step["id"], "engine": selected_engine, "seed": run_seed})
        flux_execution=json.loads((run_dir / flux_result["fileName"]).read_text(encoding="utf-8"))["execution"]
        event(run_dir, {"type": "step-configured", "stepId": flux_step["id"], "operation": "flux",
                        "model": flux_step["model"], "noOp": False, "actualEngine": selected_engine,
                        "engineSelectionReason": "pinned concrete engine for ViennaPS Process.calculateFlux; no fallback",
                        "requestedEnginePreference": engine_preference, "actualSeed": run_seed, "actualRate": signed_rate,
                        "duration": None, "actualParameters": {"result": "single-pass flux mesh; process.apply was not called",
                                                                   "rays": flux_execution["rays"],
                                                                   **({"nativeModel": flux_execution["nativeModel"]} if "nativeModel" in flux_execution else {}),
                                                                   **({"multiParticle": flux_execution["multiParticle"]}
                                                                      if "multiParticle" in flux_execution else {}),
                                                                   **({"rateCallback": flux_execution["rateCallback"]}
                                                                      if "rateCallback" in flux_execution else {}),
                                                                   **({"multiParticleFlux": flux_execution["multiParticleFlux"]}
                                                                      if "multiParticleFlux" in flux_execution else {}),
                                                                   **({"cudaDeviceReadback": flux_result["cudaDeviceReadback"]}
                                                                      if "cudaDeviceReadback" in flux_result else {}),
                                                                   "calculateFluxSeconds": flux_execution["elapsedSeconds"]}})
        if selected_engine.startswith("GPU_"):
            event(run_dir, {"type": "step-device-readback", "stepId": flux_step["id"],
                            "cudaDeviceReadback": flux_result["cudaDeviceReadback"]})
        event(run_dir, {"type": "flux-result", **flux_result})

    for index, step in enumerate(actual_steps, start=1):
        event(run_dir, {"type": "step-started", "stepId": step["id"], "index": index, "total": len(actual_steps), "name": step["name"]})
        geometry_step = is_geometry_step(step)
        no_op = not geometry_step and not has_motion(step)
        if geometry_step:
            actual_parameters = apply_geometry_operation(ps, domain, project, step)
            event(run_dir, {"type": "step-configured", "stepId": step["id"], "operation": step["model"], "model": step["model"],
                            "noOp": False, "actualEngine": "not-applicable", "engineSelectionReason": "native geometry operation; no process engine or physical duration",
                            "actualSeed": None, "actualParameters": actual_parameters})
            metadata = save_snapshot(ps, domain, run_dir, run_id=run_id, step_id=step["id"], step_name=step["name"], step_index=index, requested=project,
                                     defer_payload=LAZY_SNAPSHOTS)
            metadata["nativeState"] = save_native_state(ps, domain, run_dir, project=project, run_id=run_id,
                                                        step_id=step["id"], viennaps_version=ps_version, viennals_version=ls_version)
            actual_execution.append({"stepId": step["id"], "engine": "not-applicable", "seed": None})
        elif no_op:
            no_op_parameters: dict[str,Any] = {}
            if project["schemaVersion"] in (4,5,6,7) and step.get("rateMode") == "material":
                fallback,no_op_rates=signed_rates(step)
                no_op_parameters={"rateMode":step["rateMode"],"actualDefaultRate":fallback,
                                  "actualMaterialRatesByName":no_op_rates,
                                  "actualMaterialRatesById":{str(MATERIAL_IDS[name]):value for name,value in no_op_rates.items()},
                                  "nativeRateTable":[{"name":name,"id":MATERIAL_IDS[name],"rate":value} for name,value in sorted(no_op_rates.items())]}
            if project["schemaVersion"] in (5,6,7) and step.get("model")=="directional":
                native_directional,native_isotropic=signed_directional_rates(step)
                raw_direction=[float(value) for value in step["direction"]]
                length=math.sqrt(sum(value*value for value in raw_direction))
                no_op_parameters={"direction":raw_direction,"nativeUnitDirection":[value/length for value in raw_direction],
                    "directionalRateMagnitude":float(step["directionalRateMagnitude"]),"isotropicRateMagnitude":float(step["isotropicRateMagnitude"]),
                    "nativeDirectionalVelocity":native_directional,"nativeIsotropicVelocity":native_isotropic,
                    "calculateVisibility":"not-applicable: no rate is active"}
            if step.get("model")=="multiParticle":
                no_op_parameters={"nativeProcess":"viennaps.MultiParticleProcess (not called: zero coefficient)",
                    "species":step["species"],"rateCoefficients":step["rateCoefficients"],
                    "rateFunction":"not-applicable: all configured coefficients are zero"}
            previous = snapshots[-1]
            next_vtk_name = f"{step['id']}.vtk"
            if previous.get("vtk"):
                shutil.copyfile(run_dir / previous["vtk"], run_dir / next_vtk_name)
            previous_path = run_dir / previous["snapshot"]
            copied = None
            if previous_path.is_file():
                shutil.copyfile(previous_path, run_dir / f"{step['id']}.json")
                copied = json.loads((run_dir / f"{step['id']}.json").read_text(encoding="utf-8"))
                copied_mesh = dict(copied["mesh"])
                if previous.get("vtk"):
                    copied_mesh["vtk"] = next_vtk_name
                else:
                    copied_mesh.pop("vtk", None)
                copied.update({"stepId": step["id"], "stepName": step["name"], "stepIndex": index, "noOp": True,
                               "mesh": copied_mesh})
                atomic_json(run_dir / f"{step['id']}.json", copied)
            check_artifact_budget(run_dir)
            metadata = {"stepId": step["id"], "name": step["name"], "stepIndex": index, "snapshot": f"{step['id']}.json",
                        **({"vtk": next_vtk_name} if previous.get("vtk") else {}),
                        "pointCount": previous["pointCount"], **{k:previous[k] for k in ("lineCount","triangleCount","topology","dimension") if k in previous},
                        "materialIds": previous["materialIds"], "materialIdsByLevelSetIndex": previous["materialIdsByLevelSetIndex"], "noOp": True,
                        "measuredGeometry": (copied or previous).get("measuredGeometry")}
            if copied is None:
                # Previous step is deferred: this no-op step shares its native
                # state and is materialised from state-<step>.vpsd on first read.
                atomic_json(run_dir / f"{step['id']}.meta.json",
                            {"schemaVersion": 1, "deferred": True, "metadata": metadata})
            metadata["nativeState"] = save_native_state(ps, domain, run_dir, project=project, run_id=run_id,
                                                        step_id=step["id"], viennaps_version=ps_version, viennals_version=ls_version)
            event(run_dir, {"type": "step-configured", "stepId": step["id"], "operation": step["operation"], "model": step["model"],
                            "noOp": True, "actualEngine": "not-applicable", "engineSelectionReason": "zero rate; ViennaPS Process was not called",
                            "actualSeed": None, "actualRate": 0.0, "duration": step["duration"], "actualParameters": no_op_parameters})
            actual_execution.append({"stepId": step["id"], "engine": "not-applicable", "seed": None})
        else:
            deposition = step["operation"] == "deposit"
            directional = project["schemaVersion"] in (5,6,7) and step.get("model")=="directional"
            specialized = project["schemaVersion"] == 7 and step.get("model") == "specialized"
            rate = None
            signed_material_rates: dict[str,float] = {}
            multi_actual: dict[str,Any] | None = None
            callback_state: dict[str,Any] | None = None
            if deposition:
                if specialized and step.get("nativeModel") == "selectiveEpitaxy":
                    # The installed SelectiveEpitaxy binding checks the native
                    # enum material identity of its epitaxial top level set.
                    deposit_material = ps.Material.SiGe
                else:
                    deposit_material = material_handle(ps,step["depositMaterial"]) if project["schemaVersion"]>=3 else ps.Material.SiO2
                domain.duplicateTopLevelSet(deposit_material)
            if os.environ.get("VIENNAPS_DEBUG_NATIVE_CALLBACKS") == "1" and step.get("model") == "multiParticle":
                debug_mesh = domain.getSurfaceMesh(addInterfaces=True, sharpCorners=False, minNodeDistanceFactor=.01)
                debug_domain = native_mesh_report(debug_mesh)
                debug_domain["materialIdsByLevelSetIndex"] = material_map(domain)
                debug_domain["levelSetCount"] = int(domain.getNumberOfLevelSets())
                atomic_json(run_dir / f"native-debug-pre-process-{step['id']}.json", debug_domain)
            particle = uses_flux(step)
            material_mode = project["schemaVersion"] in (4,5,6,7) and step.get("rateMode")=="material"
            directional_parameters: dict[str,Any] | None = None
            if directional:
                raw_direction=[float(value) for value in step["direction"]]
                length=math.sqrt(sum(value*value for value in raw_direction))
                if not math.isfinite(length) or length <= 1e-12:
                    raise RuntimeError("direction must be a finite nonzero vector")
                native_direction=[value/length for value in raw_direction]
                directional_velocity,isotropic_velocity=signed_directional_rates(step)
                mask_names=protected_material_names(project,step)
                mask_materials=[material_handle(ps,name) for name in mask_names]
                visibility=bool(step["calculateVisibility"]) if float(step["directionalRateMagnitude"])>0 else False
                # ViennaPS 4.6.2 accepts a list under the positional argument
                # named ``maskMaterial``; ``maskMaterials=`` is not a bound
                # keyword, even though a sequence is accepted by the overload.
                model=ps.DirectionalProcess(native_direction,directional_velocity,isotropic_velocity,mask_materials,visibility)
                directional_parameters={
                    "requestedDirection":raw_direction,
                    "nativeUnitDirection":native_direction,
                    "directionCoordinateMap":"2D [x,h,0] -> native [x,h,0]; 3D [x,y,z] -> native [x,y,z] (z vertical)",
                    "directionalRateMagnitude":float(step["directionalRateMagnitude"]),
                    "isotropicRateMagnitude":float(step["isotropicRateMagnitude"]),
                    "nativeDirectionalVelocity":directional_velocity,
                    "nativeIsotropicVelocity":isotropic_velocity,
                    "calculateVisibility":visibility if float(step["directionalRateMagnitude"])>0 else "not-applicable: directional rate is zero",
                    "maskMaterials":mask_names,
                    "maskMaterialIds":[MATERIAL_IDS[name] for name in mask_names],
                    "nativeProcessBackend":"CPU_DISK pinned; directional model has no stochastic flux engine",
                    "raysPerPoint":"not-applicable",
                    "randomSeed":"not-applicable",
                }
            elif step.get("model") != "multiParticle" and not specialized:
                rate, signed_material_rates = signed_rates(step)
            particle = uses_flux(step)
            configured_engine = config.get("stepEngines", {}).get(step["id"])
            if configured_engine is None and particle:
                configured_engine = "GPU_DISK" if engine_preference == "gpu" else "CPU_DISK"
            gpu_multi_ion = (project["schemaVersion"] == 7 and step.get("model") == "multiParticle"
                             and configured_engine == "GPU_DISK"
                             and sum(row.get("kind") == "ion" for row in step.get("species", [])) > 1)
            multi_strategy = config.get("stepExecutionStrategies", {}).get(step["id"])
            if gpu_multi_ion and multi_strategy not in {GPU_MULTIPARTICLE_SPLIT_STRATEGY, GPU_MULTIPARTICLE_NATIVE_STRATEGY}:
                raise RuntimeError("GPU MultiParticle input did not pin its multi-ion execution strategy")
            if multi_strategy == GPU_MULTIPARTICLE_NATIVE_STRATEGY and not native_engine_fingerprint:
                raise RuntimeError("native multi-ion GPU execution requires a pinned engine build fingerprint")
            gpu_multi_split = gpu_multi_ion and multi_strategy == GPU_MULTIPARTICLE_SPLIT_STRATEGY
            if directional:
                pass
            elif specialized:
                model, special_process_parameters, specialized_actual, process_duration = build_specialized_model(
                    ps, domain, project, step, lambda name: material_handle(ps, name), MATERIAL_IDS)
            elif step.get("model") == "multiParticle":
                if gpu_multi_split:
                    model = None
                    multi_actual = multi_particle_step_summary(step)
                else:
                    model,multi_actual,callback_state=build_multi_particle_model(ps,step)
            elif particle:
                if material_mode:
                    model=ps.SingleParticleProcess(rate=rate,stickingProbability=float(step["stickingProbability"]),sourceExponent=float(step["sourceExponent"]),maskMaterials=[])
                    for name, material_rate in signed_material_rates.items():
                        model.setMaterialRate(material_handle(ps,name),material_rate)
                elif project["schemaVersion"]>=3:
                    model=ps.SingleParticleProcess(rate=rate,stickingProbability=float(step["stickingProbability"]),sourceExponent=float(step["sourceExponent"]),maskMaterials=[material_handle(ps,name) for name in protected_material_names(project,step)])
                else:
                    model = ps.SingleParticleProcess(rate=rate,stickingProbability=float(step["stickingProbability"]),sourceExponent=float(step["sourceExponent"]),maskMaterial=ps.Material.Mask)
            else:
                if material_mode:
                    model=ps.IsotropicProcess(rate=rate,maskMaterials=[])
                    for name, material_rate in signed_material_rates.items():
                        model.setMaterialRate(material_handle(ps,name),material_rate)
                elif project["schemaVersion"]>=3:
                    model=ps.IsotropicProcess(rate=rate,maskMaterials=[material_handle(ps,name) for name in protected_material_names(project,step)])
                else:
                    model = ps.IsotropicProcess(rate=rate, maskMaterial=ps.Material.Mask)
            process = (None if gpu_multi_split else
                       ps.Process(domain, model, process_duration if specialized else float(step["duration"])))
            # The 4.6.2 Process default attempts CUDA initialization even for
            # isotropic models. Pin a concrete, verified backend before apply.
            engine = "not-applicable"
            step_seed = None
            selection_reason = ("directional model does not use a stochastic ray flux engine" if directional else
                                "native model does not use a stochastic ray flux engine" if specialized and not particle else
                                "isotropic model does not use a ray flux engine")
            if particle:
                engine = config.get("stepEngines",{}).get(step["id"])
                if engine is None:
                    engine = "GPU_DISK" if engine_preference == "gpu" else "CPU_DISK"
                if engine not in {"CPU_DISK","GPU_DISK","CPU_TRIANGLE","GPU_TRIANGLE"}:
                    raise RuntimeError("no verified concrete flux engine for step")
                if process is not None:
                    process.setFluxEngineType(getattr(ps.FluxEngineType,engine))
                selection_reason = "pinned per-step verified engine; no fallback"
                if run_seed is None:
                    raise RuntimeError("a nonzero single-particle step requires an explicit run seed")
                step_seed = run_seed
            else:
                # Isotropic Process never consumes a ray flux engine.
                process.setFluxEngineType(ps.FluxEngineType.CPU_DISK)
            advection, advection_actual = configure_advection(ps, project)
            if specialized and step["nativeModel"] == "oxideRegrowth":
                advection.ignoreVoids = True
                advection_actual["ignoreVoids"] = True
            if process is not None:
                process.setParameters(advection)
            if specialized:
                for process_parameters in special_process_parameters:
                    process.setParameters(process_parameters)
            actual_parameters: dict[str, Any] = {**advection_actual, "duration": process_duration if specialized else float(step["duration"]), "rate": rate}
            if project["schemaVersion"] in (4,5,6,7) and not directional and not specialized and step.get("model") != "multiParticle":
                actual_parameters.update({"rateMode":step["rateMode"],"actualDefaultRate":rate,
                    "actualMaterialRatesByName":signed_material_rates,
                    "actualMaterialRatesById":{str(MATERIAL_IDS[name]):value for name,value in signed_material_rates.items()},
                    "nativeRateTable":[{"name":name,"id":MATERIAL_IDS[name],"rate":value} for name,value in sorted(signed_material_rates.items())]})
            if particle:
                ray_parameters, ray_actual = configure_rays(ps, project, int(step_seed))
                if process is not None:
                    process.setParameters(ray_parameters)
                actual_parameters.update(ray_actual)
                if step["model"] == "singleParticle":
                    actual_parameters.update({"stickingProbability": float(step["stickingProbability"]),
                                              "sourceExponent": float(step["sourceExponent"])})
                elif step["model"] == "multiParticle":
                    actual_parameters.update({"multiParticle":multi_actual,
                                              "cpuThreads":int(project["solver"]["cpuThreads"])})
                    if gpu_multi_split:
                        actual_parameters["multiParticleExecution"] = {
                            "strategy": GPU_MULTIPARTICLE_SPLIT_STRATEGY,
                            "sweeps": GPU_MULTIPARTICLE_SPLIT_SWEEPS,
                            "status": "scheduled; per-stage execution evidence follows after apply",
                        }
                    elif gpu_multi_ion:
                        actual_parameters["multiParticleExecution"] = {
                            "strategy": GPU_MULTIPARTICLE_NATIVE_STRATEGY,
                            "nativeProcessCount": 1,
                            "nativeEngineFingerprint": native_engine_fingerprint,
                        }
                elif specialized:
                    actual_parameters.update({"nativeModel": specialized_actual,
                                              "cpuThreads":int(project["solver"]["cpuThreads"])})
            if directional:
                actual_parameters = {"timeStepRatio":float(advection.timeStepRatio),"duration":float(step["duration"]),**(directional_parameters or {})}
                rate = directional_parameters["nativeDirectionalVelocity"] if directional_parameters else None
            elif specialized:
                if specialized_actual is not None:
                    specialized_actual.update({"actualSeed": step_seed, "actualEngine": engine,
                                               "cpuThreads": int(project["solver"]["cpuThreads"]),
                                               "nativeProcessEngine": engine})
                actual_parameters.update({"nativeModel": specialized_actual,
                                          "processDurationSeconds": process_duration})
            event(run_dir, {"type": "step-configured", "stepId": step["id"], "operation": step["operation"], "model": step["model"],
                            "noOp": False, "actualEngine": engine, "engineSelectionReason": selection_reason,
                            "requestedEnginePreference": engine_preference, "actualSeed": step_seed, "actualRate": rate,
                            "duration": process_duration if specialized else step["duration"], "actualParameters": actual_parameters})
            intermediate_dir = run_dir / "intermediate" / step["id"]
            intermediate_dir.mkdir(parents=True, exist_ok=True)
            split_result = None
            if gpu_multi_split:
                if selected_gpu_device is None:
                    raise RuntimeError("GPU MultiParticle splitting requires the selected CUDA device UUID")
                split_result = apply_gpu_multi_particle_split(
                    ps, domain, project, step, seed=int(step_seed), run_dir=run_dir,
                    intermediate_dir=intermediate_dir, selected_gpu_device=selected_gpu_device)
                process_apply_seconds = float(split_result["elapsedSeconds"])
                callback_state = split_result["callbackState"]
                actual_parameters["multiParticleExecution"] = split_result["execution"]
                event(run_dir, {"type": "multi-particle-gpu-split", "stepId": step["id"],
                                "execution": split_result["execution"]})
            else:
                if process is None:
                    raise RuntimeError("ViennaPS Process was not created for this process step")
                previous_cwd = Path.cwd()
                try:
                    # ViennaLS 5.8.5 writes velocity VTPs relative to the process
                    # working directory even when ViennaPS setIntermediateOutputPath
                    # is set. Isolate each step inside its run artifact directory.
                    os.chdir(intermediate_dir)
                    process.setIntermediateOutputPath(str(intermediate_dir))
                    process_started = time.perf_counter()
                    process.apply()
                    process_apply_seconds = time.perf_counter() - process_started
                finally:
                    os.chdir(previous_cwd)
            next_gpu_step_pending = any(
                uses_flux(future)
                and str(config.get("stepEngines", {}).get(
                    future["id"], "GPU_DISK" if engine_preference == "gpu" else "CPU_DISK")).startswith("GPU_")
                for future in actual_steps[index:]
            )
            if engine.startswith("GPU_"):
                if GPU_CONTEXT_REUSE and next_gpu_step_pending:
                    # Must not touch the CUDA Runtime here: it invalidates the
                    # live OptiX context and the next accel build fails (7001).
                    post_apply_cuda_readback = {
                        "readbackStatus": "skipped-for-context-reuse",
                        "reason": "in-process CUDA Runtime calls between GPU steps break the next optixAccelBuild",
                        "deviceEvidence": "nvidia-smi utilisation sample + pre-run CUDA Runtime readback",
                    }
                else:
                    post_apply_cuda_readback = cuda_runtime_readback(selected_gpu_device)
                gpu_usage = nvidia_process_readback(selected_gpu_device["uuid"])
                if gpu_sample is None:
                    gpu_sample = nvidia_gpu_sample()
                step_cuda_readback = {**cuda_readback, "afterProcessApply": post_apply_cuda_readback,
                                      "process": gpu_usage, "gpuSample": gpu_sample,
                                      **({"stageSelection": split_result["gpuStageSelection"]}
                                         if split_result is not None else {})}
                actual_parameters["cudaDeviceReadback"] = step_cuda_readback
                actual_parameters["processApplySeconds"] = process_apply_seconds
                event(run_dir, {"type": "step-device-readback", "stepId": step["id"],
                                "cudaDeviceReadback": step_cuda_readback,
                                "processApplySeconds": process_apply_seconds})
            else:
                actual_parameters["processApplySeconds"] = process_apply_seconds
                event(run_dir, {"type": "step-performance", "stepId": step["id"],
                                "processApplySeconds": process_apply_seconds})
            if callback_state is not None:
                if callback_state["count"] <= 0:
                    raise RuntimeError("ViennaPS did not invoke the configured MultiParticleProcess rate function")
                callback_counts_by_species = callback_state.get("callbackCountBySpecies")
                if callback_counts_by_species:
                    mean_flux_by_species = [
                        float(total) / max(int(callback_counts_by_species[str(species["label"])]), 1)
                        for total, species in zip(callback_state["fluxSums"], step["species"])
                    ]
                else:
                    mean_flux_by_species = [
                        float(total) / callback_state["count"] for total in callback_state["fluxSums"]
                    ]
                event(run_dir,{"type":"multi-particle-rate-callback","stepId":step["id"],
                               "invocationCount":callback_state["count"],
                               "materials":callback_state["materials"],
                               "samples":callback_state["samples"],
                               "fluxSums":callback_state["fluxSums"],
                               "fluxMaxima":callback_state["fluxMaxima"],
                               "meanFluxBySpecies":mean_flux_by_species,
                               **({"callbackCountBySpecies": callback_counts_by_species}
                                  if callback_counts_by_species else {})})
            for output_path in sorted(intermediate_dir.iterdir()):
                if not output_path.is_file():
                    continue
                if output_path.suffix.lower() == ".vtp":
                    output = describe_intermediate_vtp(output_path, step_id=step["id"], run_id=run_id,
                                                       run_dir=run_dir, project=project)
                else:
                    relative = output_path.resolve().relative_to(run_dir.resolve())
                    output = {"stepId": step["id"], "fileName": relative.as_posix(),
                              "url": f"/api/runs/{run_id}/intermediate-outputs/{step['id']}/{output_path.name}",
                              "format": output_path.suffix.lstrip(".").upper() or "binary",
                              "pointCount": None, "cellCounts": {}, "coordinateUnit": None,
                              "axisMap": None, "fields": [], "sizeBytes": output_path.stat().st_size,
                              "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest()}
                intermediate_outputs.append(output)
                event(run_dir, {"type": "intermediate-output", "output": output})
            native_specialized_outputs: list[dict[str, Any]] = []
            if specialized and step["nativeModel"] == "oxideRegrowth":
                cell_set = domain.getCellSet()
                if cell_set is None:
                    raise RuntimeError("ViennaPS OxideRegrowth lost its generated DenseCellSet")
                cell_set_payload = {
                    "schemaVersion": 1,
                    "source": "viennaps.Domain.getCellSet after OxideRegrowth.Process.apply",
                    "nativeType": "DenseCellSet",
                    "dimension": int(project["dimension"]),
                    "gridDelta": float(domain.getGridDelta()),
                    "association": "DenseCellSet scalar grid; separate from the returned surface mesh",
                    "derivedSetup": specialized_actual["derivedCellSet"],
                    "numberOfCells": int(cell_set.getNumberOfCells()),
                    "fields": {str(label): [float(value) for value in cell_set.getScalarData(str(label))]
                               for label in cell_set.getScalarDataLabels()},
                    "nativeUnit": None,
                }
                artifact_path = run_dir / "artifacts" / f"{step['id']}-dense-cell-set.json"
                artifact_path.parent.mkdir(parents=True, exist_ok=True)
                atomic_json(artifact_path, cell_set_payload)
                native_specialized_outputs.append(output_artifact(
                    artifact_path, run_dir, run_id, step["id"], "nativeCellSet", "JSON",
                    association="native DenseCellSet scalar grid; not projected onto surface mesh", nativeUnit=None))
            with phase(f"stepSnapshot:{step['id']}"):
                metadata = save_snapshot(ps, domain, run_dir, run_id=run_id, step_id=step["id"], step_name=step["name"], step_index=index, requested=project,
                                         extra_outputs=native_specialized_outputs or None,
                                         defer_payload=LAZY_SNAPSHOTS)
            with phase(f"stepNativeState:{step['id']}"):
                metadata["nativeState"] = save_native_state(ps, domain, run_dir, project=project, run_id=run_id,
                                                            step_id=step["id"], viennaps_version=ps_version, viennals_version=ls_version)
            execution_record = {"stepId": step["id"], "engine": engine, "seed": step_seed,
                                **({"gpuDeviceId": selected_gpu_device["uuid"]} if engine.startswith("GPU_") else {})}
            if split_result is not None:
                execution_record.update({"executionStrategy": split_result["execution"]["strategy"],
                                         "stageSeeds": split_result["execution"]["stageSeeds"]})
            elif gpu_multi_ion:
                execution_record["executionStrategy"] = GPU_MULTIPARTICLE_NATIVE_STRATEGY
            actual_execution.append(execution_record)
        snapshots.append(metadata)
        event(run_dir, {"type": "snapshot", **metadata})
        future_gpu_process = any(
            uses_flux(future)
            and str(config.get("stepEngines", {}).get(
                future["id"], "GPU_DISK" if engine_preference == "gpu" else "CPU_DISK")).startswith("GPU_")
            for future in actual_steps[index:]
        )
        if actual_execution[-1]["engine"].startswith("GPU_") and future_gpu_process:
            # ViennaRay 0fe9 reuses a registered OptiX context, but its second
            # disk-geometry build was reported to fail after the Domain has
            # changed. Dropping the completed native objects is always safe;
            # tearing down the global context costs a fresh CUDA/OptiX context
            # plus kernel-module/JIT setup for the next GPU step (0.3-0.9 s).
            # worker/profile_segments.py --mode reuse reproduces two consecutive
            # GPU steps in one process; with the installed engine build the
            # second build succeeds, so context reuse can be requested
            # explicitly. The conservative reset stays the default until reuse
            # is verified for the full per-run engine matrix.
            process = None
            model = None
            import gc
            gc.collect()
            if GPU_CONTEXT_REUSE:
                event(run_dir, {"type": "gpu-context-reuse", "afterStepId": step["id"],
                                "nextGpuStepPending": True,
                                "registeredDevices": [int(value) for value in ps.gpu.Context.getRegisteredDeviceIDs()]})
            else:
                reset = reset_native_gpu_context(ps, selected_gpu_device)
                event(run_dir, {"type": "gpu-context-reset", "afterStepId": step["id"],
                                "nextGpuStepPending": True, "context": reset})

    worker_events=read_worker_events(run_dir)
    step_records=[record for record in worker_events if record.get("type")=="step-configured"]
    step_records_by_id={record.get("stepId"):record for record in step_records}
    for device_record in (record for record in worker_events if record.get("type") == "step-device-readback"):
        configured = step_records_by_id.get(device_record.get("stepId"))
        if configured is not None:
            configured.setdefault("actualParameters", {}).update({
                key: value for key, value in device_record.items()
                if key not in {"type", "stepId"}
            })
    for performance_record in (record for record in worker_events if record.get("type") == "step-performance"):
        configured = step_records_by_id.get(performance_record.get("stepId"))
        if configured is not None:
            configured.setdefault("actualParameters", {})["processApplySeconds"] = performance_record["processApplySeconds"]
    for callback_record in (record for record in worker_events if record.get("type")=="multi-particle-rate-callback"):
        configured=step_records_by_id.get(callback_record.get("stepId"))
        if configured is not None:
            configured.setdefault("actualParameters",{})["rateCallbackSummary"]={
                "invocationCount":callback_record.get("invocationCount"),
                "materials":callback_record.get("materials"),
                "samples":callback_record.get("samples"),
                "fluxSums":callback_record.get("fluxSums"),
                "fluxMaxima":callback_record.get("fluxMaxima"),
                "meanFluxBySpecies":callback_record.get("meanFluxBySpecies"),
                **({"callbackCountBySpecies": callback_record["callbackCountBySpecies"]}
                   if callback_record.get("callbackCountBySpecies") else {}),
            }
    for split_record in (record for record in worker_events if record.get("type") == "multi-particle-gpu-split"):
        configured = step_records_by_id.get(split_record.get("stepId"))
        if configured is not None:
            configured.setdefault("actualParameters", {})["multiParticleExecution"] = split_record["execution"]
    worker_result = {
        "schemaVersion": 1,
        "runId": run_id,
        "kind": kind,
        "versions": {"viennaps": ps_version, "viennals": ls_version},
        **({"nativeEngineFingerprint": native_engine_fingerprint} if native_engine_fingerprint else {}),
        "actualSeed": run_seed,
        "cpuThreads": int(project["solver"]["cpuThreads"]),
        "selectedGpuDevice": selected_gpu_device,
        "cudaDeviceReadback": cuda_readback,
        "actualSteps": actual_execution,
        "stepRecords": step_records,
        "fluxResult": flux_result,
        "sliceResult": slice_result,
        "intermediateOutputs": intermediate_outputs,
        "snapshots": snapshots,
        "gridDeltaReadback": readback_delta,
        "xExtentRequested": float(project["domain"]["xExtent"]),
        "boundaryValuesReadback": boundary_values,
        # Domain.getBoundingBox() calls ToDiskMesh(), whose ViennaLS 5.8.5
        # expansion mutates the source level set in candidate builds. The
        # saved snapshot already contains safe surface points; derive bounds
        # from those points instead of invoking the mutating native getter.
        "boundingBoxReadback": snapshots[-1].get("surfaceBounds") if snapshots else None,
        "boundingBoxReadbackSource": "saved snapshot mesh points; mutating native getter omitted",
        "initialGeometryReadback": geometry_readback,
    }
    with phase("workerResultWrite"):
        atomic_json(run_dir / "worker-result.json", worker_result)
    if PROFILE_ENABLED:
        event(run_dir, {"type": "segment-timing", "phases": PROFILE_PHASES,
                        "note": "worker-side phases only; process.apply() is timed separately"})
    event(run_dir, {"type": "worker-complete", "snapshotCount": len(snapshots)})


def read_worker_events(run_dir: Path) -> list[dict[str, Any]]:
    try:
        return [json.loads(line) for line in (run_dir / "events.ndjson").read_text(encoding="utf-8").splitlines() if line]
    except (OSError, ValueError):
        return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    try:
        execute(args.input, args.run_dir)
        print("WORKER_COMPLETE", flush=True)
        return 0
    except BaseException as exc:
        try:
            event(args.run_dir.resolve(), {"type": "worker-failed", "error": f"{type(exc).__name__}: {exc}"})
        except BaseException:
            pass
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
