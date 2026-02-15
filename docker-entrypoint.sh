#!/usr/bin/env bash
set -euo pipefail

# Load .env if present
if [ -f /app/.env ]; then
  # export variables from .env into environment
  set -o allexport
  # shellcheck disable=SC1091
  . /app/.env
  set +o allexport
fi

# ensure necessary folders exist
mkdir -p /app/temp /app/clips /app/reels /app/logs

# run manager (it uses APScheduler and runs indefinitely)
exec python /app/run_manager.py
