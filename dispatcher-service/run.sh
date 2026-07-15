#!/usr/bin/env bash
cd "$(dirname "$0")"
export PYTHONPATH=..
. .venv/bin/activate
exec uvicorn app:app --host 0.0.0.0 --port 8666 --log-level info
