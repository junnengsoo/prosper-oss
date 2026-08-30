#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_VERSION="${PYTHON_VERSION:-3.11}"

cd "$ROOT_DIR"

require_command() {
  local command_name="$1"
  local install_hint="$2"

  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Missing required command: $command_name" >&2
    echo "$install_hint" >&2
    exit 1
  fi
}

require_command "uv" "Install uv from https://docs.astral.sh/uv/getting-started/installation/"
require_command "node" "Install Node.js 22 or newer."
require_command "npm" "Install npm with Node.js 22 or newer."

node_major="$(node -p 'Number(process.versions.node.split(".")[0])')"
if (( node_major < 22 )); then
  echo "Node.js 22 or newer is required. Current version: $(node --version)" >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example"
else
  echo "Keeping existing .env"
fi

echo "Installing backend dependencies"
uv sync --locked --extra dev --python "$PYTHON_VERSION"

echo "Installing frontend dependencies"
(
  cd frontend
  npm ci
)

echo "Installing bridge dependencies"
(
  cd bridge
  npm ci
)

echo "Initializing local database"
.venv/bin/python -m app.cli init-db

echo "Setup complete. Start the app with: scripts/dev.sh"
