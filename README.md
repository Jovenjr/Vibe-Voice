# Vibe Voice

![CI](https://github.com/Jovenjr/Vibe-Voice/actions/workflows/ci.yml/badge.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)

Vibe Voice convierte tus sesiones de coding assistants en una experiencia de **vibe coding conversacional**: puedes ver en vivo lo que el agente responde, escuchar su salida por voz, transcribir tu voz y operar sesiones a distancia desde la web o Telegram (segun configuracion).

## Tabla de contenidos

- [Proposito del proyecto](#proposito-del-proyecto)
- [Features de valor](#features-de-valor)
- [Quick start](#quick-start)
- [Instalacion](#instalacion)
- [Uso](#uso)
- [Control remoto y voz](#control-remoto-y-voz)
- [Compatibilidad](#compatibilidad)
- [Configuracion](#configuracion)
- [Seguridad, clausulas y advertencias](#seguridad-clausulas-y-advertencias)
- [Despliegue remoto seguro](#despliegue-remoto-seguro)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Contribuir](#contribuir)
- [Licencia](#licencia)

## Proposito del proyecto

Vibe Voice nace para resolver este flujo:

1. Tu agente de codificacion trabaja.
2. Tu quieres **ver y escuchar** en tiempo real que esta pasando (respuesta, actividad, herramientas, progreso).
3. Tu quieres poder **interactuar por voz** y tambien operar sesiones de forma remota cuando no estas frente al IDE.

En resumen: menos friccion para iterar, mas contexto en vivo y mas control operativo.

## Features de valor

- Monitoreo en vivo de sesiones locales de `codex`, `copilot`, `cursor`, `kiro`, `vscode`, `vscode-insiders`.
- Timeline de chat en la UI web con estado de actividad del agente (pensando, ejecutando herramienta, esperando confirmacion, etc.).
- Historial persistente en SQLite con busqueda, exportacion y archivado de sesiones.
- Pin de sesion (`📌 Fijar`) para mantener foco sin que otra actividad te cambie la vista.
- TTS server-side con reproduccion remota en navegador:
  - `Activar TTS`: genera voz nueva en backend.
  - `Activar audio`: desbloquea autoplay del navegador.
  - Volumen con icono dinamico (`🔇/🔈/🔉/🔊`) y mute/unmute.
- STT multi-proveedor (Whisper local, Groq, Gemini, Google Cloud), via WebSocket y via `POST /api/stt`.
- Entrada remota por Telegram (texto o voz) para transcribir/pegar en el entorno local.
- Bridge local de pegado en Windows (`desktop_paste_bridge.pyw`) con hotkey global `F8`.
- Secretos de configuracion persistidos con cifrado (settings sensibles en DB).

## Quick start

```bash
git clone https://github.com/Jovenjr/Vibe-Voice.git
cd Vibe-Voice
python3 -m venv .venv
source .venv/bin/activate
pip install -r server/requirements.txt
python server/main.py --host 127.0.0.1 --ui-host 127.0.0.1 --ide all
```

Luego abre `http://127.0.0.1:8080`.

## Instalacion

### Requisitos

- Python 3.11+
- `ffmpeg` en `PATH` si usas dictado/STT local
- Windows para utilidades de escritorio (`desktop_dictation.pyw`, `desktop_paste_bridge.pyw`)

### Pasos

Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r server/requirements.txt
cp .env.example .env
```

Windows:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r server\requirements.txt
copy .env.example .env
```

## Uso

### Servidor principal

```bash
python server/main.py
```

Flags utiles:

```bash
python server/main.py --host 127.0.0.1 --ui-host 127.0.0.1 --ide all
python server/main.py --ide codex
python server/main.py --ide copilot
python server/main.py --ide cursor
python server/main.py --ide kiro
python server/main.py --ide vscode
```

### Script Linux para Codex CLI

```bash
./run_codex_linux.sh
```

### UI web (flujo sugerido)

1. Abre sesion desde la barra izquierda.
2. Usa `📌 Fijar` para mantener contexto.
3. Activa TTS si quieres narracion.
4. Si el navegador bloquea autoplay, pulsa `🔈 Activar audio`.
5. Ajusta volumen o silencia con el icono de parlante.

## Control remoto y voz

### Telegram input (control remoto)

Cuando esta habilitado, Vibe Voice puede recibir mensajes (texto/voz) del chat autorizado y convertirlos en entrada util para tu flujo local.

### Bridge local de pegado (Windows)

Iniciar bridge:

```bat
run_paste_bridge.bat
```

Detener bridge:

```bat
stop_paste_bridge.bat
```

Este bridge escucha en `ws://127.0.0.1:8766` y usa `Ctrl+V` en la ventana activa (localhost solamente).

## Compatibilidad

| Area | Soporte |
| --- | --- |
| Sistemas operativos | Linux y Windows |
| IDE/sesiones observables | `all`, `codex`, `copilot`, `cursor`, `kiro`, `vscode`, `vscode-insiders` |
| UI web | Navegadores modernos con WebSocket |
| Audio remoto | Si (reproduccion en navegador) |
| Dictado desktop | Windows |
| Bridge local de pegado | Windows |

## Configuracion

Variables relevantes en `.env`:

- `GEMINI_API_KEY`, `GEMINI_MODEL`
- `GROQ_API_KEY`
- `OPENAI_API_KEY`, `OPENAI_AUDIO_MODEL`
- `DICTATION_PROVIDER`
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- `STT_PROVIDER`, `WHISPER_MODEL`

Variables operativas utiles:

- `CODEX_SESSIONS_OVERRIDE`
- `COPILOT_SESSIONS_OVERRIDE`
- `TTS_BROWSER_PLAYBACK`
- `VIBE_VOICE_SETTINGS_KEY` (clave de cifrado para settings sensibles)
- `VIBE_VOICE_BRIDGE_HOST`, `VIBE_VOICE_BRIDGE_PORT`, `VIBE_VOICE_BRIDGE_SEND_ENTER`, `VIBE_VOICE_BRIDGE_NAME`

## Seguridad, clausulas y advertencias

> **Clausula de uso responsable:** al activar canales remotos (web publica, Telegram input, bridge de pegado), estas habilitando control indirecto de flujo de trabajo local. Debe usarse solo en entornos confiables y con controles de acceso.

Advertencias importantes:

- No expongas `:8080` ni `:8765` directamente a Internet.
- Usa siempre proxy HTTPS + allowlist IP/CIDR para acceso remoto.
- Nunca publiques `.env`, tokens, rutas privadas ni datos de sesiones reales.
- Protege `TELEGRAM_BOT_TOKEN` como credencial critica.
- Si habilitas Telegram input, limita estrictamente `TELEGRAM_CHAT_ID`.
- El bridge de pegado debe permanecer en localhost; no lo tunnels ni publiques.
- Revisa `SECURITY.md` y `docs/SECURE_REMOTE_ACCESS.md` antes de desplegar.

## Despliegue remoto seguro

Resumen:

1. Ejecuta backend/UI en loopback (`127.0.0.1`).
2. Publica via Nginx (HTTPS) con proxy para `/` y `/ws`.
3. Restringe acceso por IP/CIDR en proxy y firewall.

Guia y template:

- `docs/SECURE_REMOTE_ACCESS.md`
- `deploy/nginx/vibe-voice.conf`

## Estructura del proyecto

```text
server/                   backend, parser, watcher, DB, TTS/STT
ui/                       app web (historial, controles de sesion y audio)
deploy/nginx/             template de reverse proxy seguro
docs/                     guias de integracion y despliegue
desktop_dictation.pyw     app de dictado local (Windows)
desktop_paste_bridge.pyw  bridge local para pegar texto remoto (Windows)
run_codex_linux.sh        launcher Linux orientado a sesiones Codex
run_paste_bridge.bat      inicia bridge de pegado en Windows
stop_paste_bridge.bat     detiene bridge de pegado en Windows
```

## Contribuir

1. Fork del repo.
2. Crea rama: `git checkout -b feat/mi-cambio`.
3. Haz commits pequenos y claros.
4. Ejecuta chequeos de sintaxis/funcionamiento.
5. Abre Pull Request con contexto tecnico y validacion.

## Licencia

MIT. Ver [LICENSE](LICENSE).
