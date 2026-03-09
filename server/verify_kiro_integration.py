"""
Script de verificación de la integración de Kiro.
Verifica que todos los componentes funcionen correctamente.
"""

import sys
from pathlib import Path

def test_imports():
    """Verifica que todos los imports funcionen."""
    print("1. Verificando imports...")
    try:
        from kiro_parser import KiroParser, find_most_recent_kiro_session, get_all_kiro_session_files
        from jsonl_parser import SUPPORTED_IDES
        from file_watcher import CopilotChatWatcher
        print("   ✓ Todos los imports exitosos")
        return True
    except Exception as e:
        print(f"   ✗ Error en imports: {e}")
        return False

def test_supported_ides():
    """Verifica que Kiro esté en la lista de IDEs soportados."""
    print("\n2. Verificando IDEs soportados...")
    try:
        from jsonl_parser import SUPPORTED_IDES
        
        if "kiro" not in SUPPORTED_IDES:
            print("   ✗ Kiro no está en SUPPORTED_IDES")
            return False
        
        kiro_config = SUPPORTED_IDES["kiro"]
        print(f"   ✓ Kiro encontrado: {kiro_config['name']}")
        
        # Verificar que 'all' incluye Kiro
        all_config = SUPPORTED_IDES["all"]
        if not all_config.get("include_kiro", False):
            print("   ✗ 'all' no incluye Kiro")
            return False
        
        print("   ✓ 'all' incluye Kiro")
        return True
    except Exception as e:
        print(f"   ✗ Error: {e}")
        return False

def test_kiro_detection():
    """Verifica que se puedan detectar sesiones de Kiro."""
    print("\n3. Verificando detección de sesiones...")
    try:
        from kiro_parser import get_all_kiro_session_files, find_most_recent_kiro_session
        
        all_files = get_all_kiro_session_files()
        print(f"   ✓ Archivos encontrados: {len(all_files)}")
        
        recent = find_most_recent_kiro_session()
        if recent:
            print(f"   ✓ Sesión más reciente: {recent.parent.name}/{recent.name}")
        else:
            print("   ⚠ No se encontró sesión reciente (esto es normal si no has usado Kiro)")
        
        return True
    except Exception as e:
        print(f"   ✗ Error: {e}")
        return False

def test_kiro_parser():
    """Verifica que el parser de Kiro funcione."""
    print("\n4. Verificando parser de Kiro...")
    try:
        from kiro_parser import KiroParser, find_most_recent_kiro_session
        
        recent = find_most_recent_kiro_session()
        if not recent:
            print("   ⚠ No hay sesiones para parsear")
            return True
        
        parser = KiroParser()
        session_data = parser.parse_file(recent)
        
        if not session_data:
            print("   ✗ Error parseando sesión")
            return False
        
        print(f"   ✓ Sesión parseada: {len(session_data['messages'])} mensajes")
        
        # Probar extracción de mensajes
        messages = parser.get_all_messages(session_data)
        print(f"   ✓ Mensajes extraídos: {len(messages)}")
        
        # Probar detección de cambios
        changes = parser.compare_sessions(None, session_data)
        print(f"   ✓ Detección de cambios funciona: {len(changes)} cambios")
        
        return True
    except Exception as e:
        print(f"   ✗ Error: {e}")
        return False

def test_file_watcher_integration():
    """Verifica que el file watcher soporte Kiro."""
    print("\n5. Verificando integración con file_watcher...")
    try:
        from file_watcher import CopilotChatWatcher
        
        # Crear un watcher con filtro de Kiro
        def dummy_callback(event):
            pass
        
        watcher = CopilotChatWatcher(dummy_callback, ide_filter="kiro")
        print("   ✓ Watcher creado con filtro 'kiro'")
        
        # Verificar que tiene el parser de Kiro
        if not hasattr(watcher, 'kiro_parser'):
            print("   ✗ Watcher no tiene kiro_parser")
            return False
        
        print("   ✓ Watcher tiene kiro_parser")
        
        # Verificar que tiene cache de sesiones
        if not hasattr(watcher, 'kiro_sessions'):
            print("   ✗ Watcher no tiene kiro_sessions")
            return False
        
        print("   ✓ Watcher tiene kiro_sessions")
        
        return True
    except Exception as e:
        print(f"   ✗ Error: {e}")
        return False

def main():
    """Función principal."""
    print("=" * 70)
    print("  VERIFICACIÓN DE INTEGRACIÓN DE KIRO")
    print("=" * 70)
    
    tests = [
        test_imports,
        test_supported_ides,
        test_kiro_detection,
        test_kiro_parser,
        test_file_watcher_integration
    ]
    
    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"\n✗ Error ejecutando test: {e}")
            results.append(False)
    
    print("\n" + "=" * 70)
    print("  RESUMEN")
    print("=" * 70)
    
    passed = sum(results)
    total = len(results)
    
    print(f"\nTests pasados: {passed}/{total}")
    
    if passed == total:
        print("\n✓ TODAS LAS VERIFICACIONES PASARON")
        print("\nLa integración de Kiro está completa y funcional.")
        print("\nPara usar:")
        print("  python server/main.py --ide kiro")
    else:
        print("\n⚠ ALGUNAS VERIFICACIONES FALLARON")
        print("\nRevisa los errores arriba para más detalles.")
    
    print("=" * 70)
    
    return 0 if passed == total else 1

if __name__ == "__main__":
    sys.exit(main())
