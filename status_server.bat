@echo off
setlocal
chcp 65001 > nul
cd /d "%~dp0"

set "PID_FILE=server\server.pid"
if not exist "%PID_FILE%" (
  echo [Vibe Voice] Estado: detenido (sin PID file).
  exit /b 0
)

set /p PID=<"%PID_FILE%"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "if (Get-Process -Id %PID% -ErrorAction SilentlyContinue) { Write-Host '[Vibe Voice] Estado: activo (PID %PID%).'; exit 0 } " ^
  "else { Write-Host '[Vibe Voice] Estado: detenido (PID %PID% no existe).'; exit 1 }"
exit /b 0
