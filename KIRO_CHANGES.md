# Cambios para Soporte de Kiro

Este documento resume todos los cambios realizados para agregar soporte del editor Kiro a Vibe Voice.

## Fecha
5 de marzo de 2026

## Resumen
Se agregÃ³ soporte completo para el editor Kiro, que usa un formato JSON diferente al formato JSONL incremental de VS Code y Cursor.

## Archivos Nuevos

### 1. `server/kiro_parser.py`
Parser especÃ­fico para archivos JSON de Kiro.

**Funcionalidades:**
- `KiroParser`: Clase principal para parsear archivos JSON de Kiro
- `find_most_recent_kiro_session()`: Encuentra la sesiÃ³n mÃ¡s reciente
- `get_all_kiro_session_files()`: Lista todos los archivos de sesiÃ³n
- `parse_file()`: Parsea un archivo JSON de Kiro
- `get_all_messages()`: Extrae mensajes como objetos KiroMessage
- `compare_sessions()`: Detecta cambios entre dos estados de sesiÃ³n

**UbicaciÃ³n de datos:** `%APPDATA%\Kiro\User\History\<session_id>\*.json`

### 2. `server/test_kiro.py`
Suite de pruebas para el parser de Kiro.

**Tests incluidos:**
- DetecciÃ³n de sesiones
- Parsing de archivos
- ExtracciÃ³n de mensajes
- DetecciÃ³n de cambios

### 3. `server/test_kiro_simple.py`
Prueba simple para verificar el parsing de un archivo especÃ­fico.

### 4. `server/demo_kiro.py`
DemostraciÃ³n interactiva del soporte de Kiro.

**Secciones:**
1. DetecciÃ³n de sesiones
2. Parsing de sesiÃ³n
3. ExtracciÃ³n de mensajes
4. DetecciÃ³n de cambios
5. Monitoreo en tiempo real

### 5. `server/verify_kiro_integration.py`
Script de verificaciÃ³n completa de la integraciÃ³n.

**Verificaciones:**
- Imports correctos
- IDEs soportados
- DetecciÃ³n de sesiones
- Funcionamiento del parser
- IntegraciÃ³n con file_watcher

### 6. `server/test_imports.py`
Prueba simple de imports.

### 7. `docs/kiro_format_example.json`
Ejemplo del formato JSON de Kiro.

### 8. `docs/KIRO_INTEGRATION.md`
DocumentaciÃ³n completa de la integraciÃ³n de Kiro.

**Contenido:**
- Resumen de la integraciÃ³n
- UbicaciÃ³n de datos
- Formato de datos
- Diferencias con otros editores
- ImplementaciÃ³n
- Uso
- Funcionamiento
- Pruebas
- Limitaciones
- SoluciÃ³n de problemas
- Desarrollo futuro

## Archivos Modificados

### 1. `server/jsonl_parser.py`

**Cambios:**
```python
# Antes
SUPPORTED_IDES = {
    "all": {"name": "Todos", "folders": ["Code - Insiders", "Code"], "include_cursor": True},
    "vscode-insiders": {"name": "VS Code Insiders", "folders": ["Code - Insiders"], "include_cursor": False},
    "vscode": {"name": "VS Code", "folders": ["Code"], "include_cursor": False},
    "cursor": {"name": "Cursor", "folders": [], "include_cursor": True},
}

# DespuÃ©s
SUPPORTED_IDES = {
    "all": {"name": "Todos", "folders": ["Code - Insiders", "Code"], "include_cursor": True, "include_kiro": True},
    "vscode-insiders": {"name": "VS Code Insiders", "folders": ["Code - Insiders"], "include_cursor": False, "include_kiro": False},
    "vscode": {"name": "VS Code", "folders": ["Code"], "include_cursor": False, "include_kiro": False},
    "cursor": {"name": "Cursor", "folders": [], "include_cursor": True, "include_kiro": False},
    "kiro": {"name": "Kiro", "folders": [], "include_cursor": False, "include_kiro": True},
}
```

### 2. `server/file_watcher.py`

**Cambios principales:**

