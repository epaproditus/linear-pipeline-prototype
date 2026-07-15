#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
export PYTHONPATH="../lib"
uvicorn linear_router:app --host 0.0.0.0 --port 8670 --log-level info
