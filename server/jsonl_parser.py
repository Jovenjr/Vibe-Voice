"""
jsonl_parser.py
===============
Parser para el formato JSONL incremental de VS Code Copilot Chat.

El formato usa tres tipos de operaciones:
- kind=0: Estado inicial de la sesión
- kind=1: SET - establece un valor en un path
- kind=2: APPEND - agrega elementos a un array en un path
"""

import json
import logging
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime


logger = logging.getLogger(__name__)


@dataclass
class ChatMessage:
    """Representa un mensaje en el chat."""
    role: str  # 'user' o 'assistant'
    text: str
    timestamp: int
    request_index: int
    is_complete: bool = True


@dataclass
class SessionState:
    """Estado actual de una sesión de chat."""
    session_id: str = ""
    custom_title: str = ""
    requests: List[Dict] = field(default_factory=list)
    raw_state: Dict = field(default_factory=dict)
    last_line_read: int = 0


class JSONLParser:
    """Parser para archivos JSONL de sesiones de chat de Copilot."""
    
    def __init__(self):
        self.sessions: Dict[str, SessionState] = {}
    
    def get_at_path(self, obj: Any, path: List) -> Any:
        """Navega por un path de keys/indices en un objeto nested."""
        current = obj
        for key in path:
            if isinstance(current, dict) and key in current:
                current = current[key]
            elif isinstance(current, list) and isinstance(key, int) and key < len(current):
                current = current[key]
            else:
                return None
        return current
    
    def set_at_path(self, obj: Dict, path: List, value: Any) -> None:
        """Establece un valor en un path de keys/indices."""
        if not path:
            return
        
        current = obj
        for key in path[:-1]:
            if isinstance(current, dict):
                if key not in current:
                    next_key = path[path.index(key) + 1] if path.index(key) + 1 < len(path) else None
                    current[key] = [] if isinstance(next_key, int) else {}
                current = current[key]
            elif isinstance(current, list) and isinstance(key, int):
                while len(current) <= key:
                    current.append({})
                current = current[key]
            else:
                return
        
        last_key = path[-1]
        if isinstance(current, dict):
            current[last_key] = value
        elif isinstance(current, list) and isinstance(last_key, int):
            while len(current) <= last_key:
                current.append(None)
            current[last_key] = value
    
    def parse_line(self, line: str, state: Dict) -> Tuple[str, Any]:
        """
        Parsea una línea JSONL y actualiza el estado.
        Retorna (tipo_operacion, datos_relevantes).
        Soporta formato VS Code (kind 0/1/2), Cursor (role + message)
        y Codex CLI (type + payload).
        """
        entry = json.loads(line.strip())
        
        # Detectar formato Codex CLI
        if "type" in entry and "payload" in entry:
            return self._parse_codex_line(entry, state)

        # Detectar formato Cursor (tiene 'role' en lugar de 'kind')
        if "role" in entry and "message" in entry:
            return self._parse_cursor_line(entry, state)
        
        # Formato VS Code Copilot (kind 0/1/2)
        kind = entry.get("kind")
        k = entry.get("k", [])
        v = entry.get("v")
        
        if kind == 0:
            state.clear()
            if isinstance(v, dict):
                state.update(v)
            return ("init", state.copy())
        
        elif kind == 1:
            self.set_at_path(state, k, v)
            return ("set", {"path": k, "value": v})
        
        elif kind == 2:
            arr = self.get_at_path(state, k)
            if arr is None:
                self.set_at_path(state, k, v if isinstance(v, list) else [v])
            elif isinstance(arr, list):
                if isinstance(v, list):
                    arr.extend(v)
                else:
                    arr.append(v)
            return ("append", {"path": k, "value": v})
        
        return ("unknown", entry)

    def _parse_codex_line(self, entry: Dict, state: Dict) -> Tuple[str, Any]:
        """Parsea una línea en formato Codex CLI rollout JSONL."""
        line_type = entry.get("type", "")
        payload = entry.get("payload", {})

        if line_type == "session_meta":
            session_id = payload.get("id", "")
            cwd = payload.get("cwd", "")
            if session_id:
                state["sessionId"] = session_id
            state["customTitle"] = Path(cwd).name if cwd else "Codex CLI"
            state["codex_meta"] = payload
            return ("codex_meta", payload)

        if line_type == "event_msg" and isinstance(payload, dict):
            if payload.get("type") == "user_message":
                text = (payload.get("message") or "").strip()
                if not text:
                    return ("unknown", entry)
                if "codex_messages" not in state:
                    state["codex_messages"] = []
                message_data = {
                    "role": "user",
                    "text": text,
                    "phase": "user_message",
                    "timestamp": self._parse_codex_timestamp(entry.get("timestamp", "")),
                    "index": len(state["codex_messages"]),
                }
                state["codex_messages"].append(message_data)
                return ("codex_user", message_data)
            return ("unknown", entry)

        if line_type != "response_item" or not isinstance(payload, dict):
            return ("unknown", entry)

        if payload.get("type") != "message":
            return ("unknown", entry)

        role = payload.get("role", "")
        if role != "assistant":
            return ("unknown", entry)

        text = self._extract_codex_text(payload.get("content", []))
        if not text:
            return ("unknown", entry)

        if "codex_messages" not in state:
            state["codex_messages"] = []

        timestamp = self._parse_codex_timestamp(entry.get("timestamp", ""))
        message_data = {
            "role": role,
            "text": text,
            "phase": payload.get("phase", ""),
            "timestamp": timestamp,
            "index": len(state["codex_messages"]),
        }
        state["codex_messages"].append(message_data)

        if role == "user":
            return ("codex_user", message_data)
        return ("codex_assistant", message_data)

    def _extract_codex_text(self, content: List[Dict]) -> str:
        """Extrae texto visible de mensajes del rollout de Codex CLI."""
        if not isinstance(content, list):
            return ""

        parts = []
        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type in ("input_text", "output_text"):
                text = item.get("text", "")
                if text:
                    parts.append(text)
        return "\n".join(part.strip() for part in parts if part and part.strip()).strip()

    def _parse_codex_timestamp(self, timestamp: str) -> int:
        """Convierte timestamp ISO8601 a epoch segundos."""
        if not timestamp:
            return 0
        try:
            return int(datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp())
        except Exception:
            return 0
    
    def _parse_cursor_line(self, entry: Dict, state: Dict) -> Tuple[str, Any]:
        """
        Parsea una línea en formato Cursor (agent-transcripts).
        Formato: {"role": "assistant/user", "message": {"content": [{"type": "text", "text": "..."}]}}
        """
        role = entry.get("role", "")
        message = entry.get("message", {})
        content_list = message.get("content", [])
        
        # Extraer texto del contenido
        text_parts = []
        for item in content_list:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(item.get("text", ""))
        
        full_text = "\n".join(text_parts)
        
        # Para user: limpiar tags <user_query>
        if role == "user":
            full_text = self._clean_user_query(full_text)
        
        # Para assistant: separar respuesta del razonamiento (thinking)
        response_only = full_text
        thinking = ""
        if role == "assistant":
            response_only, thinking = self._split_cursor_response(full_text)
        
        # Inicializar estado de Cursor si no existe
        if "cursor_messages" not in state:
            state["cursor_messages"] = []
        
        # Agregar mensaje con ambos: respuesta y thinking
        msg_data = {
            "role": role,
            "text": full_text,  # Texto completo
            "response": response_only,  # Solo respuesta
            "thinking": thinking,  # Solo razonamiento
            "index": len(state["cursor_messages"])
        }
        state["cursor_messages"].append(msg_data)
        
        # Retornar como evento apropiado
        if role == "user":
            return ("cursor_user", msg_data)
        else:
            return ("cursor_assistant", msg_data)
    
    def _split_cursor_response(self, text: str) -> Tuple[str, str]:
        """
        Separa la respuesta del razonamiento de un mensaje de Cursor.
        Cursor escribe: "Respuesta aquí\n\nEl razonamiento interno..."
        
        Retorna: (response, thinking)
        """
        if not text:
            return (text, "")
        
        # Buscar el primer doble salto de línea
        parts = text.split("\n\n", 1)
        if len(parts) == 1:
            return (text, "")
        
        response = parts[0].strip()
        rest = parts[1].strip() if len(parts) > 1 else ""
        
        # Si la segunda parte parece razonamiento interno, separar
        thinking_indicators = [
            "el usuario", "voy a", "necesito", "parece que", "esto es",
            "déjame", "veo que", "puedo", "debería", "quizás",
            "probablemente", "la búsqueda", "este", "esta", "hay"
        ]
        
        if rest:
            rest_lower = rest.lower()
            for indicator in thinking_indicators:
                if rest_lower.startswith(indicator):
                    return (response, rest)
        
        # Si no detectamos thinking, todo es respuesta
        return (text, "")
    
    def _clean_user_query(self, text: str) -> str:
        """Limpia los tags <user_query> del mensaje del usuario."""
        import re
        # Remover tags <user_query> y </user_query>
        text = re.sub(r'<user_query>\s*', '', text)
        text = re.sub(r'\s*</user_query>', '', text)
        return text.strip()
    
    def parse_file(self, file_path: Path) -> SessionState:
        """Parsea un archivo JSONL completo y retorna el estado de la sesión."""
        session = SessionState()
        state = {}
        
        with open(file_path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                self.parse_line(line, state)
                session.last_line_read = i + 1
        
        session.raw_state = state
        session.session_id = state.get("sessionId", "")
        session.custom_title = state.get("customTitle", "")
        session.requests = state.get("requests", [])
        
        return session
    
    def parse_new_lines(self, file_path: Path, from_line: int = 0) -> Tuple[SessionState, List[Dict]]:
        """
        Parsea solo las líneas nuevas de un archivo.
        Retorna (estado_actualizado, lista_de_cambios).
        """
        file_key = str(file_path)
        
        if file_key not in self.sessions:
            self.sessions[file_key] = SessionState()
            self.sessions[file_key].raw_state = {}
        
        session = self.sessions[file_key]
        state = session.raw_state
        changes = []
        
        with open(file_path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                if i < from_line:
                    continue
                line = line.strip()
                if not line:
                    continue
                
                op_type, op_data = self.parse_line(line, state)
                changes.append({
                    "line": i,
                    "type": op_type,
                    "data": op_data
                })
                session.last_line_read = i + 1
        
        session.raw_state = state
        session.session_id = state.get("sessionId", "")
        session.custom_title = state.get("customTitle", "")
        session.requests = state.get("requests", [])
        
        return session, changes
    
    def extract_markdown_from_response(self, response_parts: List) -> str:
        """Extrae el texto markdown del array de response parts."""
        markdown_parts = []
        
        for part in response_parts:
            if not isinstance(part, dict):
                continue
            
            kind = part.get("kind", "")
            
            if kind == "markdownContent":
                val = part.get("content", {}).get("value", "")
                if val:
                    markdown_parts.append(val)
            
            elif kind in ("", None) and "value" in part:
                val = part.get("value", "")
                if val and isinstance(val, str):
                    markdown_parts.append(val)
            
            elif kind == "thinking":
                pass
            
            elif kind == "inlineReference":
                ref = part.get("inlineReference", {})
                if isinstance(ref, dict):
                    uri = ref.get("uri", ref.get("path", ""))
                    if uri:
                        markdown_parts.append(f"[ref:{uri}]")
        
        return "".join(markdown_parts)
    
    def get_last_assistant_response(self, session: SessionState) -> Optional[ChatMessage]:
        """Obtiene la última respuesta del assistant."""
        requests = session.requests
        
        for i, req in enumerate(reversed(requests)):
            response_parts = req.get("response", [])
            md = self.extract_markdown_from_response(response_parts)
            
            if md and md.strip():
                timestamp = req.get("timestamp", 0)
                return ChatMessage(
                    role="assistant",
                    text=md,
                    timestamp=timestamp,
                    request_index=len(requests) - 1 - i,
                    is_complete=True
                )
        
        return None
    
    def get_all_messages(self, session: SessionState) -> List[ChatMessage]:
        """Obtiene todos los mensajes de la sesión como lista de ChatMessage."""
        if session.raw_state.get("codex_messages"):
            return [
                ChatMessage(
                    role=msg.get("role", ""),
                    text=msg.get("text", ""),
                    timestamp=msg.get("timestamp", 0),
                    request_index=msg.get("index", 0),
                    is_complete=True
                )
                for msg in session.raw_state.get("codex_messages", [])
                if msg.get("text")
            ]

        messages = []
        
        for i, req in enumerate(session.requests):
            user_msg = req.get("message", {})
            if isinstance(user_msg, dict):
                user_text = user_msg.get("text", "")
            else:
                user_text = str(user_msg)
            
            timestamp = req.get("timestamp", 0)
            
            if user_text:
                messages.append(ChatMessage(
                    role="user",
                    text=user_text,
                    timestamp=timestamp,
                    request_index=i
                ))
            
            response_parts = req.get("response", [])
            assistant_text = self.extract_markdown_from_response(response_parts)
            
            if assistant_text:
                messages.append(ChatMessage(
                    role="assistant",
                    text=assistant_text,
                    timestamp=timestamp,
                    request_index=i
                ))
        
        return messages


# IDEs soportados
SUPPORTED_IDES = {
    "all": {"name": "Todos", "folders": ["Code - Insiders", "Code"], "include_cursor": True, "include_kiro": True, "include_codex": True},
    "vscode-insiders": {"name": "VS Code Insiders", "folders": ["Code - Insiders"], "include_cursor": False, "include_kiro": False, "include_codex": False},
    "vscode": {"name": "VS Code", "folders": ["Code"], "include_cursor": False, "include_kiro": False, "include_codex": False},
    "cursor": {"name": "Cursor", "folders": [], "include_cursor": True, "include_kiro": False, "include_codex": False},
    "kiro": {"name": "Kiro", "folders": [], "include_cursor": False, "include_kiro": True, "include_codex": False},
    "codex": {"name": "Codex CLI", "folders": [], "include_cursor": False, "include_kiro": False, "include_codex": True},
}


def find_most_recent_session_file(ide_filter: str = "all") -> Optional[Path]:
    """
    Busca el archivo JSONL de sesión más reciente.
    
    Args:
        ide_filter: "all", "vscode-insiders", "vscode", o "cursor"
    """
    import os
    import logging
    logger = logging.getLogger(__name__)
    
    appdata = Path(os.environ.get("APPDATA", ""))
    userprofile = Path(os.environ.get("USERPROFILE", ""))
    
    # Obtener configuración según filtro
    ide_config = SUPPORTED_IDES.get(ide_filter, SUPPORTED_IDES["all"])
    folders = ide_config["folders"]
    include_cursor = ide_config.get("include_cursor", False)
    include_codex = ide_config.get("include_codex", False)
    
    logger.debug(f"[FIND] Filtro: {ide_filter}, folders: {folders}, cursor: {include_cursor}")
    
    best_file = None
    best_mtime = 0
    
    # Buscar en VS Code / VS Code Insiders
    for folder in folders:
        ws_storage = appdata / folder / "User" / "workspaceStorage"
        if not ws_storage.exists():
            logger.debug(f"[FIND] No existe: {ws_storage}")
            continue
        
        logger.debug(f"[FIND] Buscando en: {ws_storage}")
        for jsonl_file in ws_storage.glob("*/chatSessions/*.jsonl"):
            mtime = jsonl_file.stat().st_mtime
            if mtime > best_mtime:
                best_mtime = mtime
                best_file = jsonl_file
                logger.debug(f"[FIND] Candidato VS Code: {jsonl_file}")
    
    # También buscar en globalStorage para sesiones sin workspace (solo VS Code)
    for folder in folders:
        if folder in ["Code - Insiders", "Code"]:
            empty_sessions = appdata / folder / "User" / "globalStorage" / "emptyWindowChatSessions"
            if empty_sessions.exists():
                for jsonl_file in empty_sessions.glob("*.jsonl"):
                    mtime = jsonl_file.stat().st_mtime
                    if mtime > best_mtime:
                        best_mtime = mtime
                        best_file = jsonl_file
    
    # Buscar en Cursor (ubicación diferente: ~/.cursor/projects/*/agent-transcripts/*/*.jsonl)
    if include_cursor:
        cursor_projects = userprofile / ".cursor" / "projects"
        if cursor_projects.exists():
            logger.debug(f"[FIND] Buscando en Cursor: {cursor_projects}")
            for jsonl_file in cursor_projects.glob("*/agent-transcripts/*/*.jsonl"):
                # Ignorar subagents
                if "subagents" in str(jsonl_file):
                    continue
                mtime = jsonl_file.stat().st_mtime
                if mtime > best_mtime:
                    best_mtime = mtime
                    best_file = jsonl_file
                    logger.debug(f"[FIND] Candidato Cursor: {jsonl_file}")
        else:
            logger.debug(f"[FIND] No existe carpeta Cursor: {cursor_projects}")

    # Buscar en Codex CLI (~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl)
    if include_codex:
        codex_sessions = userprofile / ".codex" / "sessions"
        if codex_sessions.exists():
            logger.debug(f"[FIND] Buscando en Codex: {codex_sessions}")
            day_dirs = [path for path in codex_sessions.glob("*/*/*") if path.is_dir()]
            day_dirs.sort(key=lambda path: path.stat().st_mtime, reverse=True)
            for day_dir in day_dirs[:10]:
                for jsonl_file in day_dir.glob("rollout-*.jsonl"):
                    mtime = jsonl_file.stat().st_mtime
                    if mtime > best_mtime:
                        best_mtime = mtime
                        best_file = jsonl_file
                        logger.debug(f"[FIND] Candidato Codex: {jsonl_file}")
        else:
            logger.debug(f"[FIND] No existe carpeta Codex: {codex_sessions}")
    
    # No loguear cada selección - se llama cientos de veces por minuto
    # if best_file:
    #     logger.debug(f"[FIND] Seleccionado ({ide_filter}): {best_file}")
    if not best_file:
        logger.warning(f"[FIND] No se encontró archivo para filtro: {ide_filter}")
    
    return best_file


if __name__ == "__main__":
    parser = JSONLParser()
    
    jsonl_file = find_most_recent_session_file()
    if not jsonl_file:
        print("No se encontró ningún archivo de sesión")
        exit(1)
    
    print(f"Archivo: {jsonl_file}")
    print(f"Tamaño: {jsonl_file.stat().st_size / 1024 / 1024:.2f} MB")
    
    session = parser.parse_file(jsonl_file)
    print(f"Session ID: {session.session_id}")
    print(f"Título: {session.custom_title}")
    print(f"Total requests: {len(session.requests)}")
    
    messages = parser.get_all_messages(session)
    print(f"\nUltimos 3 mensajes:")
    for msg in messages[-3:]:
        role_label = "[USER]" if msg.role == "user" else "[AI]"
        text_preview = msg.text[:100].replace('\n', ' ')
        print(f"  {role_label} [{msg.request_index}] {text_preview}...")