1. **Imports:**
```python
from kiro_parser import KiroParser, find_most_recent_kiro_session, get_all_kiro_session_files
```

2. **Clase CopilotChatWatcher.__init__:**
```python
self.kiro_parser = KiroParser()
self.kiro_sessions: Dict[str, Dict] = {}
```

3. **MÃ©todo _get_watch_directories:**
- Agregado soporte para `include_kiro`
- Agregado monitoreo de `%APPDATA%\Kiro\User\History\*\`

4. **MÃ©todo _load_current_session:**
- Agregada lÃ³gica para cargar sesiones de Kiro
- Detecta si debe monitorear Kiro segÃºn el filtro de IDE

5. **MÃ©todo _poll_loop:**
- Agregada llamada a `_poll_kiro_sessions()` cuando corresponde

6. **Nuevo mÃ©todo _poll_kiro_sessions:**
- Polling especÃ­fico para archivos JSON de Kiro
- Detecta cambios comparando estados
- Emite eventos WebSocket para mensajes nuevos

### 3. `README.md`

**Cambios:**

1. **CaracterÃ­sticas:**
- Agregado "Kiro" a la lista de editores soportados

2. **Uso:**
- Agregada secciÃ³n "SelecciÃ³n de IDE"
- Ejemplos de comandos para filtrar por IDE
- MenciÃ³n del selector de IDE en la UI

3. **Estructura del Proyecto:**
- Agregado `kiro_parser.py` a la lista de archivos

4. **Formatos Soportados:**
- Nueva secciÃ³n explicando diferencias entre JSONL y JSON
- Ejemplo del formato de Kiro

5. **UbicaciÃ³n de los datos:**
- Agregada ubicaciÃ³n de Kiro: `%APPDATA%\Kiro\User\History\*\*.json`

## Formato de Datos

### VS Code / Cursor (JSONL)
```jsonl
{"kind":0,"v":{"sessionId":"abc123"}}
{"kind":1,"k":["requests",0,"message","text"],"v":"Hello"}
```

### Kiro (JSON)
```json
{
  "messages": [
    {"role": "user", "content": "Hello"},
    {"role": "assistant", "content": "Hi!"}
  ]
}
```

## Uso

### Iniciar con Kiro
```bash
# Solo Kiro
python server/main.py --ide kiro

# Todos los editores (incluye Kiro)
python server/main.py --ide all
```

### Verificar IntegraciÃ³n
```bash
python server/verify_kiro_integration.py
```

### Ejecutar Demo
```bash
python server/demo_kiro.py
```

## Pruebas Realizadas

âœ“ DetecciÃ³n de sesiones de Kiro (2749 archivos encontrados)
âœ“ Parsing de archivos JSON
âœ“ ExtracciÃ³n de mensajes
âœ“ DetecciÃ³n de cambios
âœ“ IntegraciÃ³n con file_watcher
âœ“ Imports correctos
âœ“ ConfiguraciÃ³n de IDEs soportados

## Compatibilidad

- **Windows**: âœ“ Completamente soportado
- **Linux/Mac**: âš  Requiere ajuste de rutas (Kiro usa diferentes ubicaciones)
- **Docker**: âš  Requiere montar el directorio de Kiro

## Limitaciones Conocidas

1. Sin timestamps en mensajes (Kiro no los incluye)
2. Sin streaming de chunks (mensajes completos)
3. DetecciÃ³n de cambios basada en longitud del array
4. Solo formato actual de Kiro (puede cambiar)

## PrÃ³ximos Pasos

Posibles mejoras futuras:
1. Agregar timestamps basados en mtime
2. Monitorear mÃºltiples sesiones simultÃ¡neamente
3. Cargar historial completo
4. Mejorar detecciÃ³n de cambios con hash
5. Soportar metadata adicional

## ConclusiÃ³n

La integraciÃ³n de Kiro estÃ¡ completa y funcional. El sistema ahora soporta:
- VS Code
- VS Code Insiders
- Cursor
- Kiro

Todos los tests pasan y la funcionalidad estÃ¡ documentada.

