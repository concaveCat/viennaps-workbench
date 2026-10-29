# P0 compatibility and resource baseline

Date: 2026-10-26

## Environment

- Python 3.12.14; installed ViennaPS 4.6.2 and ViennaLS 5.8.5. Metadata was read without importing ViennaPS.
- `/root/ViennaPS` source tree is 4.7.0 and was used only to compare documented call shape; installed-package probes were performed against 4.6.2.
- The project `.venv` was created with `--system-site-packages`. Before P0 probes, `scripts/bounded_child.py` was implemented and exercised with a success child, a one-second timeout, a 1 MiB RSS threshold, and a 1 KiB log cap. Every engine probe then ran through that guard in a new session and process group.
- RSS supervision measures `/proc` resident pages for the owned process group every 200 ms. It is not a hard memory cap. No usable `memory.max` cgroup file was available in this sandbox; RLIMIT_AS is not used.
- The workbench filesystem had about 985 GB free at P0. The app still enforces its own 10 GiB accumulation cap and 20 GiB minimum-free reserve.

## Verified installed-package behavior

- `setDimension(2)`, `setNumThreads(1)`, reflective and periodic x boundaries, 2D plane and trench creation, material IDs, process execution, and ASCII VTK surface export all worked in bounded child processes.
- Boundary enum values read back as `REFLECTIVE_BOUNDARY=0`, `PERIODIC_BOUNDARY=2`, and vertical `INFINITE_BOUNDARY=1` for the tested constructor forms.
- `Domain.getGridDelta()` returned 0.1 and 0.05 for those requests. At grid 0.1, x extent 5, trench width 2 and depth 2.2, the initial mesh covered x `[-2.5, 2.5]` and h `[-2.2, 0]`.
- Isotropic etch at rate -0.5 µm/s for 0.4 s moved a plane from h=0 to h=-0.2 µm. Isotropic deposition at +0.5 µm/s for 0.4 s created a SiO2 level set up to h=+0.2 µm.
- SingleParticleProcess with explicit `CPU_DISK`, 64/128 rays, fixed seed, and `useRandomSeeds=false` completed. `setNumThreads(1/2/4)` completed. Sticking probability endpoints 0 and 1 completed. Tested time-step ratios 0.05, 0.25 and 0.5 completed and appeared in `AdvectionParameters.toMetaData()`.
- Two SiO2 depositions followed by etch completed on the same Domain. The level-set material map read `[10, 30, 30]`; surface cell data only carried material IDs, so no layer provenance is inferred from repeated IDs.
- `saveSurfaceMesh(..., addInterfaces=True, sharpCorners=False, minNodeDistanceFactor=0.01)` wrote VTK legacy ASCII `UNSTRUCTURED_GRID` files with `MaterialIds` cell data.

## Installed-package failures and guards

- `Domain.getSurfaceMesh(addInterfaces=False)` caused a SIGSEGV in a bounded probe. It is not called by the application.
- `Domain.getSurfaceMesh(addInterfaces=True, sharpCorners=True, ...)` also caused a SIGSEGV. The application fixes `sharpCorners=False`.
- A trench with `gridDelta=0.1`, `xExtent=4`, `width=1.2`, `depth=1`, zero taper returned no positive-x Si sidewall cells. A geometry post-check must reject this incomplete mesh. The same width at x extent 5 and typical width 2 at extent 4 or 5 returned both sides.
- `getLevelSetMesh(width=1)` produced level-set grid values but no line cells; it is not a display mesh.

## GPU and host environment

- The bounded sandbox probe of `gpuAvailable()` raised `RuntimeError: Failed to load CUDA symbol via cuGetProcAddress: cuInit`. This establishes only that GPU access is blocked in the current sandbox.
- After the user authorized an out-of-sandbox check, the bounded host probe returned `gpuAvailable() = true` and found one `NVIDIA GeForce RTX 3060 Laptop GPU`. ViennaPS 4.6.2 `GPU_DISK` completed 2D single-particle etch and deposition probes at `gridDelta=0.2`, `xExtent=4`, 64 rays, one thread, and a fixed seed. Guard records show normal exit and an empty owned group; see `var/probes/gpu-engine-host.record.json` and `var/probe-tests/gpu-engine-results.json`.
- A second bounded test ran the actual API supervisor/worker path with explicit GPU preference, then exported and replayed its `GPU_DISK` replay project. GPU runs succeeded and the replay matched material IDs and geometry within `2*gridDelta`; see `var/probes/gpu-api-integration.log` and the associated run manifests. `AUTO` deliberately remains CPU_DISK. GPU_TRIANGLE and GPU_LINE were not tested and remain unavailable.
- The read-only host self-check returned a non-zero status: patch bytes and static checks passed, while host identity, license runtime and port checks were skipped because the shell is sandboxed. This is not evidence of a host-environment failure. No system service, license, network interface, route, MAC, hostname, or loopback device was changed.

## Implementation and dependency status

- `frontend/package.json` and `package-lock.json` pin React, React DOM, TypeScript, Vite, and the React Vite plugin. After the user installed the npm packages, `scripts/build-frontend.sh` completed `npm ci --offline` and the production TypeScript/Vite build; npm reported zero vulnerabilities.
- FastAPI 0.162.0, Pydantic 2.14.1, and Uvicorn 0.54.0 are present in `.venv`; `pip check` reports no broken requirements. Their transitive application dependency closure is pinned in `requirements.lock`, without ViennaPS/ViennaLS entries.
- The Python verification suite passed 19 tests, including nine in-process ASGI API tests that launch real bounded ViennaPS workers. It covers project API behavior, preview grids and geometry variants, direct-call comparisons, multi-deposit/no-op and reorder semantics, CPU_DISK and GPU_DISK replay, streamed ZIP/independent VTK reads, invalid-input paths, concurrency locks, worker failure, disk preflight, and cancellation/reaping. The complete suite ran with the host GPU test enabled and all tests passed. See `implementation-status.md` for the exact A01–A15 status.
- On the host, `scripts/start.sh`, `scripts/status.sh`, and `scripts/stop.sh` completed a start/status/stop/restart cycle using the saved PID and start-tick identity. The service is left running at `http://127.0.0.1:8766/`; the UI, built JS/CSS assets, `/api/health`, and `/api/capabilities` returned HTTP 200. See `var/probes/live-service-smoke.json`. Interactive browser acceptance is still open because no browser runtime is available in this execution session.
- The sandbox itself still cannot initialize CUDA. The authorized host probe verified only the `GPU_DISK` engine for 2D single-particle etch/deposition on the RTX 3060 Laptop GPU; other GPU engines are not exposed. `AUTO` remains on CPU_DISK, and explicit GPU preference never silently falls back.
- No system Python, ViennaPS, ViennaLS, CUDA, or operating-system package was changed.

