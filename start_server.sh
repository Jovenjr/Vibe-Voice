#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$ROOT_DIR/server/server.pid"
LOG_FILE="$ROOT_DIR/server/vibe_voice.log"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"

if [[ -x "$VENV_PYTHON" ]]; then
  PYTHON_BIN="$VENV_PYTHON"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

HOST="${VIBE_VOICE_HOST:-127.0.0.1}"
UI_HOST="${VIBE_VOICE_UI_HOST:-127.0.0.1}"
PORT="${VIBE_VOICE_PORT:-8765}"
UI_PORT="${VIBE_VOICE_UI_PORT:-8080}"
IDE="${VIBE_VOICE_IDE:-all}"

if [[ -f "$PID_FILE" ]]; then
  OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${OLD_PID}" ]] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "[Vibe Voice] Ya está ejecutándose (PID $OLD_PID)."
    echo "[Vibe Voice] Usa ./status_server.sh para ver estado."
    exit 0
  fi
  rm -f "$PID_FILE"
fi

mkdir -p "$ROOT_DIR/server"
touch "$LOG_FILE"

cd "$ROOT_DIR"
nohup "$PYTHON_BIN" server/main.py \
  --host "$HOST" \
  --ui-host "$UI_HOST" \
  --port "$PORT" \
  --ui-port "$UI_PORT" \
  --ide "$IDE" \
  "$@" >>"$LOG_FILE" 2>&1 &

PID="$!"
echo "$PID" > "$PID_FILE"

echo "[Vibe Voice] Iniciado."
echo "[Vibe Voice] PID: $PID"
echo "[Vibe Voice] UI: http://$UI_HOST:$UI_PORT"
echo "[Vibe Voice] Logs: $LOG_FILE"
