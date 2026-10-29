#!/usr/bin/env bash
set -euo pipefail
workbench_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$workbench_root"
./scripts/status.sh
"$workbench_root/.venv/bin/python" scripts/probe-host-gpu.py --host-execution-confirmed
node frontend/tests/browser-3d.mjs
