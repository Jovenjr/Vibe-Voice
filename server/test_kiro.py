"""
Script de prueba para verificar el soporte de Kiro.
"""

import sys
from pathlib import Path

# Agregar el directorio server al path
sys.path.insert(0, str(Path(__file__).parent))

from kiro_parser import KiroParser, find_most_recent_kiro_session, get_all_kiro_session_files

def test_kiro_detection():
    """Prueba la detección de sesiones de Kiro."""
    print("=" * 60)
    print("TEST: Detección de sesiones de Kiro")
    print("=" * 60)
    
    # Buscar todas las sesiones
    all_files = get_all_kiro_session_files()
    print(f"\n✓ Sesiones encontradas: {len(all_files)}")
    
    if all_files:
        print("\nPrimeras 5 sesiones:")
        for i, file in enumerate(all_files[:5]):
            print(f"  {i+1}. {file.parent.name}/{file.name}")
    
    # Buscar la más reciente
    recent = find_most_recent_kiro_session()
    if recent:
        print(f"\n✓ Sesión más reciente: {recent.parent.name}/{recent.name}")
    else:
        print("\n✗ No se encontró sesión reciente")
    
    return recent

def test_kiro_parser(file_path):
    """Prueba el parser de Kiro."""
    print("\n" + "=" * 60)
    print("TEST: Parser de Kiro")
    print("=" * 60)
    
    parser = KiroParser()
    
    # Parsear el archivo
    session_data = parser.parse_file(file_path)
    
    if not session_data:
        print("\n✗ Error parseando el archivo")
        return
    
    print(f"\n✓ Sesión parseada correctamente")
    print(f"  Session ID: {session_data['session_id']}")
    print(f"  Mensajes: {len(session_data['messages'])}")
    
    # Obtener mensajes
    messages = parser.get_all_messages(session_data)
    print(f"\n✓ Mensajes extraídos: {len(messages)}")
    
    if messages:
        print("\nPrimeros 3 mensajes:")
        for i, msg in enumerate(messages[:3]):
            content_preview = msg.content[:60] + "..." if len(msg.content) > 60 else msg.content
            print(f"  {i+1}. [{msg.role}] {content_preview}")
    
    # Probar comparación de sesiones
    print("\n" + "=" * 60)
    print("TEST: Detección de cambios")
    print("=" * 60)
    
    # Simular cambio agregando un mensaje
    old_data = session_data.copy()
    old_data['messages'] = session_data['messages'][:-1] if len(session_data['messages']) > 1 else []
    
    changes = parser.compare_sessions(old_data, session_data)
    print(f"\n✓ Cambios detectados: {len(changes)}")
    
    if changes:
        print("\nCambios:")
        for change in changes:
            print(f"  - Tipo: {change['type']}, Índice: {change['index']}")

def main():
    """Función principal."""
    print("\n" + "=" * 60)
    print("PRUEBA DE SOPORTE DE KIRO")
    print("=" * 60)
    
    # Test 1: Detección
    recent_file = test_kiro_detection()
    
    if not recent_file:
        print("\n⚠ No se encontraron sesiones de Kiro para probar")
        print("  Asegúrate de tener Kiro instalado y haber usado el chat")
        return
    
    # Test 2: Parser
    test_kiro_parser(recent_file)
    
    print("\n" + "=" * 60)
    print("✓ TODAS LAS PRUEBAS COMPLETADAS")
    print("=" * 60)

if __name__ == "__main__":
    main()
