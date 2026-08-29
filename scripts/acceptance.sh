#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_VERSION="${PYTHON_VERSION:-3.11}"
INSTALL_DEPS="${INSTALL_DEPS:-1}"
INSTALL_PLAYWRIGHT="${INSTALL_PLAYWRIGHT:-1}"

cd "$ROOT_DIR"

if [[ "$INSTALL_DEPS" == "1" ]]; then
  uv sync --locked --extra dev --python "$PYTHON_VERSION"
  (
    cd frontend
    npm ci
  )
fi

if [[ "$INSTALL_PLAYWRIGHT" == "1" ]]; then
  (
    cd frontend
    npx playwright install chromium
  )
fi

(
  cd frontend
  npm run test:acceptance
)
