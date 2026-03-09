@echo off
chcp 65001 > nul
title Vibe Voice [NO CLICAR]

echo.
echo  ================================================
echo   VIBE VOICE
echo  ================================================
echo   Abre en el navegador: http://localhost:8080
echo   NO cliques dentro de esta ventana
echo  ================================================
echo.

python -u main.py %*
pause
