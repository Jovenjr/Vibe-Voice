# Acceso remoto seguro

La forma recomendada de exponer Vibe Voice es:

1. Ejecutar Vibe Voice solo en loopback.
2. Publicar un proxy HTTPS delante.
3. Restringir acceso por IP en el proxy y/o firewall.

## Puertos internos

- UI HTTP: `127.0.0.1:8080`
- WebSocket: `127.0.0.1:8765`

## Arranque recomendado

```bash
cd /opt/Vibe-Voice
source .venv/bin/activate
python server/main.py --host 127.0.0.1 --ui-host 127.0.0.1 --ide all
```

Si solo quieres sesiones de Codex CLI, puedes usar:

```bash
./run_codex_linux.sh --host 127.0.0.1 --ui-host 127.0.0.1
```

## Nginx

Hay una config base en:

`deploy/nginx/vibe-voice.conf`

Esa config:

- expone solo `443`
- redirige `80` a `HTTPS`
- hace proxy de `/` a `127.0.0.1:8080`
- hace proxy de `/ws` a `127.0.0.1:8765`
- trae una IP de ejemplo (`203.0.113.10`) para allowlist

> Importante: en repositorio público no dejes IPs reales de tu infraestructura.
> Edita `deploy/nginx/vibe-voice.conf` en tu servidor con tu IP/CIDR y evita
> commitear esos valores sensibles.

## UFW

Aunque Nginx ya restringe por IP, conviene cerrar el resto:

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw deny 8080/tcp
sudo ufw deny 8765/tcp
```

Si no quieres exponer `80`, puedes cerrarlo despues de emitir el certificado.

## Certificados

Si usas un dominio con Let's Encrypt:

```bash
sudo apt update
sudo apt install -y nginx certbot python3-certbot-nginx
```

Luego:

```bash
sudo certbot --nginx -d tu-dominio
```

Despues actualiza en `deploy/nginx/vibe-voice.conf`:

- `server_name`
- `ssl_certificate`
- `ssl_certificate_key`

## Verificacion

Desde tu IP permitida:

```bash
curl -I https://tu-dominio
```

Desde otra IP, el resultado esperado es `403 Forbidden`.
