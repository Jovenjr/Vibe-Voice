# Operacion diaria

Guia rapida para operar Vibe Voice sin perderse entre scripts.
Prioridad P0: modo local-first. Modo remoto/VPS solo cuando ya tengas local estable.

## Scripts operativos estandar

### Linux

- `./start_server.sh` inicia el servidor principal.
- `./status_server.sh` muestra estado por PID.
- `./stop_server.sh` detiene el servidor por PID.
- `./doctor.sh` valida prerequisitos y salud basica.

### Windows

- `start_server.bat` inicia el servidor principal.
- `status_server.bat` muestra estado por PID.
- `stop_server.bat` detiene el servidor.
- `doctor.bat` valida prerequisitos y salud basica.

## Scripts especializados (opcionales)

- `run_codex_linux.sh`: arranque directo orientado a sesiones Codex CLI.
- `run_dictation.bat`: app de dictado local en Windows.
- `run_paste_bridge.bat` / `stop_paste_bridge.bat`: bridge local de pegado para flujo remoto en Windows.

## Variables operativas utiles

- `VIBE_VOICE_HOST` (default `127.0.0.1`)
- `VIBE_VOICE_UI_HOST` (default `127.0.0.1`)
- `VIBE_VOICE_PORT` (default `8765`)
- `VIBE_VOICE_UI_PORT` (default `8080`)
- `VIBE_VOICE_IDE` (default `all`)

## Notas de logs

- Log principal: `server/vibe_voice.log`
- Error log Windows (start_server.bat): `server/vibe_voice.error.log`
- PID file: `server/server.pid`

## Recomendaciones

- Mantener defaults en loopback salvo despliegue remoto controlado.
- Ejecutar `doctor` despues de cambios de dependencias o configuracion.
- Para remoto seguro, seguir [`SECURE_REMOTE_ACCESS.md`](SECURE_REMOTE_ACCESS.md).
