# Acceso remoto seguro para Vibe Voice

Esta guia resume como exponer Vibe Voice minimizando riesgo.
Asume que ya validaste modo local-first (ver [`INSTALL_LOCAL.md`](INSTALL_LOCAL.md) y [`OPERATIONS.md`](OPERATIONS.md)).

## Clausula de seguridad y uso responsable

Al habilitar acceso remoto, TTS/STT y canales de entrada (web/Telegram/bridge), estas ampliando la superficie de control sobre tu entorno de desarrollo.

Usalo solo en equipos y redes confiables, con controles de acceso estrictos y credenciales bien protegidas.

## Principios base

1. Ejecuta Vibe Voice solo en loopback (`127.0.0.1`).
2. Publica hacia Internet unicamente detras de HTTPS reverse proxy.
3. Restringe por IP/CIDR permitida.
4. Nunca expongas puertos internos directos.

## Puertos internos esperados

- UI HTTP: `127.0.0.1:8080`
- WebSocket: `127.0.0.1:8765`
- Bridge local Windows: `127.0.0.1:8766` (solo local)

## Arranque recomendado

Modo general (todos los IDE soportados, via script unificado):

```bash
cd /opt/Vibe-Voice
VIBE_VOICE_HOST=127.0.0.1 VIBE_VOICE_UI_HOST=127.0.0.1 VIBE_VOICE_IDE=all ./start_server.sh
./status_server.sh
```

Modo manual (avanzado, equivalente):

```bash
python server/main.py --host 127.0.0.1 --ui-host 127.0.0.1 --ide all
```

Modo Codex CLI rapido (avanzado):

```bash
./run_codex_linux.sh --host 127.0.0.1 --ui-host 127.0.0.1
```

## Nginx (template seguro)

Template versionado:

- `deploy/nginx/vibe-voice.conf`

Ese archivo incluye:

- redirect HTTP -> HTTPS
- proxy de `/` a `127.0.0.1:8080`
- proxy de `/ws` a `127.0.0.1:8765`
- allowlist por IP/CIDR con valores de ejemplo (`203.0.113.10`)

> Importante para repositorio publico: nunca subas IPs reales de infraestructura.

## Firewall (UFW)

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw deny 8080/tcp
sudo ufw deny 8765/tcp
sudo ufw deny 8766/tcp
```

## Certificados TLS

```bash
sudo apt update
sudo apt install -y nginx certbot python3-certbot-nginx
sudo certbot --nginx -d tu-dominio
```

Actualiza luego:

- `server_name`
- `ssl_certificate`
- `ssl_certificate_key`

## Riesgos especificos y mitigaciones

### 1) Telegram input habilitado

Riesgo: entrada remota al flujo local.

Mitigaciones:

- Define `TELEGRAM_CHAT_ID` estricto.
- Protege `TELEGRAM_BOT_TOKEN`.
- Desactiva Telegram input cuando no lo uses.

### 2) Bridge local de pegado (Windows)

Riesgo: inyeccion de texto en ventana activa local.

Mitigaciones:

- Mantenerlo en localhost (`127.0.0.1`) siempre.
- No tunelarlo ni exponerlo externamente.
- Ejecutarlo solo cuando se necesite.

### 3) Secrets y configuracion

Riesgo: fuga de API keys/tokens.

Mitigaciones:

- No commitear `.env`.
- Usar settings cifrados en DB para claves sensibles.
- Definir `VIBE_VOICE_SETTINGS_KEY` en despliegues controlados.

## Verificacion operativa

Desde IP autorizada:

```bash
curl -I https://tu-dominio
```

Desde IP no autorizada, deberias obtener `403 Forbidden`.

## Checklist antes de publicar cambios

1. `git status` limpio de secretos/artefactos locales.
2. Sin tokens reales en codigo, docs o configs.
3. `deploy/nginx/vibe-voice.conf` solo con ejemplos.
4. Puertos internos no expuestos publicamente.
5. Revisado `SECURITY.md`.
