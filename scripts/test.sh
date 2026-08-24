#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT_DIR"

PYTHON_VERSION="${PYTHON_VERSION:-3.11}"

echo "Installing backend dependencies"
uv sync --locked --extra dev --python "$PYTHON_VERSION"

echo "Running backend tests"
.venv/bin/python -m pytest backend/tests

echo "Compiling backend"
.venv/bin/python -m compileall backend/app

echo "Compiling Python scripts"
.venv/bin/python -m compileall scripts

echo "Checking shell scripts"
for script in scripts/*.sh; do
  bash -n "$script"
done

echo "Checking legacy template/draft surfaces"
scripts/legacy_surface_check.py

echo "Building frontend"
(
  cd frontend
  npm ci
  npm run build
)

echo "Testing frontend"
(
  cd frontend
  npm run test
)

echo "Typechecking bridge"
(
  cd bridge
  npm ci
  npm run typecheck
)

echo "Testing bridge"
(
  cd bridge
  npm run test
)

echo "All checks passed."
