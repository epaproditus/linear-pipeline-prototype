#!/usr/bin/env bash
cd "$(dirname "$0")"
unset LINEAR_API_KEY
export $(grep -v '^#' .env | xargs)
export PYTHONPATH=..
. .venv/bin/activate
exec uvicorn linear_executor:app --host 0.0.0.0 --port 8664 --log-level info
