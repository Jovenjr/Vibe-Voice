# Contributing to Vibe Voice

Gracias por tu interés en contribuir a `Vibe Voice`.

## Cómo contribuir

- Abre un `issue` para bugs, ideas o preguntas.
- Crea un fork y trabaja en una rama corta y descriptiva.
- Envía un `pull request` con una explicación clara del cambio.
- Mantén los cambios enfocados y evita mezclar refactors no relacionados.

## Entorno local

Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r server/requirements.txt
```

Windows:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r server\requirements.txt
```

Chequeo rapido recomendado:

Linux:

```bash
./doctor.sh
```

Windows:

```bat
doctor.bat
```

## Estilo general

- Mantén nombres claros y cambios pequeños.
- Respeta la estructura actual del proyecto.
- No subas secretos, logs, bases de datos ni archivos temporales.
- Si agregas una variable nueva, actualiza `.env.example`.
- Si cambias comportamiento visible, actualiza `README.md`.

## Validación mínima

Antes de abrir un PR, valida al menos esto:

```bash
python - <<'PY'
import ast
from pathlib import Path
for path in Path('server').glob('*.py'):
    ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
print('OK')
PY
```

Y para UI:

```bash
node --check ui/app.js
```

## Pull requests

Incluye en tu PR:

- objetivo del cambio
- archivos principales modificados
- cómo probarlo localmente
- capturas si cambias la UI

## Seguridad

Si encuentras una vulnerabilidad o una exposición accidental de datos, evita publicarla en un issue abierto hasta mitigarla.
