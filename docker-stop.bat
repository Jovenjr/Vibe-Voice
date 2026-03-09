@echo off
chcp 65001 > nul
echo Deteniendo contenedor...
docker-compose down
echo Contenedor detenido.
pause
