@echo off
setlocal
chcp 65001 > nul
cd /d "%~dp0"
echo [Vibe Voice] Deteniendo servidor...

set "PID_FILE=server\server.pid"

if exist "%PID_FILE%" (
  set /p PID=<"%PID_FILE%"
  taskkill /PID %PID% /F >nul 2>&1
  if errorlevel 1 (
    echo [Vibe Voice] El PID %PID% no estaba activo.
  ) else (
    echo [Vibe Voice] Servidor detenido (PID %PID%).
  )
  del "%PID_FILE%" >nul 2>&1
  exit /b 0
)

echo [Vibe Voice] PID file no encontrado, buscando proceso Vibe Voice por command line...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$targets = Get-CimInstance Win32_Process | Where-Object { " ^
  "  $cmd = $_.CommandLine; " ^
  "  if ([string]::IsNullOrWhiteSpace($cmd)) { return $false }; " ^
  "  if ($_.Name -notmatch '^python(?:w)?\.exe$') { return $false }; " ^
  "  $normalized = $cmd.ToLowerInvariant().Replace('\','/'); " ^
  "  ($normalized -match '(?:^|\s)\x22?server/main\.py(?:\x22?\s|$)') -and " ^
  "  ($normalized -match '--host\s+\S+') -and " ^
  "  ($normalized -match '--ui-host\s+\S+') -and " ^
  "  ($normalized -match '--port\s+\d+') -and " ^
  "  ($normalized -match '--ui-port\s+\d+') -and " ^
  "  ($normalized -match '--ide\s+\S+') " ^
  "}; " ^
  "if (-not $targets) { Write-Host '[Vibe Voice] No hay instancias activas.'; exit 0 }; " ^
  "$targets | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host ('[Vibe Voice] Cerrado PID ' + $_.ProcessId) }"

exit /b 0
