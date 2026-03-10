"""
Demo del soporte de Kiro en Vibe Voice.

Este script demuestra cÃ³mo el sistema detecta y procesa sesiones de Kiro.
"""

import time
from kiro_parser import KiroParser, find_most_recent_kiro_session, get_all_kiro_session_files
from platform_paths import get_kiro_history_dir

def print_header(text):
    """Imprime un encabezado formateado."""
    print("\n" + "=" * 70)
    print(f"  {text}")
    print("=" * 70)

def demo_detection():
    """Demuestra la detecciÃ³n de sesiones de Kiro."""
    print_header("1. DETECCIÃ“N DE SESIONES DE KIRO")
    
    history_dir = get_kiro_history_dir()
    print(f"\nDirectorio de historial: {history_dir}")
    print(f"Existe: {history_dir.exists()}")
    
    if not history_dir.exists():
        print("\nâš  Kiro no estÃ¡ instalado o no se ha usado el chat")
        return None
    
    # Obtener todas las sesiones
    all_files = get_all_kiro_session_files()
    print(f"\nâœ“ Total de archivos JSON encontrados: {len(all_files)}")
    
    # Contar sesiones Ãºnicas (por carpeta)
    sessions = set(f.parent.name for f in all_files)
    print(f"âœ“ Sesiones Ãºnicas: {len(sessions)}")
    
    # Mostrar algunas sesiones
    if sessions:
        print("\nPrimeras 5 sesiones:")
        for i, session_id in enumerate(list(sessions)[:5]):
            session_dir = history_dir / session_id
            files = list(session_dir.glob("*.json"))
            print(f"  {i+1}. {session_id} ({len(files)} archivos)")
    
    # Encontrar la mÃ¡s reciente
    recent = find_most_recent_kiro_session()
    if recent:
        print(f"\nâœ“ SesiÃ³n mÃ¡s reciente:")
        print(f"  Archivo: {recent.name}")
        print(f"  SesiÃ³n: {recent.parent.name}")
        print(f"  Modificado: {time.ctime(recent.stat().st_mtime)}")
        return recent
    else:
        print("\nâš  No se encontrÃ³ sesiÃ³n reciente")
        return None

def demo_parsing(file_path):
    """Demuestra el parsing de una sesiÃ³n de Kiro."""
    print_header("2. PARSING DE SESIÃ“N")
    
    parser = KiroParser()
    
    print(f"\nParseando: {file_path.name}")
    session_data = parser.parse_file(file_path)
    
    if not session_data:
        print("âœ— Error parseando el archivo")
        return None
    
    print(f"\nâœ“ SesiÃ³n parseada correctamente")
    print(f"  Session ID: {session_data['session_id']}")
    print(f"  Archivo: {session_data['file_path']}")
    print(f"  Mensajes: {len(session_data['messages'])}")
    
    return session_data

def demo_messages(session_data):
    """Demuestra la extracciÃ³n de mensajes."""
    print_header("3. EXTRACCIÃ“N DE MENSAJES")
    
    parser = KiroParser()
    messages = parser.get_all_messages(session_data)
    
    print(f"\nâœ“ Mensajes extraÃ­dos: {len(messages)}")
    
    if not messages:
        print("\nâš  No hay mensajes en esta sesiÃ³n")
        return
    
    print("\nConversaciÃ³n:")
    print("-" * 70)
    
    for i, msg in enumerate(messages):
        role_label = "ðŸ‘¤ Usuario" if msg.role == "user" else "ðŸ¤– Asistente"
        print(f"\n{role_label}:")
        
        # Mostrar contenido con lÃ­mite
        content = msg.content
        if len(content) > 200:
            content = content[:200] + "..."
        
        # Indentar el contenido
        for line in content.split('\n'):
            print(f"  {line}")
    
    print("\n" + "-" * 70)

def demo_change_detection(session_data):
    """Demuestra la detecciÃ³n de cambios."""
    print_header("4. DETECCIÃ“N DE CAMBIOS")
    
    parser = KiroParser()
    
    # Simular estado anterior (sin el Ãºltimo mensaje)
    old_data = session_data.copy()
    old_messages = session_data['messages'][:-1] if len(session_data['messages']) > 1 else []
    old_data['messages'] = old_messages
    
    print(f"\nEstado anterior: {len(old_messages)} mensajes")
    print(f"Estado actual: {len(session_data['messages'])} mensajes")
    
    # Detectar cambios
    changes = parser.compare_sessions(old_data, session_data)
    
    print(f"\nâœ“ Cambios detectados: {len(changes)}")
    
    if changes:
        print("\nDetalles de cambios:")
        for change in changes:
            msg = change['message']
            role = msg.get('role', 'unknown')
            content_preview = msg.get('content', '')[:50] + "..."
            print(f"  - Nuevo mensaje [{role}] en Ã­ndice {change['index']}")
            print(f"    {content_preview}")

def demo_monitoring():
    """Demuestra cÃ³mo se monitorearÃ­a en tiempo real."""
    print_header("5. MONITOREO EN TIEMPO REAL")
    
    print("\nEn el servidor principal, el sistema:")
    print("  1. Detecta el archivo JSON mÃ¡s reciente de Kiro")
    print("  2. Lee el contenido inicial y lo cachea")
    print("  3. Hace polling cada 100ms para detectar cambios")
    print("  4. Cuando detecta cambios:")
    print("     - Parsea el archivo actualizado")
    print("     - Compara con el estado anterior")
    print("     - Emite eventos WebSocket para mensajes nuevos")
    print("  5. Los clientes reciben los mensajes en tiempo real")
    
    print("\nEventos emitidos:")
    print("  - user_message: Cuando el usuario envÃ­a un mensaje")
    print("  - response_complete: Cuando el asistente responde")
    print("  - session_changed: Cuando cambia la sesiÃ³n activa")

def main():
    """FunciÃ³n principal de la demo."""
    print("\n" + "=" * 70)
    print("  DEMO: SOPORTE DE KIRO EN COPILOT REALTIME VIEWER")
    print("=" * 70)
    
    # 1. DetecciÃ³n
    recent_file = demo_detection()
    
    if not recent_file:
        print("\nâš  No se puede continuar sin sesiones de Kiro")
        print("  Usa Kiro y envÃ­a algunos mensajes al chat para probar")
        return
    
    # 2. Parsing
    session_data = demo_parsing(recent_file)
    
    if not session_data:
        print("\nâš  No se pudo parsear la sesiÃ³n")
        return
    
    # 3. Mensajes
    demo_messages(session_data)
    
    # 4. DetecciÃ³n de cambios
    if len(session_data['messages']) > 0:
        demo_change_detection(session_data)
    
    # 5. Monitoreo
    demo_monitoring()
    
    print_header("âœ“ DEMO COMPLETADA")
    print("\nPara usar el soporte de Kiro:")
    print("  1. Inicia el servidor: python server/main.py --ide kiro")
    print("  2. Abre la UI en tu navegador")
    print("  3. Usa Kiro y observa los mensajes en tiempo real")
    print("\n" + "=" * 70)

if __name__ == "__main__":
    main()
