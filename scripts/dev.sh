#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -x "$ROOT_DIR/.venv/bin/uvicorn" ]]; then
  echo "Missing Python environment. Run the README setup commands first." >&2
  exit 1
fi

if [[ ! -d "$ROOT_DIR/frontend/node_modules" ]]; then
  echo "Missing frontend dependencies. Run: cd frontend && npm install" >&2
  exit 1
fi

if [[ ! -d "$ROOT_DIR/bridge/node_modules" ]]; then
  echo "Missing bridge dependencies. Run: cd bridge && npm install" >&2
  exit 1
fi

cleanup() {
  trap - INT TERM EXIT
  for pid in "${PIDS[@]:-}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
}

trap cleanup INT TERM EXIT

PIDS=()

echo "Starting backend on http://127.0.0.1:8000"
(
  cd "$ROOT_DIR"
  .venv/bin/uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000 --log-config "$ROOT_DIR/scripts/uvicorn_log_config.json"
) &
PIDS+=("$!")

echo "Starting dashboard on http://127.0.0.1:5173"
(
  cd "$ROOT_DIR/frontend"
  npm run dev
) &
PIDS+=("$!")

echo "Starting Baileys bridge on http://127.0.0.1:8788"
(
  cd "$ROOT_DIR/bridge"
  npm run dev
) &
PIDS+=("$!")

echo "All services started. Press Ctrl+C to stop."
wait
