@echo off
setlocal
cd /d "%~dp0"

echo [Vibe Voice] Cerrando instancias anteriores del bridge...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$targets = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*desktop_paste_bridge.pyw*' }; " ^
  "if ($targets) { $targets | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } }"

timeout /t 1 /nobreak >nul

if exist ".venv\Scripts\pythonw.exe" (
  echo [Vibe Voice] Iniciando bridge local con .venv\Scripts\pythonw.exe
  start "" ".venv\Scripts\pythonw.exe" "%~dp0desktop_paste_bridge.pyw"
  exit /b 0
)

if exist ".venv\Scripts\python.exe" (
  echo [Vibe Voice] Iniciando bridge local con .venv\Scripts\python.exe
  start "" ".venv\Scripts\python.exe" "%~dp0desktop_paste_bridge.pyw"
  exit /b 0
)

echo [Vibe Voice] Iniciando bridge local con pyw
start "" pyw "%~dp0desktop_paste_bridge.pyw"
