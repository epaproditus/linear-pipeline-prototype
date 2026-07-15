#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
export PYTHONPATH="../lib"
uvicorn linear_planner:app --host 0.0.0.0 --port 8663 --log-level info
