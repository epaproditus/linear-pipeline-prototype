#!/usr/bin/env bash
set -euo pipefail

STAGE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$STAGE_DIR"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from template. Edit it with real values, then rerun."
  exit 1
fi

uvicorn linear_router:app --host 0.0.0.0 --port 8661 --log-level info
