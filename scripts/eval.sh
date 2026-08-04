#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT_DIR"

echo "Running live triage eval"
.venv/bin/python scripts/triage_eval.py

echo "Running live unit matching eval"
.venv/bin/python scripts/unit_matching_eval.py

echo "All live evals passed."
