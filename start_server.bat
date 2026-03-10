@echo off
setlocal
chcp 65001 > nul
cd /d "%~dp0"

set "PID_FILE=server\server.pid"
set "LOG_FILE=server\vibe_voice.log"
set "ERR_FILE=server\vibe_voice.error.log"

if not exist server mkdir server >nul 2>&1

if exist "%PID_FILE%" (
  set /p PID=<"%PID_FILE%"
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "if (Get-Process -Id %PID% -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
  if not errorlevel 1 (
    echo [Vibe Voice] Ya está ejecutándose (PID %PID%).
    exit /b 0
  )
  del "%PID_FILE%" >nul 2>&1
)

if exist ".venv\Scripts\python.exe" (
  set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
) else (
  set "PYTHON_EXE=python"
)

if "%VIBE_VOICE_HOST%"=="" set "VIBE_VOICE_HOST=127.0.0.1"
if "%VIBE_VOICE_UI_HOST%"=="" set "VIBE_VOICE_UI_HOST=127.0.0.1"
if "%VIBE_VOICE_PORT%"=="" set "VIBE_VOICE_PORT=8765"
if "%VIBE_VOICE_UI_PORT%"=="" set "VIBE_VOICE_UI_PORT=8080"
if "%VIBE_VOICE_IDE%"=="" set "VIBE_VOICE_IDE=all"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$pythonExe = '%PYTHON_EXE%'; " ^
  "$workDir = '%CD%'; " ^
  "$args = 'server/main.py --host %VIBE_VOICE_HOST% --ui-host %VIBE_VOICE_UI_HOST% --port %VIBE_VOICE_PORT% --ui-port %VIBE_VOICE_UI_PORT% --ide %VIBE_VOICE_IDE%'; " ^
  "$proc = Start-Process -FilePath $pythonExe -ArgumentList $args -WorkingDirectory $workDir -RedirectStandardOutput 'server\\vibe_voice.log' -RedirectStandardError 'server\\vibe_voice.error.log' -WindowStyle Hidden -PassThru; " ^
  "Set-Content -Path 'server\\server.pid' -Value $proc.Id -Encoding ascii; " ^
  "Write-Host ('[Vibe Voice] Iniciado. PID ' + $proc.Id)"

echo [Vibe Voice] UI: http://%VIBE_VOICE_UI_HOST%:%VIBE_VOICE_UI_PORT%
echo [Vibe Voice] Logs: %LOG_FILE%
echo [Vibe Voice] Error log: %ERR_FILE%
exit /b 0
