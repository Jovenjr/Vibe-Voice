"""
Diagnóstico: ¿VS Code escribe al archivo JSONL en tiempo real?

Este script monitorea el archivo JSONL de VS Code directamente para ver
si cambia cuando Copilot responde, o si solo cambia cuando hay interacción.

Uso:
    python diagnose_vscode.py
    
Luego haz una pregunta en VS Code Copilot y observa si este script
detecta el cambio ANTES de que hagas clic en cualquier ventana.
"""

import os
import sys
import time
import hashlib
from pathlib import Path

# Buscar el archivo más reciente de VS Code
def find_vscode_file():
    appdata = Path(os.environ.get("APPDATA", ""))
    
    for folder in ["Code - Insiders", "Code"]:
        ws_storage = appdata / folder / "User" / "workspaceStorage"
        if not ws_storage.exists():
            continue
        
        best_file = None
        best_mtime = 0
        
        for ws_dir in ws_storage.iterdir():
            if not ws_dir.is_dir():
                continue
            chat_sessions = ws_dir / "chatSessions"
            if not chat_sessions.exists():
                continue
            for jsonl_file in chat_sessions.glob("*.jsonl"):
                mtime = jsonl_file.stat().st_mtime
                if mtime > best_mtime:
                    best_mtime = mtime
                    best_file = jsonl_file
        
        if best_file:
            return best_file, folder
    
    return None, None

def get_file_state(file_path):
    """Obtiene el estado actual del archivo."""
    try:
        with open(file_path, 'rb') as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 512))
            tail = f.read()
        
        stat = file_path.stat()
        return {
            'size': size,
            'mtime': stat.st_mtime,
            'mtime_ns': stat.st_mtime_ns,
            'tail_hash': hashlib.md5(tail).hexdigest()[:8]
        }
    except Exception as e:
        return {'error': str(e)}

def main():
    print("=" * 60)
    print("DIAGNÓSTICO: Detección de cambios en VS Code")
    print("=" * 60)
    
    file_path, ide = find_vscode_file()
    
    if not file_path:
        print("ERROR: No se encontró ningún archivo JSONL de VS Code")
        return
    
    print(f"\nArchivo: {file_path.name}")
    print(f"IDE: {ide}")
    print(f"Ruta completa: {file_path}")
    print()
    print("Instrucciones:")
    print("1. Haz una pregunta en VS Code Copilot Chat")
    print("2. NO hagas clic en ninguna ventana")
    print("3. Observa si este script detecta cambios")
    print()
    print("-" * 60)
    print("Monitoreando... (Ctrl+C para salir)")
    print("-" * 60)
    
    last_state = get_file_state(file_path)
    print(f"Estado inicial: size={last_state.get('size')}, hash={last_state.get('tail_hash')}")
    
    poll_count = 0
    while True:
        time.sleep(0.1)  # Poll cada 100ms
        poll_count += 1
        
        current_state = get_file_state(file_path)
        
        if current_state.get('error'):
            print(f"[{poll_count}] Error: {current_state['error']}")
            continue
        
        # Detectar cambios
        size_changed = current_state['size'] != last_state.get('size')
        mtime_changed = current_state['mtime_ns'] != last_state.get('mtime_ns')
        hash_changed = current_state['tail_hash'] != last_state.get('tail_hash')
        
        if size_changed or mtime_changed or hash_changed:
            now = time.strftime("%H:%M:%S")
            changes = []
            if size_changed:
                changes.append(f"size: {last_state.get('size')} → {current_state['size']}")
            if mtime_changed:
                changes.append("mtime cambió")
            if hash_changed:
                changes.append(f"hash: {last_state.get('tail_hash')} → {current_state['tail_hash']}")
            
            print(f"[{now}] CAMBIO DETECTADO: {', '.join(changes)}")
            last_state = current_state
        
        # Mostrar heartbeat cada 10 segundos
        if poll_count % 100 == 0:
            print(f"[{time.strftime('%H:%M:%S')}] ... polling activo ({poll_count} iteraciones)")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nDetenido.")
