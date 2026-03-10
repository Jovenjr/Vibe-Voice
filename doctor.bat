@echo off
setlocal
chcp 65001 > nul
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  .venv\Scripts\python.exe server\doctor.py %*
) else (
  python server\doctor.py %*
)
