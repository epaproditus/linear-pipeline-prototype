#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
. .venv/bin/activate
export PYTHONPATH=".."
exec uvicorn linear_router:app --host 0.0.0.0 --port 8670 --log-level info
