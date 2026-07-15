#!/usr/bin/env bash
cd "$(dirname "$0")"
export PYTHONPATH=..
. .venv/bin/activate
exec uvicorn linear_planner:app --host 0.0.0.0 --port 8663 --log-level info
