#!/usr/bin/env bash
# Local style/contract gate for the modularized surface.
# Deliberately narrow: only the new/modernized modules are linted, legacy files
# are excluded in pyproject.toml, and the full Python suite is left to the
# isolated-data-dir task.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

python_bin="${PYTHON:-.venv/bin/python}"

echo "== compileall =="
"$python_bin" -m compileall -q backend worker scripts

echo "== ruff =="
ruff_cmd=()
if command -v ruff >/dev/null 2>&1; then
  ruff_cmd=(ruff)
elif "$python_bin" -m ruff --version >/dev/null 2>&1; then
  ruff_cmd=("$python_bin" -m ruff)
fi
if [ ${#ruff_cmd[@]} -gt 0 ]; then
  targets=(backend/api backend/api_errors.py backend/packages.py backend/planning.py
           backend/supervisor.py worker/artifacts.py worker/engine_policy.py worker/steps)
  "${ruff_cmd[@]}" check "${targets[@]}"
  "${ruff_cmd[@]}" format --check "${targets[@]}"
else
  echo "ruff not found; skipping ruff check/format (install ruff to enable)"
fi

echo "== frontend typecheck =="
(cd frontend && npm run typecheck)

echo "== git diff --check =="
git diff --check

echo "style gate passed"
