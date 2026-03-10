#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$ROOT_DIR/server/server.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "[Vibe Voice] Estado: detenido (sin PID file)."
  exit 0
fi

PID="$(cat "$PID_FILE" 2>/dev/null || true)"
if [[ -z "$PID" ]]; then
  echo "[Vibe Voice] Estado: detenido (PID file vacío)."
  exit 0
fi

if kill -0 "$PID" 2>/dev/null; then
  echo "[Vibe Voice] Estado: activo (PID $PID)."
else
  echo "[Vibe Voice] Estado: detenido (PID $PID no existe)."
fi
