#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from template. Edit it with real values, then rerun."
  exit 1
fi

uvicorn linear_critic:app --host 0.0.0.0 --port 8665 --log-level info
