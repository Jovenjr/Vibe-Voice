# Vibe Voice

![CI](https://github.com/Jovenjr/Vibe-Voice/actions/workflows/ci.yml/badge.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)
Vibe Voice es una app local para visualizar en tiempo real respuestas de asistentes dentro de tu entorno de desarrollo y, además, ofrece utilidades opcionales de voz como TTS y dictado con Whisper.

El proyecto incluye:

- un servidor WebSocket que observa sesiones locales compatibles
- una interfaz web ligera para ver mensajes en vivo
- TTS opcional para leer respuestas
- una mini app de dictado de escritorio para Windows

## Qué hace

- Monitorea sesiones locales de chat compatibles
- Muestra mensajes y respuestas en vivo en la UI web
- Permite filtrar por IDE/editor soportado
- Puede leer respuestas con TTS
- Incluye dictado local con `Whisper`, `Groq` u `OpenAI`

## Novedades recientes (UI)

- `📌 Fijar` en la cabecera del chat para mantener una sesión seleccionada sin que otra actividad la cambie.
- `🔈 Activar audio` para desbloquear reproducción del navegador cuando el autoplay está bloqueado.
- Control de volumen local con icono dinámico (`🔇`/`🔈`/`🔉`/`🔊`) y `mute/unmute` con un clic.

## Compatibilidad

| Área | Soporte |
| --- | --- |
| Sistemas operativos | Linux y Windows |
| IDE/sesiones observables | `codex`, `copilot`, `cursor`, `kiro`, `vscode`, `vscode-insiders`, `all` |
| UI web | Navegadores modernos con WebSocket (`/ws`) |
| TTS remoto | Sí, vía audio en navegador (recomendado para despliegue remoto) |
| Dictado escritorio | Windows (`desktop_dictation.pyw`) |
| Bridge de pegado local | Windows (`desktop_paste_bridge.pyw`, `run_paste_bridge.bat`) |

## Estructura

```text
server/   backend, watchers, WebSocket y TTS
ui/       interfaz web estática
docs/     documentación adicional
```

## Requisitos

- Python 3.11+
- Linux y Windows soportados para el watcher principal
- El dictado de escritorio sigue siendo solo para Windows
- `ffmpeg` en el `PATH` si vas a usar dictado

## Instalación rápida

### 1) Clona el repositorio

```bash
git clone https://github.com/Jovenjr/Vibe-Voice.git
cd Vibe-Voice
```

### 2) Crea un entorno virtual

Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows:

```bash
python -m venv .venv
.venv\Scripts\activate
```

### 3) Instala dependencias del servidor

```bash
cd server
pip install -r requirements.txt
cd ..
```

### 4) Configura variables opcionales

Linux:

```bash
cp .env.example .env
```

Windows:

```bash
copy .env.example .env
```

## Ejecutar

### Servidor principal

```bash
cd server
python main.py
```

Luego abre:

- `http://localhost:8080`

Opciones útiles:

```bash
python main.py --host 0.0.0.0 --port 8765 --ui-port 8080
python main.py --host 127.0.0.1 --ui-host 127.0.0.1 --ide all
python main.py --ide vscode
python main.py --ide cursor
python main.py --ide kiro
python main.py --ide codex
python main.py --ide copilot
```

### Linux + Codex CLI

Si lo quieres arrancar directamente contra las sesiones locales de Codex CLI:

```bash
./run_codex_linux.sh
```

Este script lee sesiones desde `~/.codex/sessions`.

### Bridge de pegado local (Windows)

Para pegar transcripciones en la ventana activa con `Ctrl+V`:

```bat
run_paste_bridge.bat
```

Para detener el bridge:

```bat
stop_paste_bridge.bat
```

## Uso de la UI (rápido)

- Usa el filtro `IDE` para ver solo sesiones de una fuente.
- En historial, abre una sesión y pulsa `📌 Fijar` para mantenerla como foco principal.
- `Activar TTS` controla si se genera voz nueva en backend.
- `Activar audio` solo desbloquea la reproducción en el navegador.
- Ajusta `Volumen` o usa el icono de parlante para `mute/unmute`.

## Despliegue remoto seguro

Para publicar de forma segura:

1. Ejecuta el backend/UI solo en loopback (`127.0.0.1`).
2. Expón el servicio con un proxy HTTPS (Nginx) y `/ws` para WebSocket.
3. Restringe acceso por IP/CIDR en proxy y firewall.

Referencia completa:

- `docs/SECURE_REMOTE_ACCESS.md`
- `deploy/nginx/vibe-voice.conf`

### Lanzador oculto en Windows

```bash
pythonw run_hidden.pyw
```

### Dictado local

```bash
pythonw desktop_dictation.pyw
```

## Variables de entorno

Revisa `.env.example`. Las más importantes son:

- `GEMINI_API_KEY`
- `GEMINI_MODEL`
- `OPENAI_API_KEY`
- `OPENAI_AUDIO_MODEL`
- `GROQ_API_KEY`
- `GROQ_AUDIO_MODEL`
- `DICTATION_PROVIDER`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## Checklist antes de push a repositorio público

Antes de hacer `git push`:

1. Revisa `git status` y confirma que no entren archivos locales/runtime.
2. No subas `.env`, credenciales reales ni tokens.
3. Mantén `deploy/nginx/vibe-voice.conf` con valores de ejemplo (sin IP real).
4. Verifica que `data/`, `audio_cache/`, logs y DB locales sigan ignorados.
5. Si cambias UI/flujo, actualiza este README y los docs relacionados.

El `.gitignore` ya cubre artefactos comunes (`.env`, `*.log`, `*.db`, `data/`, `audio_cache/`).

## Notas

- Este proyecto está pensado para correr localmente y leer sesiones locales del usuario.
- Algunas integraciones dependen del sistema operativo y de cómo cada editor guarda sus sesiones.
- Las funciones de TTS, Telegram y dictado son opcionales.
- En Linux, VS Code se busca en `~/.config/Code` y Codex CLI en `~/.codex/sessions`.

## Repositorio público

- `https://github.com/Jovenjr/Vibe-Voice`

## GitHub setup

Suggested GitHub metadata is available in .github/REPO_METADATA.md.
Initial public release notes are available in .github/RELEASE_v0.1.0.md.
