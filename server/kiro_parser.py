"""
kiro_parser.py
==============
Parser para el formato JSON de Kiro chat sessions.

Kiro almacena las sesiones de chat en formato JSON completo (no JSONL incremental).
Ubicacion: %APPDATA%\\Kiro\\User\\History\\<session_id>\\*.json
"""

import json
from pathlib import Path
from typing import Optional, Dict, List, Any
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class KiroMessage:
    """Representa un mensaje en el chat de Kiro."""
    role: str  # 'user' o 'assistant'
    content: str
    timestamp: float = 0.0


class KiroParser:
    """Parser para archivos JSON de sesiones de chat de Kiro."""
    
    def __init__(self):
        self.sessions: Dict[str, Dict] = {}
    
    def parse_file(self, file_path: Path) -> Optional[Dict]:
        """
        Parsea un archivo JSON de Kiro y retorna los datos de la sesión.
        
        Returns:
            Dict con 'messages' y 'session_id'
        """
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            if not isinstance(data, dict):
                logger.warning(f"[Kiro] Archivo no es un dict: {file_path}")
                return None
            
            messages = data.get("messages", [])
            if not isinstance(messages, list):
                logger.warning(f"[Kiro] 'messages' no es una lista: {file_path}")
                return None
            
            # Extraer session_id del path (nombre de la carpeta padre)
            session_id = file_path.parent.name
            
            return {
                "session_id": session_id,
                "file_path": str(file_path),
                "messages": messages,
                "raw_data": data
            }
        
        except json.JSONDecodeError as e:
            logger.error(f"[Kiro] Error JSON en {file_path}: {e}")
            return None
        except Exception as e:
            logger.error(f"[Kiro] Error leyendo {file_path}: {e}")
            return None
    
    def get_all_messages(self, session_data: Dict) -> List[KiroMessage]:
        """Convierte los mensajes de Kiro a objetos KiroMessage."""
        messages = []
        
        for msg in session_data.get("messages", []):
            if not isinstance(msg, dict):
                continue
            
            role = msg.get("role", "")
            content = msg.get("content", "")
            
            if role and content:
                messages.append(KiroMessage(
                    role=role,
                    content=content,
                    timestamp=0.0  # Kiro no incluye timestamps en el formato actual
                ))
        
        return messages
    
    def compare_sessions(self, old_data: Optional[Dict], new_data: Dict) -> List[Dict]:
        """
        Compara dos estados de sesión y retorna los cambios (mensajes nuevos).
        
        Returns:
            Lista de cambios con tipo 'new_message'
        """
        changes = []
        
        if not old_data:
            # Primera carga: todos los mensajes son nuevos
            for i, msg in enumerate(new_data.get("messages", [])):
                changes.append({
                    "type": "new_message",
                    "index": i,
                    "message": msg
                })
            return changes
        
        old_messages = old_data.get("messages", [])
        new_messages = new_data.get("messages", [])
        
        # Detectar mensajes nuevos (comparando longitud)
        if len(new_messages) > len(old_messages):
            for i in range(len(old_messages), len(new_messages)):
                changes.append({
                    "type": "new_message",
                    "index": i,
                    "message": new_messages[i]
                })
        
        return changes


def find_most_recent_kiro_session() -> Optional[Path]:
    """
    Busca el archivo JSON de sesión más reciente de Kiro.
    
    Returns:
        Path al archivo JSON más reciente o None
    """
    import os
    
    appdata = Path(os.environ.get("APPDATA", ""))
    history_dir = appdata / "Kiro" / "User" / "History"
    
    if not history_dir.exists():
        logger.debug(f"[Kiro] No existe directorio: {history_dir}")
        return None
    
    best_file = None
    best_mtime = 0
    
    # Buscar en todas las carpetas de sesión
    for session_dir in history_dir.iterdir():
        if not session_dir.is_dir():
            continue
        
        # Buscar archivos JSON en la sesión
        for json_file in session_dir.glob("*.json"):
            try:
                mtime = json_file.stat().st_mtime
                if mtime > best_mtime:
                    best_mtime = mtime
                    best_file = json_file
            except Exception as e:
                logger.debug(f"[Kiro] Error stat {json_file}: {e}")
                continue
    
    if best_file:
        logger.info(f"[Kiro] Sesión más reciente: {best_file}")
    else:
        logger.debug(f"[Kiro] No se encontraron sesiones en {history_dir}")
    
    return best_file


def get_all_kiro_session_files() -> List[Path]:
    """
    Obtiene todos los archivos JSON de sesiones de Kiro.
    
    Returns:
        Lista de Paths a archivos JSON
    """
    import os
    
    appdata = Path(os.environ.get("APPDATA", ""))
    history_dir = appdata / "Kiro" / "User" / "History"
    
    if not history_dir.exists():
        return []
    
    files = []
    for session_dir in history_dir.iterdir():
        if not session_dir.is_dir():
            continue
        
        for json_file in session_dir.glob("*.json"):
            files.append(json_file)
    
    return files
