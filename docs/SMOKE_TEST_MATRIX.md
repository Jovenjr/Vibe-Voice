# Smoke test matrix

Matriz minima para validar release publico sin romper flujo principal.
En P0, prioriza escenarios local-first; remoto queda como validacion avanzada.

## Escenarios P0

| Escenario | Plataforma | Resultado esperado |
| --- | --- | --- |
| Arranque local (`./start_server.sh`) | Linux | UI y WS activos en `127.0.0.1` |
| Arranque local (`start_server.bat`) | Windows | UI y WS activos, PID registrado |
| Doctor check | Linux/Windows | Sin `FAIL` en dependencias core |
| Filtro IDE (`all`, `codex`, `copilot`) | Linux/Windows | Sesiones visibles segun filtro |
| Pin de sesion (`📌 Fijar`) | UI web | La vista no cambia por actividad externa |
| Audio browser unlock (`🔈 Activar audio`) | UI web | Reproduccion remota sin backlog |
| Volumen/mute icono | UI web | Icono y volumen se sincronizan |
| STT por API (`/api/stt`) | Linux/Windows | Respuesta JSON `ok=true` o error explicito |
| Telegram input habilitado | Linux/Windows | Solo procesa chat autorizado |
| Bridge local Windows | Windows | Pega texto en ventana activa (localhost) |

## Validaciones de seguridad

| Chequeo | Resultado esperado |
| --- | --- |
| Backend/UI en loopback por defecto | `VIBE_VOICE_HOST=127.0.0.1` y `VIBE_VOICE_UI_HOST=127.0.0.1` |
| Plantilla Nginx sin IP real | Solo valores de ejemplo |
| Sin secretos en repo | Sin tokens/API keys reales en tracked files |
| Puertos internos no expuestos | Firewall/proxy bloquean acceso directo |

## Comandos sugeridos

```bash
# Sintaxis Python
python3 - <<'PY'
import ast
from pathlib import Path
for p in Path('server').glob('*.py'):
    ast.parse(p.read_text(encoding='utf-8'), filename=str(p))
print('OK')
PY

# Sintaxis JS
node --check ui/app.js

# Doctor
./doctor.sh
```
