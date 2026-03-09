@echo off
chcp 65001 > nul
title Vibe Voice - Docker

echo ============================================
echo    VIBE VOICE (Docker)
echo ============================================
echo.

REM Verificar si Docker está corriendo
docker info > nul 2>&1
if errorlevel 1 (
    echo ERROR: Docker no está corriendo.
    echo Inicia Docker Desktop y vuelve a intentar.
    pause
    exit /b 1
)

echo Construyendo imagen...
docker-compose build

echo.
echo Iniciando contenedor...
docker-compose up -d

echo.
echo ============================================
echo    Servidor iniciado en:
echo    http://localhost:8080
echo.
echo    Para ver logs:
echo    docker-compose logs -f
echo.
echo    Para detener:
echo    docker-compose down
echo ============================================
echo.
pause
