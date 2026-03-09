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

## Estructura

```text
server/   backend, watchers, WebSocket y TTS
ui/       interfaz web estática
docs/     documentación adicional
```

## Requisitos

- Python 3.11+
- Windows recomendado para las funciones de watcher y dictado
- `ffmpeg` en el `PATH` si vas a usar dictado

## Instalación rápida

### 1) Clona el repositorio

```bash
git clone https://github.com/Jovenjr/Vibe-Voice.git
cd Vibe-Voice
```

### 2) Crea un entorno virtual

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
python main.py --ide vscode
python main.py --ide cursor
python main.py --ide kiro
python main.py --ide codex
```

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

## Publicar este repo sin filtrar datos locales

Antes de hacer `git push`, verifica que no subas archivos generados localmente:

- `.env`
- `data/`
- `audio_cache/`
- `*.log`
- `*.db`
- transcripciones temporales

Ya se añadió un `.gitignore` orientado a publicación pública.

## Notas

- Este proyecto está pensado para correr localmente y leer sesiones locales del usuario.
- Algunas integraciones dependen del sistema operativo y de cómo cada editor guarda sus sesiones.
- Las funciones de TTS, Telegram y dictado son opcionales.

## Repositorio público

- `https://github.com/Jovenjr/Vibe-Voice`

## GitHub setup

Suggested GitHub metadata is available in .github/REPO_METADATA.md.
Initial public release notes are available in .github/RELEASE_v0.1.0.md.

