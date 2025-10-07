#!/usr/bin/env bash
set -euo pipefail

PORT="${1:-5555}"
echo "[kill_port] Checking listeners on port $PORT ..."

if command -v lsof >/dev/null 2>&1; then
  PIDS=$(lsof -tiTCP:$PORT -sTCP:LISTEN || true)
  if [ -n "$PIDS" ]; then
    echo "[kill_port] Killing PIDs: $PIDS"
    kill -9 $PIDS || true
    echo "[kill_port] Done."
    exit 0
  fi
fi

if command -v fuser >/dev/null 2>&1; then
  echo "[kill_port] Trying fuser ..."
  # Some fuser implementations don't support -k; manually kill returned PIDs.
  FPIDS=$(fuser ${PORT}/tcp 2>/dev/null || true)
  if [ -n "$FPIDS" ]; then
    echo "[kill_port] Killing PIDs: $FPIDS"
    kill -9 $FPIDS || true
    echo "[kill_port] Done."
    exit 0
  fi
fi

echo "[kill_port] No listeners found or tools unavailable."
