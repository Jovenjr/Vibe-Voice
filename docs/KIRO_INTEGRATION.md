# IntegraciÃ³n de Kiro

Este documento describe la integración del editor Kiro con Vibe Voice.

## Resumen

Kiro es un editor de cÃ³digo con IA integrada que almacena las conversaciones de chat en formato JSON. A diferencia de VS Code y Cursor que usan formato JSONL incremental, Kiro usa archivos JSON completos.

## UbicaciÃ³n de Datos

**Windows:**
```
%APPDATA%\Kiro\User\History\<session_id>\*.json
```

Cada sesiÃ³n de chat tiene su propia carpeta identificada por un ID Ãºnico (ej: `-1012eccc`). Dentro de cada carpeta hay mÃºltiples archivos JSON, cada uno representando una conversaciÃ³n o estado.

## Formato de Datos

### Estructura del Archivo JSON

```json
{
  "messages": [
    {
      "role": "user",
      "content": "Hello! Can you help me?"
    },
    {
      "role": "assistant",
      "content": "Of course! How can I assist you?"
    }
  ]
}
```

### Campos

- `messages`: Array de mensajes de la conversaciÃ³n
  - `role`: Rol del mensaje (`"user"` o `"assistant"`)
  - `content`: Contenido del mensaje (texto completo)

## Diferencias con Otros Editores

| CaracterÃ­stica | VS Code/Cursor | Kiro |
|---------------|----------------|------|
| Formato | JSONL (JSON Lines) | JSON completo |
| Escritura | Incremental (append) | Archivo completo |
| Operaciones | kind 0/1/2 | Mensajes directos |
| Streaming | SÃ­ (chunks) | No (mensaje completo) |
| UbicaciÃ³n | workspaceStorage | History |

## ImplementaciÃ³n

### Archivos Creados

1. **`server/kiro_parser.py`**
   - Parser especÃ­fico para archivos JSON de Kiro
   - Funciones de detecciÃ³n de sesiones
   - ComparaciÃ³n de estados para detectar cambios

2. **`server/test_kiro.py`**
   - Suite de pruebas para el parser de Kiro
   - VerificaciÃ³n de detecciÃ³n y parsing

3. **`server/demo_kiro.py`**
   - DemostraciÃ³n interactiva del soporte de Kiro
   - Ejemplos de uso

### Archivos Modificados

1. **`server/jsonl_parser.py`**
   - Agregado `"kiro"` a `SUPPORTED_IDES`
   - Agregado flag `include_kiro` a configuraciones

2. **`server/file_watcher.py`**
   - Importado `KiroParser`
   - Agregado soporte de monitoreo de directorios de Kiro
   - Implementado `_poll_kiro_sessions()` para polling de archivos JSON
   - Modificado `_load_current_session()` para cargar sesiones de Kiro

3. **`README.md`**
   - DocumentaciÃ³n de soporte de Kiro
   - Instrucciones de uso
   - Ejemplos de comandos

## Uso

### Iniciar el Servidor

```bash
# Monitorear solo Kiro
python server/main.py --ide kiro

# Monitorear todos los editores (incluye Kiro)
python server/main.py --ide all
```

### Cambiar IDE desde la UI

La interfaz web incluye un selector de IDE que permite cambiar entre:
- Todos
- VS Code
- VS Code Insiders
- Cursor
- Kiro

## Funcionamiento

### DetecciÃ³n de Sesiones

1. El sistema escanea `%APPDATA%\Kiro\User\History\`
2. Busca todas las carpetas de sesiÃ³n
3. Dentro de cada carpeta, busca archivos `*.json`
4. Identifica el archivo mÃ¡s reciente por `mtime`

### Monitoreo en Tiempo Real

1. **Polling**: Cada 100ms, el sistema verifica el archivo mÃ¡s reciente
2. **Parsing**: Lee y parsea el archivo JSON completo
3. **ComparaciÃ³n**: Compara con el estado anterior en cache
4. **DetecciÃ³n de Cambios**: Identifica mensajes nuevos
5. **Eventos**: Emite eventos WebSocket para la UI

### Eventos Emitidos

- `user_message`: Cuando se detecta un nuevo mensaje del usuario
- `response_complete`: Cuando se detecta una respuesta del asistente
- `session_changed`: Cuando cambia la sesiÃ³n activa

## Pruebas

### Ejecutar Pruebas

```bash
# Suite completa de pruebas
python server/test_kiro.py

# Prueba simple con archivo especÃ­fico
python server/test_kiro_simple.py

# Demo interactiva
python server/demo_kiro.py
```

### Verificar DetecciÃ³n

```bash
# Verificar que Kiro estÃ¡ instalado y tiene sesiones
python -c "from server.kiro_parser import get_all_kiro_session_files; print(f'Sesiones: {len(get_all_kiro_session_files())}')"
```

## Limitaciones Conocidas

1. **Sin Timestamps**: Los archivos JSON de Kiro no incluyen timestamps en los mensajes
2. **Sin Streaming**: Los mensajes aparecen completos, no hay streaming de chunks
3. **DetecciÃ³n de Cambios**: Se basa en comparaciÃ³n de longitud del array de mensajes
4. **Formato de Archivo**: Solo soporta el formato actual de Kiro (puede cambiar en futuras versiones)

## SoluciÃ³n de Problemas

### No se detectan sesiones de Kiro

1. Verificar que Kiro estÃ¡ instalado
2. Verificar que has usado el chat al menos una vez
3. Verificar la ruta: `%APPDATA%\Kiro\User\History\`

### Los mensajes no aparecen en tiempo real

1. Verificar que el filtro de IDE estÃ¡ en "kiro" o "all"
2. Verificar que el archivo JSON se estÃ¡ actualizando
3. Revisar los logs del servidor para errores

### Error al parsear archivos

1. Verificar que el archivo es JSON vÃ¡lido
2. Verificar que tiene la estructura esperada (`messages` array)
3. Revisar los logs para detalles del error

## Desarrollo Futuro

Posibles mejoras:

1. **Soporte de Timestamps**: Agregar timestamps basados en `mtime` del archivo
2. **MÃºltiples Sesiones**: Monitorear mÃºltiples sesiones simultÃ¡neamente
3. **Historial Completo**: Cargar y mostrar historial completo de sesiones
4. **DetecciÃ³n Mejorada**: Usar hash de contenido para detectar cambios mÃ¡s precisos
5. **Soporte de Metadata**: Parsear metadata adicional si Kiro la agrega en el futuro

## Referencias

- [Kiro Editor](https://kiro.ai)
- [Formato JSON](https://www.json.org/)
- [DocumentaciÃ³n del Proyecto](../README.md)


