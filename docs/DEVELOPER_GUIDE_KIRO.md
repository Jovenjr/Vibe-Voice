# Guía para Desarrolladores - Soporte de Kiro

Esta guía explica cómo funciona internamente el soporte de Kiro y cómo extenderlo.

## Arquitectura

### Componentes Principales

```
┌─────────────────────────────────────────────────────────────┐
│                    Kiro Editor                              │
│  Escribe archivos JSON en %APPDATA%\Kiro\User\History\     │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      │ Archivos JSON
                      ↓
┌─────────────────────────────────────────────────────────────┐
│              CopilotChatWatcher                             │
│  - Polling cada 100ms                                       │
│  - Detecta archivos más recientes                           │
│  - Llama a _poll_kiro_sessions()                            │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      ↓
┌─────────────────────────────────────────────────────────────┐
│                 KiroParser                                  │
│  - parse_file(): Lee y parsea JSON                          │
│  - compare_sessions(): Detecta cambios                      │
│  - get_all_messages(): Extrae mensajes                      │
└─────────────────────┬───────────────────────────────────────┘
                      │
                      │ Eventos
                      ↓
┌─────────────────────────────────────────────────────────────┐
│              WebSocket Server                               │
│  Emite eventos a clientes conectados                        │
└─────────────────────────────────────────────────────────────┘
```

## Flujo de Datos

### 1. Detección de Sesiones

```python
# En file_watcher.py
def _get_watch_directories(self) -> List[Path]:
    # ...
    if include_kiro:
        kiro_history = appdata / "Kiro" / "User" / "History"
        if kiro_history.exists():
            for session_dir in kiro_history.iterdir():
                if session_dir.is_dir():
                    dirs.append(session_dir)
```

**Proceso:**
1. Verifica si `include_kiro` está habilitado
2. Busca el directorio `%APPDATA%\Kiro\User\History\`
3. Agrega cada carpeta de sesión a la lista de directorios monitoreados

### 2. Polling de Archivos

```python
# En file_watcher.py
def _poll_loop(self):
    while self.polling_active:
        # ...
        if include_kiro and self.ide_filter in ["kiro", "all"]:
            self._poll_kiro_sessions()
```

**Proceso:**
1. Cada 100ms, verifica si debe monitorear Kiro
2. Llama a `_poll_kiro_sessions()` si corresponde
3. Continúa con el polling de otros editores

### 3. Detección de Cambios

```python
# En file_watcher.py
def _poll_kiro_sessions(self):
    recent_file = find_most_recent_kiro_session()
    session_data = self.kiro_parser.parse_file(recent_file)
    old_data = self.kiro_sessions.get(file_key)
    changes = self.kiro_parser.compare_sessions(old_data, session_data)
```

**Proceso:**
1. Encuentra el archivo JSON más reciente
2. Parsea el archivo completo
3. Compara con el estado anterior en cache
4. Identifica mensajes nuevos

### 4. Emisión de Eventos

```python
# En file_watcher.py
for change in changes:
    if change["type"] == "new_message":
        msg = change["message"]
        if msg.get("role") == "user":
            event = ChatEvent(
                event_type="user_message",
                data={"text": content, "request_index": index}
            )
            self.event_callback(event)
```

**Proceso:**
1. Itera sobre los cambios detectados
2. Crea eventos según el rol del mensaje
3. Llama al callback para emitir el evento
4. El servidor WebSocket transmite a los clientes

## Clases Principales

### KiroParser

```python
class KiroParser:
    def __init__(self):
        self.sessions: Dict[str, Dict] = {}
    
    def parse_file(self, file_path: Path) -> Optional[Dict]:
        """Parsea un archivo JSON de Kiro."""
        # Lee el archivo JSON
        # Extrae session_id del path
        # Retorna dict con messages y metadata
    
    def get_all_messages(self, session_data: Dict) -> List[KiroMessage]:
        """Convierte mensajes a objetos KiroMessage."""
        # Itera sobre messages
        # Crea objetos KiroMessage
        # Retorna lista
    
    def compare_sessions(self, old_data: Optional[Dict], new_data: Dict) -> List[Dict]:
        """Detecta cambios entre dos estados."""
        # Compara longitud de arrays
        # Identifica mensajes nuevos
        # Retorna lista de cambios
```

### KiroMessage

```python
@dataclass
class KiroMessage:
    role: str  # 'user' o 'assistant'
    content: str
    timestamp: float = 0.0
```

## Formato de Datos

### Archivo JSON de Kiro

```json
{
  "messages": [
    {
      "role": "user",
      "content": "Hello"
    },
    {
      "role": "assistant",
      "content": "Hi there!"
    }
  ]
}
```

### Estructura Interna

```python
session_data = {
    "session_id": "abc123",
    "file_path": "/path/to/file.json",
    "messages": [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi!"}
    ],
    "raw_data": {...}  # JSON original completo
}
```

### Eventos Emitidos

```python
# Mensaje del usuario
{
    "event": "user_message",
    "text": "Hello",
    "request_index": 0,
    "timestamp": 1234567890.123
}

