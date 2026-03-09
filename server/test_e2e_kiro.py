"""
Test End-to-End del soporte de Kiro.
Simula el flujo completo desde detección hasta emisión de eventos.
"""

import json
import tempfile
from pathlib import Path
from kiro_parser import KiroParser
from file_watcher import ChatEvent

def create_test_session(messages):
    """Crea un archivo de sesión de prueba."""
    temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
    json.dump({"messages": messages}, temp_file)
    temp_file.close()
    return Path(temp_file.name)

def test_basic_parsing():
    """Test básico de parsing."""
    print("Test 1: Parsing básico")
    print("-" * 50)
    
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"}
    ]
    
    test_file = create_test_session(messages)
    
    try:
        parser = KiroParser()
        session_data = parser.parse_file(test_file)
        
        assert session_data is not None, "Session data is None"
        assert len(session_data['messages']) == 2, f"Expected 2 messages, got {len(session_data['messages'])}"
        
        msgs = parser.get_all_messages(session_data)
        assert len(msgs) == 2, f"Expected 2 parsed messages, got {len(msgs)}"
        assert msgs[0].role == "user", f"Expected user, got {msgs[0].role}"
        assert msgs[1].role == "assistant", f"Expected assistant, got {msgs[1].role}"
        
        print("✓ Parsing básico funciona correctamente")
        return True
    except AssertionError as e:
        print(f"✗ Error: {e}")
        return False
    finally:
        test_file.unlink()

def test_change_detection():
    """Test de detección de cambios."""
    print("\nTest 2: Detección de cambios")
    print("-" * 50)
    
    # Estado inicial
    messages_v1 = [
        {"role": "user", "content": "Hello"}
    ]
    
    # Estado actualizado
    messages_v2 = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi!"}
    ]
    
    test_file_v1 = create_test_session(messages_v1)
    test_file_v2 = create_test_session(messages_v2)
    
    try:
        parser = KiroParser()
        
        # Parsear ambas versiones
        session_v1 = parser.parse_file(test_file_v1)
        session_v2 = parser.parse_file(test_file_v2)
        
        # Detectar cambios
        changes = parser.compare_sessions(session_v1, session_v2)
        
        assert len(changes) == 1, f"Expected 1 change, got {len(changes)}"
        assert changes[0]['type'] == 'new_message', f"Expected new_message, got {changes[0]['type']}"
        assert changes[0]['message']['role'] == 'assistant', "Expected assistant message"
        
        print("✓ Detección de cambios funciona correctamente")
        return True
    except AssertionError as e:
        print(f"✗ Error: {e}")
        return False
    finally:
        test_file_v1.unlink()
        test_file_v2.unlink()

def test_empty_session():
    """Test con sesión vacía."""
    print("\nTest 3: Sesión vacía")
    print("-" * 50)
    
    messages = []
    test_file = create_test_session(messages)
    
    try:
        parser = KiroParser()
        session_data = parser.parse_file(test_file)
        
        assert session_data is not None, "Session data is None"
        assert len(session_data['messages']) == 0, f"Expected 0 messages, got {len(session_data['messages'])}"
        
        msgs = parser.get_all_messages(session_data)
        assert len(msgs) == 0, f"Expected 0 parsed messages, got {len(msgs)}"
        
        print("✓ Sesión vacía manejada correctamente")
        return True
    except AssertionError as e:
        print(f"✗ Error: {e}")
        return False
    finally:
        test_file.unlink()

def test_multiple_messages():
    """Test con múltiples mensajes."""
    print("\nTest 4: Múltiples mensajes")
    print("-" * 50)
    
    messages = [
        {"role": "user", "content": "Message 1"},
        {"role": "assistant", "content": "Response 1"},
        {"role": "user", "content": "Message 2"},
        {"role": "assistant", "content": "Response 2"},
        {"role": "user", "content": "Message 3"},
    ]
    
    test_file = create_test_session(messages)
    
    try:
        parser = KiroParser()
        session_data = parser.parse_file(test_file)
        
        assert len(session_data['messages']) == 5, f"Expected 5 messages, got {len(session_data['messages'])}"
        
        msgs = parser.get_all_messages(session_data)
        assert len(msgs) == 5, f"Expected 5 parsed messages, got {len(msgs)}"
        
        # Verificar alternancia de roles
        expected_roles = ["user", "assistant", "user", "assistant", "user"]
        actual_roles = [msg.role for msg in msgs]
        assert actual_roles == expected_roles, f"Expected {expected_roles}, got {actual_roles}"
        
        print("✓ Múltiples mensajes manejados correctamente")
        return True
    except AssertionError as e:
        print(f"✗ Error: {e}")
        return False
    finally:
        test_file.unlink()

def test_event_generation():
    """Test de generación de eventos."""
    print("\nTest 5: Generación de eventos")
    print("-" * 50)
    
    messages = [
        {"role": "user", "content": "Test message"}
    ]
    
    test_file = create_test_session(messages)
    
    try:
        parser = KiroParser()
        session_data = parser.parse_file(test_file)
        
        # Simular detección de cambio
        changes = parser.compare_sessions(None, session_data)
        
        assert len(changes) == 1, f"Expected 1 change, got {len(changes)}"
        
        # Simular creación de evento
        change = changes[0]
        msg = change['message']
        
        event = ChatEvent(
            event_type="user_message",
            data={
                "text": msg['content'],
                "request_index": change['index']
            }
        )
        
        assert event.event_type == "user_message", f"Expected user_message, got {event.event_type}"
        assert event.data['text'] == "Test message", f"Expected 'Test message', got {event.data['text']}"
        
        # Verificar serialización
        event_dict = event.to_dict()
        assert 'event' in event_dict, "Missing 'event' in dict"
        assert 'timestamp' in event_dict, "Missing 'timestamp' in dict"
        assert 'text' in event_dict, "Missing 'text' in dict"
        
        print("✓ Generación de eventos funciona correctamente")
        return True
    except AssertionError as e:
        print(f"✗ Error: {e}")
        return False
    finally:
        test_file.unlink()

def main():
    """Ejecuta todos los tests."""
    print("=" * 70)
    print("  TEST END-TO-END: SOPORTE DE KIRO")
    print("=" * 70)
    
    tests = [
        test_basic_parsing,
        test_change_detection,
        test_empty_session,
        test_multiple_messages,
        test_event_generation
    ]
    
    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"\n✗ Error inesperado: {e}")
            results.append(False)
    
    print("\n" + "=" * 70)
    print("  RESUMEN")
    print("=" * 70)
    
    passed = sum(results)
    total = len(results)
    
    print(f"\nTests pasados: {passed}/{total}")
    
    if passed == total:
        print("\n✓ TODOS LOS TESTS PASARON")
        print("\nEl soporte de Kiro está completamente funcional.")
    else:
        print("\n⚠ ALGUNOS TESTS FALLARON")
        print("\nRevisa los errores arriba.")
    
    print("=" * 70)
    
    return 0 if passed == total else 1

if __name__ == "__main__":
    import sys
    sys.exit(main())
