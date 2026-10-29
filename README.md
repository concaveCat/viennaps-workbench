# ViennaPS Workbench

A self-hosted web workbench for [ViennaPS](https://github.com/ViennaTools/ViennaPS)
process simulation. Build 2D and 3D structures, compose multi-step etch/deposit
recipes, run them on CPU or an NVIDIA GPU, inspect snapshots and particle fluxes
in a 3D viewer, and export runs that can be replayed exactly.

Single-user and loopback-only by design: the service binds `127.0.0.1`, keeps all
data on the local disk, and never calls an external API. Verified against
**ViennaPS 4.6.2 / ViennaLS 5.8.5** on Linux.

[中文说明](README.zh-CN.md)

## Contents

- [Features](#features)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Using the workbench](#using-the-workbench)
- [HTTP API](#http-api)
- [Configuration](#configuration)
- [Resource limits and safety](#resource-limits-and-safety)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Repository layout](#repository-layout)
- [Documentation](#documentation)
- [Known limitations](#known-limitations)
- [License](#license)

## Features

**Structure editor**

- 2D: plane and trench, with taper angles, mask height and mask taper.
- 3D: plane, trench and hole structures, fin extrusion, stacked films with a
  top-level opening, and GDS layout import (layer-to-material mapping, periodic
  repair).
- 13 built-in materials (`Si`, `SiO2`, `Si3N4`, `PolySi`, `SiGe`, `Al2O3`, `HfO2`,
  `TiN`, `W`, `Cu`, `PMMA`, `Mask`, `SiN`) plus user-defined materials, each with
  an independent film/mask role.
- 2D/3D preview before a run, including material legends.

**Recipes**

- Isotropic etch and deposition.
- Ray-traced single-particle etch/deposition (`singleParticle`), with CPU or GPU
  flux engines.
- Native specialized models: SF6/O2, CF4/O2, HBr/O2, SF6/C4F8, fluorocarbon,
  ion-beam etching, wet etch, TEOS deposition, TEOS PECVD, ALD, oxidation, oxide
  regrowth, selective epitaxy, neutral transport, and multi-particle.
- Directional rate model and selectivity comparisons.
- Per-step enable/disable, reordering, and per-step native parameters.

**Execution**

- One active run at a time, executed in a supervised child process with a fixed
  seed, wall-clock timeout, RSS watch and cancellation.
- Engine preference `auto` / `cpu` / `gpu`; explicit GPU never falls back to CPU
  silently. GPU devices can be pinned by stable UUID.
- Live status, per-step engine/seed records, and reproducible input hashes.

**Results**

- Snapshot viewer with rotation, clipping planes and orthographic sections.
- Surface flux maps, intermediate native outputs, CSV/PNG export.
- Artifact ZIP download per step.
- Replay-project export/import, continuation packages, and project save/open.
- Run history that survives restarts.

## Architecture

```
Browser  (React 19 + vtk.js, built by Vite)
   │  HTTP/JSON on 127.0.0.1 only
   ▼
FastAPI service  backend/main.py
   │  in-process supervisor, one active run, identity-checked lifecycle
   ▼
worker subprocess  worker/simulate.py
   │  imports viennaps / viennals from system site-packages
   ▼
var/runs/<run-id>/   input.json, manifest.json, snapshots/*.vtk, flux/*.csv
```

The web process only imports package metadata and `vtk.js` files; the native
ViennaPS/CUDA libraries are loaded exclusively inside the worker child. Run
outputs are plain files, so a run can be inspected or replayed without the
original process.

| Path | Role |
| --- | --- |
| `backend/` | FastAPI app, validation, storage, supervisor, native bindings |
| `worker/` | Simulation entry point and native `viennaps` probes |
| `frontend/` | React + TypeScript UI and vtk.js viewer |
| `scripts/` | Start/stop, build, host probes, promotion and acceptance helpers |
| `tests/` | Python unit and API integration tests |
| `docs/` | Contracts, schemas, acceptance reports, capability matrices |
| `var/` | Runtime data: projects, runs, domain states, GDS assets (git-ignored) |

## Requirements

| Component | Requirement |
| --- | --- |
| OS | Linux (uses `/proc` for process supervision and CPU affinity) |
| Python | 3.12 |
| ViennaPS | 4.6.2, installed system-wide; a CUDA build is required for GPU runs |
| ViennaLS | 5.8.5 |
| Node.js | 20.19+ or 22.12+ (Vite 8) |
| GPU | Optional. NVIDIA GPU with CUDA for `GPU_DISK` |

ViennaPS and ViennaLS are deliberately **not** application dependencies: the
workbench must use the engine build that is already installed on the host, and
must never upgrade or replace it. This is why the virtual environment is created
with `--system-site-packages`.

## Installation

```bash
git clone https://github.com/concaveCat/viennaps-workbench.git
cd viennaps-workbench

# 1. Python environment that can see the system ViennaPS/ViennaLS.
python3.12 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -r requirements.lock

# 2. Frontend bundle (the start script refuses to run without it).
cd frontend
npm ci
npm run build
cd ..
```

Check that the engines are visible to the venv:

```bash
.venv/bin/python -c "import viennaps, viennals; print(viennaps.__version__, viennals.__version__)"
# expected: 4.6.2 5.8.5
```

If the import fails, install ViennaPS/ViennaLS first (including their Python
bindings) and re-create `.venv` with `--system-site-packages`. The workbench
does not build or install the engines.

## Quick start

```bash
./scripts/start.sh     # starts the service in the background
./scripts/status.sh    # prints the URL and health state
./scripts/stop.sh      # stops only the process it started
```

Then open the printed address, for example <http://127.0.0.1:8789/>.

The port is chosen in this order:

1. `WORKBENCH_PORT` environment variable,
2. `var/listen-port.json` (written when the service is moved between ports),
3. built-in default `8767`.

The service always binds `127.0.0.1`; requests whose `Host` header does not match
the active loopback address and port are rejected. Logs go to `var/server.log`.

## Using the workbench

A minimal first run:

1. **Structure** — keep the default Si trench, or pick plane/trench (2D) or
   hole/fin/stack/GDS (3D), set the grid spacing (`gridDelta`) and materials,
   then press **Preview**.
2. **Recipe** — add a step, choose `isotropic`, operation `etch`, rate
   `-0.5 µm/s`, duration `0.4 s`. Add more steps to build a sequence; disabled
   steps stay in the project but are not executed.
3. **Solver** — set the time-step ratio (default `0.25`), rays per point for
   particle models, CPU threads, random seed mode, engine preference and timeout.
4. **Run** — submit and watch the status. Only one run executes at a time; a
   second submission is rejected until the first finishes or is cancelled.
5. **Inspect** — open the latest snapshot, rotate/clip/section the structure,
   switch materials, and inspect flux or intermediate outputs for particle steps.
6. **Save / export** — save the project, download the run ZIP, or export a
   replay project and import it later to reproduce the same engine and seed.

The API can also be driven directly:

```bash
curl -s http://127.0.0.1:8789/api/health
curl -s http://127.0.0.1:8789/api/projects
```

## HTTP API

All endpoints are relative to the loopback base URL and exchange JSON. Errors use
`{"detail": ...}` with `4xx` status codes. The interactive OpenAPI routes are
disabled on purpose; `docs/api-contract.md` is the normative contract.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Service, Python and engine versions, active run |
| `GET` | `/api/capabilities` | Verified capabilities and resource policy |
| `GET` | `/api/gpu-devices` | Visible CUDA devices and UUIDs |
| `GET` | `/api/materials` | Built-in and custom material catalog |
| `POST` | `/api/materials` | Create a custom material |
| `PATCH`/`DELETE` | `/api/materials/{name}` | Update or delete a custom material |
| `POST` | `/api/gds-assets` | Upload a GDS layout (≤ 64 MiB) |
| `GET` | `/api/gds-assets/{id}` | Download a stored layout |
| `POST` | `/api/gds-assets/{id}/inspect` | Inspect layers and bounds |
| `POST` | `/api/projects/validate` | Validate a project without saving |
| `GET`/`POST` | `/api/projects` | List or create projects |
| `GET`/`PUT` | `/api/projects/{id}` | Read or replace a project |
| `POST` | `/api/runs` | Submit a simulation run (202) |
| `POST` | `/api/runs/flux` | Single flux calculation for one step |
| `POST` | `/api/runs/slice` | Surface slice extraction |
| `POST` | `/api/runs/replay` | Replay an exported replay project |
| `GET` | `/api/runs` | List runs |
| `GET` | `/api/runs/{id}` | Run manifest and step status |
| `POST` | `/api/runs/{id}/cancel` | Cancel a running run |
| `GET` | `/api/runs/{id}/snapshots/{step}` | Snapshot mesh as JSON |
| `GET` | `/api/runs/{id}/flux-result` | Flux values as JSON/CSV |
| `GET` | `/api/runs/{id}/intermediate-outputs/{step}/{file}` | Native intermediate files |
| `GET` | `/api/runs/{id}/artifacts/{step}` | Step artifact metadata |
| `GET` | `/api/runs/{id}/download` | Streamed ZIP of the run |
| `GET` | `/api/runs/{id}/replay-project` | Export a replay envelope |
| `GET` | `/api/runs/{id}/continuation-project/{step}` | Build a continuation project |
| `POST` | `/api/continuation-package/export` | Export a continuation package |
| `POST` | `/api/continuation-package/import` | Import a continuation package |

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `WORKBENCH_PORT` | `var/listen-port.json`, else `8767` | Loopback port to bind |
| `VIENNAPS_WORKBENCH_DATA_DIR` | `./var` | Root for projects, runs and assets |
| `VIENNAPS_PATCHED_RUNTIME_DIR` | unset | Optional patched ViennaPS runtime (set by `scripts/start.sh` when present) |
| `VIENNAPS_PATCHED_RUNTIME_HISTORY_DIR` | unset | Version archive for the patched runtime |
| `WORKBENCH_GPU_TEST` | unset | `1` enables GPU integration tests |
| `VIENNAPS_WORKBENCH_PROFILE` | on | `0` disables the per-segment timing event |
| `VIENNAPS_WORKBENCH_GPU_CONTEXT_REUSE` | on | `0` tears the CUDA/OptiX context down between consecutive GPU steps |
| `VIENNAPS_DEBUG_NATIVE_CALLBACKS` | unset | Verbose native callback logging |
| `WORKBENCH_HOST_VERIFY_SCRIPT` | unset | Optional host self-check used by `scripts/finalize-host.py` |

## Resource limits and safety

- **One active run** per service instance; a second submission returns `409`.
- Runs execute in their own session/process group and are terminated as a group.
- Wall-clock timeout: 1–600 s per project (policy default 120 s).
- RSS is sampled every 200 ms; the child is terminated above 2048 MiB. This is a
  soft watch, not a hard cgroup limit, and does not account for CUDA memory.
- Combined stdout/stderr is capped at 4 MiB per run; artifacts at 512 MiB.
- Application data is capped at 10 GiB, and a run is refused if the filesystem has
  less than 20 GiB free.
- Uploaded GDS bodies are limited to 64 MiB.
- The service never touches other processes: start/stop use a saved PID plus
  process start tick, so a recycled PID cannot be signalled.

## Testing

```bash
# Unit and API integration tests (GPU cases are skipped by default).
.venv/bin/python -m unittest discover -s tests -v

# Include the GPU integration cases on a CUDA host.
WORKBENCH_GPU_TEST=1 .venv/bin/python -m unittest discover -s tests -v

# Frontend type check and production build.
cd frontend && npm run typecheck && npm run build
```

If the workbench service is already running against `./var`, the suite aborts in
milliseconds with `another ViennaPS Workbench server already holds the
data-directory lock`. In that case point `VIENNAPS_WORKBENCH_DATA_DIR` at a fresh
directory **inside the repository** (for example
`$PWD/var/test-$(date +%s)`) and use a port the running service does not own
(`WORKBENCH_PORT=8767`). The directory has to stay inside the repository because
`scripts/bounded_child.py` rejects `--record`/`--log-path` values outside it.

`frontend/tests/*.mjs` are real-browser acceptance scripts (Playwright). They
expect a running service and a prepared data directory, and are written for the
host acceptance environment rather than a generic CI runner; use them as
references when adapting the suite.

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `frontend build is missing` on start | Run `npm ci && npm run build` in `frontend/`. |
| `cannot bind configured loopback port` | Another process holds the port. Set `WORKBENCH_PORT` or remove `var/listen-port.json`; the workbench will not kill the unknown listener. |
| `ModuleNotFoundError: viennaps` | `.venv` was created without `--system-site-packages`, or ViennaPS is not installed for this Python. |
| GPU runs rejected or `gpuAvailable()` false | Check `nvidia-smi`, the CUDA build of ViennaPS, and `GET /api/gpu-devices`. GPU support is only exposed for `GPU_DISK` combinations recorded in `docs/stage9-evidence/gpu-capability-matrix.json`. |
| `request Host must be the active loopback address` | Use `127.0.0.1:<port>` or `localhost:<port>`; reverse proxies and custom hostnames are intentionally rejected. |
| Run stops with `run artifact size exceeded` | The step produced more than 512 MiB; reduce grid resolution, rays, or output frequency. |

## Repository layout

```text
backend/        FastAPI app, models, validation, storage, supervisor
worker/         simulate.py entry point, native probes, profiling helper
frontend/       React/TypeScript UI, vtk.js viewer, Playwright acceptance scripts
scripts/        start/stop/status, frontend build, host probes, promotion
tests/          Python unittest suite, fixtures, legacy readers
docs/           contracts, schemas, capability matrices, acceptance reports
var/            runtime data (ignored by git)
```

## Documentation

- `docs/api-contract.md` — wire contract, hashes, replay envelope.
- `docs/schema-project-*.json`, `docs/schema-replay-*.json` — project and replay schemas.
- `docs/capabilities.json`, `docs/resource-policy.json` — verified native behavior and limits.
- `docs/ACCEPTANCE_STAGE*.md` — stage-by-stage acceptance reports.
- `docs/performance-profile-20261029.md` — GPU/CPU timing breakdown.
- `docs/compatibility.md` — pinned environment and native failure notes.

Large acceptance evidence (`docs/*-evidence/`) is intentionally not versioned;
the three capability matrices required at runtime are the exception.

## Known limitations

- Verified only against ViennaPS 4.6.2 / ViennaLS 5.8.5; other versions are
  rejected rather than guessed at.
- GPU support is limited to `GPU_DISK` for covered model/geometry combinations.
  `GPU_TRIANGLE` and `GPU_LINE` are not exposed.
- The installed 4.6.2 MultiParticle GPU path handles a single ion species;
  multi-ion GPU requests are reported as a native limitation, not silently
  approximated. Multi-ion CPU paths and the single-pass flux strategy remain
  available.
- Host capability evidence was collected on one NVIDIA RTX 3060 Laptop GPU;
  multi-GPU selection is implemented and unit-tested, but not proven on two
  physical cards.
- The UI is a single-user local tool: no authentication, no TLS, no remote access.
- Browser acceptance scripts assume the host toolchain and are not hermetic.

## License

No license has been chosen yet. Until one is added, the source is
all-rights-reserved by default. If you plan to reuse it, open an issue to discuss
a license (MIT or Apache-2.0 would be the natural choices for this codebase).
