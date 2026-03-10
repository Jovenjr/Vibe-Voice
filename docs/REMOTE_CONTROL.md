# Control remoto (web + Telegram)

Este documento explica como usar funciones remotas sin comprometer seguridad.
Modo remoto/VPS = avanzado. Primero valida modo local con [`INSTALL_LOCAL.md`](INSTALL_LOCAL.md) y [`OPERATIONS.md`](OPERATIONS.md).

## Precondicion (local estable)

Linux:

```bash
./doctor.sh
./start_server.sh
./status_server.sh
```

Windows:

```bat
doctor.bat
start_server.bat
status_server.bat
```

## Objetivo

Permitir control y observabilidad a distancia del flujo de trabajo del agente:

- ver sesiones y actividad en UI web
- escuchar salida (TTS) en navegador
- enviar entrada remota por Telegram (si se habilita)

## Web remota

1. Ejecuta servidor en loopback.
2. Publica por reverse proxy HTTPS.
3. Restringe acceso por IP/CIDR.
4. No expongas directamente `:8080` o `:8765`.

Referencia: [`SECURE_REMOTE_ACCESS.md`](SECURE_REMOTE_ACCESS.md).

## Telegram input

Requiere:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Riesgo principal: inyeccion de entrada remota.

Mitigaciones:

- usa un chat autorizado estricto
- protege el token
- desactiva Telegram input cuando no lo uses

## Bridge local (Windows)

El bridge local (`desktop_paste_bridge.pyw`) escucha en `127.0.0.1:8766` y pega en ventana activa.

Buenas practicas:

- no exponer ese puerto externamente
- no tunelar el bridge
- activar solo cuando sea necesario

## Diferencia clave de audio

- `Activar TTS`: genera voz nueva en backend.
- `Activar audio`: desbloquea reproduccion del navegador (autoplay/política del browser).
