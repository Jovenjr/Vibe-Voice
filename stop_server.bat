@echo off
chcp 65001 > nul
echo Deteniendo Vibe Voice...

:: Leer PID si existe
if exist server\server.pid (
    set /p PID=<server\server.pid
    taskkill /PID %PID% /F > nul 2>&1
    del server\server.pid
    echo Servidor detenido (PID %PID%)
) else (
    :: Fallback: matar por nombre de proceso
    taskkill /IM python.exe /F /FI "WINDOWTITLE eq Copilot*" > nul 2>&1
    echo Proceso detenido.
)
pause
