# Inicio RÃ¡pido - Soporte de Kiro

Guía rápida para usar Vibe Voice con Kiro.

## Requisitos Previos

1. Tener Kiro instalado
2. Haber usado el chat de Kiro al menos una vez
3. Python 3.7+ instalado

## InstalaciÃ³n

```bash
# 1. Instalar dependencias
cd server
pip install -r requirements.txt
```

## VerificaciÃ³n

```bash
# Verificar que Kiro estÃ¡ detectado
python server/verify_kiro_integration.py
```

DeberÃ­as ver:
```
âœ“ TODAS LAS VERIFICACIONES PASARON
```

## Uso

### OpciÃ³n 1: Solo Kiro

```bash
# Iniciar servidor monitoreando solo Kiro
python server/main.py --ide kiro
```

### OpciÃ³n 2: Todos los Editores

```bash
# Iniciar servidor monitoreando todos los editores (incluye Kiro)
python server/main.py --ide all
```

### Abrir la UI

1. Abre `ui/index.html` en tu navegador
2. O usa un servidor web local:
   ```bash
   cd ui
   python -m http.server 3000
   ```
   Luego abre: http://localhost:3000

## Probar

1. Abre Kiro
2. Inicia una conversaciÃ³n con el asistente
3. Observa los mensajes aparecer en tiempo real en la UI

## Demo

Para ver una demostraciÃ³n del soporte de Kiro:

```bash
python server/demo_kiro.py
```

## SoluciÃ³n de Problemas

### No se detectan sesiones

```bash
# Verificar ubicaciÃ³n de datos
echo %APPDATA%\Kiro\User\History
```

AsegÃºrate de que esta carpeta existe y tiene archivos JSON.

### Los mensajes no aparecen

1. Verifica que el servidor estÃ¡ corriendo
2. Verifica que el filtro de IDE estÃ¡ en "kiro" o "all"
3. Revisa los logs del servidor

### Error al iniciar

```bash
# Verificar imports
python server/test_imports.py
```

## MÃ¡s InformaciÃ³n

- DocumentaciÃ³n completa: `docs/KIRO_INTEGRATION.md`
- Resumen de cambios: `KIRO_CHANGES.md`
- README principal: `README.md`

## Comandos Ãštiles

```bash
# Ver todas las sesiones de Kiro
python -c "from server.kiro_parser import get_all_kiro_session_files; print(f'Sesiones: {len(get_all_kiro_session_files())}')"

# Ver sesiÃ³n mÃ¡s reciente
python -c "from server.kiro_parser import find_most_recent_kiro_session; print(find_most_recent_kiro_session())"

# Probar parser
python server/test_kiro_simple.py
```

## Soporte

Si encuentras problemas:
1. Ejecuta `python server/verify_kiro_integration.py`
2. Revisa los logs del servidor
3. Consulta `docs/KIRO_INTEGRATION.md` para mÃ¡s detalles


