#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$ROOT_DIR/server/server.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "[Vibe Voice] No hay PID file en $PID_FILE."
  echo "[Vibe Voice] Si el servidor está activo, deténlo manualmente con su PID."
  exit 0
fi

PID="$(cat "$PID_FILE" 2>/dev/null || true)"
if [[ -z "$PID" ]]; then
  rm -f "$PID_FILE"
  echo "[Vibe Voice] PID file vacío. Limpiado."
  exit 0
fi

if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  sleep 1
  if kill -0 "$PID" 2>/dev/null; then
    kill -9 "$PID"
  fi
  echo "[Vibe Voice] Servidor detenido (PID $PID)."
else
  echo "[Vibe Voice] El proceso $PID ya no estaba activo."
fi

rm -f "$PID_FILE"
