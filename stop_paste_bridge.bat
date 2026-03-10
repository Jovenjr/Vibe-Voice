@echo off
setlocal

echo [Vibe Voice] Deteniendo bridge local...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$targets = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*desktop_paste_bridge.pyw*' }; " ^
  "if (-not $targets) { Write-Host 'No hay instancias activas.'; exit 0 }; " ^
  "$targets | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host ('Cerrado PID ' + $_.ProcessId) }"

exit /b 0