# Respuesta del asistente
{
    "event": "response_complete",
    "text": "Hi there!",
    "request_index": 1,
    "timestamp": 1234567890.456
}
```

## Extender el Soporte

### Agregar Nuevo Tipo de Evento

1. Modificar `_poll_kiro_sessions()` en `file_watcher.py`:

```python
def _poll_kiro_sessions(self):
    # ...
    for change in changes:
        if change["type"] == "new_message":
            msg = change["message"]
            
            # Agregar nuevo tipo de evento
            if msg.get("role") == "system":
                event = ChatEvent(
                    event_type="system_message",
                    data={"text": content}
                )
                self.event_callback(event)
```

2. Actualizar la UI para manejar el nuevo evento.

### Agregar Metadata

1. Modificar `parse_file()` en `kiro_parser.py`:

```python
def parse_file(self, file_path: Path) -> Optional[Dict]:
    with open(file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Extraer metadata adicional
    metadata = data.get("metadata", {})
    
    return {
        "session_id": session_id,
        "file_path": str(file_path),
        "messages": messages,
        "metadata": metadata,  # Nueva metadata
        "raw_data": data
    }
```

### Mejorar Detección de Cambios

Actualmente se usa longitud del array. Para mejorar:

```python
def compare_sessions(self, old_data: Optional[Dict], new_data: Dict) -> List[Dict]:
    # Usar hash de contenido
    import hashlib
    
    def hash_message(msg):
        content = json.dumps(msg, sort_keys=True)
        return hashlib.md5(content.encode()).hexdigest()
    
    old_hashes = set(hash_message(m) for m in old_data.get("messages", []))
    new_messages = new_data.get("messages", [])
    
    changes = []
    for i, msg in enumerate(new_messages):
        if hash_message(msg) not in old_hashes:
            changes.append({
                "type": "new_message",
                "index": i,
                "message": msg
            })
    
    return changes
```

## Testing

### Estructura de Tests

```
server/
├── test_kiro.py              # Suite completa
├── test_kiro_simple.py       # Test simple
├── test_e2e_kiro.py          # Test end-to-end
├── demo_kiro.py              # Demo interactiva
└── verify_kiro_integration.py # Verificación
```

### Agregar Nuevo Test

```python
# En test_e2e_kiro.py
def test_nueva_funcionalidad():
    """Test de nueva funcionalidad."""
    print("\nTest X: Nueva funcionalidad")
    print("-" * 50)
    
    # Crear datos de prueba
    messages = [...]
    test_file = create_test_session(messages)
    
    try:
        # Ejecutar test
        parser = KiroParser()
        result = parser.nueva_funcionalidad(test_file)
        
        # Verificar resultado
        assert result is not None, "Result is None"
        
        print("✓ Nueva funcionalidad funciona")
        return True
    except AssertionError as e:
        print(f"✗ Error: {e}")
        return False
    finally:
        test_file.unlink()
```

## Debugging

### Habilitar Logs Detallados

```python
# En file_watcher.py
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Verificar Detección de Archivos

```python
from kiro_parser import get_all_kiro_session_files

files = get_all_kiro_session_files()
print(f"Archivos encontrados: {len(files)}")
for f in files[:5]:
    print(f"  - {f}")
```

### Inspeccionar Sesión

```python
from kiro_parser import KiroParser, find_most_recent_kiro_session

recent = find_most_recent_kiro_session()
parser = KiroParser()
data = parser.parse_file(recent)

print(f"Session ID: {data['session_id']}")
print(f"Messages: {len(data['messages'])}")
for msg in data['messages']:
    print(f"  [{msg['role']}] {msg['content'][:50]}")
```

## Performance

### Optimizaciones Actuales

1. **Cache de Sesiones**: `self.kiro_sessions` evita re-parsear archivos sin cambios
2. **Polling Adaptativo**: Ajusta frecuencia según actividad
3. **Comparación Eficiente**: Solo compara longitud de arrays

### Posibles Mejoras

1. **Hash de Contenido**: Usar MD5 para detectar cambios reales
2. **Índice de Archivos**: Mantener índice de archivos por mtime
3. **Batch Processing**: Procesar múltiples cambios en lote
4. **Async I/O**: Usar asyncio para lectura de archivos

## Compatibilidad

### Windows
✓ Completamente soportado

### Linux/Mac
Requiere ajustar rutas:

```python
# En kiro_parser.py
def find_most_recent_kiro_session() -> Optional[Path]:
    import platform
    
    if platform.system() == "Windows":
        appdata = Path(os.environ.get("APPDATA", ""))
        history_dir = appdata / "Kiro" / "User" / "History"
    else:
        # Linux/Mac
        home = Path.home()
        history_dir = home / ".config" / "Kiro" / "User" / "History"
```

## Recursos

- [Documentación de Integración](KIRO_INTEGRATION.md)
- [Guía de Inicio Rápido](../QUICKSTART_KIRO.md)
- [Resumen de Cambios](../KIRO_CHANGES.md)
- [README Principal](../README.md)
