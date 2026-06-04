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

# Initialize persistent volumes from image copy if they are empty.
# We keep a full copy of the repo at /usr/src/app in the image; when
# named volumes are mounted into /app they will be empty on first run,
# so copy any seeded content from the image path into the mounted volume.
for d in data db clips reels temp logs; do
  target="/app/$d"
  src="/usr/src/app/$d"
  mkdir -p "$target"
  if [ -d "$src" ] && [ -z "$(ls -A "$target" 2>/dev/null)" ]; then
    echo "Initializing $target from image defaults"
    cp -a "$src/." "$target/" || true
  fi
done

# Specifically handle the database file if it exists in the root of the image copy
if [ ! -f /app/db/reels_manager.db ] && [ -f /usr/src/app/reels_manager.db ]; then
  echo "Initializing database from image default"
  cp /usr/src/app/reels_manager.db /app/db/reels_manager.db
fi

# run manager (it uses APScheduler and runs indefinitely)
exec python /app/run_manager.py --disable-interaction
