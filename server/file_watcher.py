"""
file_watcher.py
===============
Monitorea los archivos JSONL de sesiones de chat en tiempo real.
Detecta cambios y emite eventos cuando hay nuevos mensajes.
"""

import os
import asyncio
import threading
import time
import hashlib
from pathlib import Path
from typing import Optional, Callable, Dict, List, Any
from dataclasses import dataclass
from datetime import datetime
import logging

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileModifiedEvent

from jsonl_parser import JSONLParser, SessionState, find_most_recent_session_file, SUPPORTED_IDES
from kiro_parser import KiroParser, find_most_recent_kiro_session, get_all_kiro_session_files

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)


@dataclass
class ChatEvent:
    """Evento de chat para transmitir via WebSocket."""
    event_type: str  # user_message, response_chunk, response_complete, session_changed
    data: Dict[str, Any]
    timestamp: float = 0
    
    def __post_init__(self):
        if self.timestamp == 0:
            self.timestamp = datetime.now().timestamp()
    
    def to_dict(self) -> Dict:
        return {
            "event": self.event_type,
            "timestamp": self.timestamp,
            **self.data
        }


class ChatSessionHandler(FileSystemEventHandler):
    """Handler para eventos de modificación de archivos JSONL."""
    
    def __init__(self, callback: Callable[[ChatEvent], None]):
        super().__init__()
        self.callback = callback
        self.parser = JSONLParser()
        self.file_positions: Dict[str, int] = {}  # path -> last line read
        self.file_sizes: Dict[str, int] = {}  # path -> last known size
        self.current_session_file: Optional[Path] = None
        self.last_response_text: Dict[int, str] = {}  # request_index -> accumulated text
        self.last_input_text: str = ""
        self.completed_requests: set[int] = set()
        self._process_lock = threading.Lock()  # evita procesado concurrente watchdog+poll
        self.include_thinking = True  # Incluir razonamiento de Cursor (activado por defecto)
        self.include_codex_progress = True  # Incluir commentary/progreso visible de Codex

    def _current_ide(self) -> str:
        current = str(self.current_session_file or "").lower()
        if "\\code - insiders\\" in current:
            return "vscode-insiders"
        if "\\code\\user\\" in current:
            return "vscode"
        return ""

    def _emit_session_changed(self, file_path: Path):
        self.callback(ChatEvent(
            event_type="session_changed",
            data={
                "file": str(file_path),
                "session_id": file_path.stem
            }
        ))

    def _emit_latest_snapshot(self, session: SessionState):
        ide = self._current_ide() or "vscode"

        input_state = session.raw_state.get("inputState", {})
        draft_text = input_state.get("inputText", "") if isinstance(input_state, dict) else ""
        if isinstance(draft_text, str):
            self.last_input_text = draft_text
            if draft_text.strip():
                self.callback(ChatEvent(
                    event_type="user_draft",
                    data={
                        "text": draft_text,
                        "request_index": len(session.requests),
                        "ide": ide,
                        "cleared": False,
                    }
                ))

        if not session.requests:
            return

        request_index = len(session.requests) - 1
        last_request = session.requests[request_index]
        message = last_request.get("message", {})
        user_text = message.get("text", "") if isinstance(message, dict) else str(message)
        if user_text:
            self.callback(ChatEvent(
                event_type="user_message",
                data={
                    "text": user_text,
                    "request_index": request_index,
                    "ide": ide,
                }
            ))

        response_text = self._extract_text_from_chunks(last_request.get("response", []))
        if response_text:
            self.last_response_text[request_index] = response_text
            model_state = last_request.get("modelState", {})
            is_complete = False
            if isinstance(model_state, dict):
                is_complete = bool(model_state.get("completedAt")) or model_state.get("value") == 1

            if is_complete:
                self.completed_requests.add(request_index)

            self.callback(ChatEvent(
                event_type="response_chunk",
                data={
                    "text": response_text,
                    "accumulated_text": response_text,
                    "request_index": request_index,
                    "is_first": True,
                    "is_complete": is_complete,
                    "ide": ide,
                }
            ))
    
    def on_modified(self, event):
        if event.is_directory:
            return
        
        if not event.src_path.endswith('.jsonl'):
            return
        
        file_path = Path(event.src_path)
        self._process_file_changes(file_path)
    
    def _process_file_changes(self, file_path: Path):
        """Procesa los cambios en un archivo JSONL."""
        with self._process_lock:
            try:
                file_key = str(file_path)
                current_size = file_path.stat().st_size
                switched_session = self.current_session_file != file_path

                if switched_session:
                    self.current_session_file = file_path
                    self.last_response_text.clear()
                    self.last_input_text = ""
                    self.completed_requests.clear()
                    self._emit_session_changed(file_path)

                if file_key in self.file_sizes and current_size < self.file_sizes[file_key]:
                    self.file_positions[file_key] = 0
                    self.parser.sessions.pop(file_key, None)
                    self.last_response_text.clear()
                    self.last_input_text = ""
                    self.completed_requests.clear()

                self.file_sizes[file_key] = current_size

                # Archivo NUEVO: marcar posición actual sin procesar historial
                if file_key not in self.file_positions:
                    # Contar líneas actuales para empezar desde ahí
                    try:
                        with open(file_path, 'r', encoding='utf-8') as f:
                            current_lines = sum(1 for _ in f)
                    except:
                        current_lines = 0
                    
                    self.file_positions[file_key] = current_lines
                    logger.info(f"[NEW FILE] {file_path.name} - marcando posición {current_lines}, solo mensajes nuevos")
                    
                    # Inicializar parser sin emitir eventos
                    session, _ = self.parser.parse_new_lines(file_path, 0)
                    if switched_session:
                        self._emit_latest_snapshot(session)
                    return  # No procesar contenido existente

                from_line = self.file_positions.get(file_key, 0)
                session, changes = self.parser.parse_new_lines(file_path, from_line)

                new_line = session.last_line_read
                if changes:
                    logger.debug(f"[PARSE] Read lines {from_line} -> {new_line}, got {len(changes)} changes")
                    self.file_positions[file_key] = new_line
                    self._process_changes(session, changes)
                elif new_line > from_line:
                    self.file_positions[file_key] = new_line

            except Exception as e:
                logger.error(f"Error procesando {file_path}: {e}")
    
    def _process_changes(self, session: SessionState, changes: List[Dict]):
        """Procesa los cambios y emite eventos apropiados."""
        for change in changes:
            op_type = change["type"]
            op_data = change["data"]
            
            # Debug: mostrar cada cambio detectado
            logger.debug(f"[CHANGE] type={op_type}, data_keys={list(op_data.keys()) if isinstance(op_data, dict) else 'N/A'}")
            
            if op_type == "init":
                # Sesión inicializada
                self.callback(ChatEvent(
                    event_type="session_init",
                    data={
                        "session_id": session.session_id,
                        "title": session.custom_title,
                        "request_count": len(session.requests)
                    }
                ))
            
            elif op_type == "append":
                path = op_data.get("path", [])
                value = op_data.get("value", [])
                logger.debug(f"[APPEND] path={path}")
                
                # Nuevo request (mensaje del usuario Y posiblemente respuesta)
                if path == ["requests"] and isinstance(value, list):
                    for req_idx, req in enumerate(value):
                        request_index = len(session.requests) - len(value) + req_idx
                        
                        # 1. Extraer mensaje del usuario
                        msg = req.get("message", {})
                        if isinstance(msg, dict):
                            user_text = msg.get("text", "")
                        else:
                            user_text = str(msg)
                        
                        if user_text:
                            ide = self._current_ide() or "vscode"
                            logger.debug(f"[USER_MSG] request={request_index}, text_len={len(user_text)}")
                            if self.last_input_text:
                                self.callback(ChatEvent(
                                    event_type="user_draft",
                                    data={
                                        "text": "",
                                        "request_index": request_index,
                                        "ide": ide,
                                        "cleared": True,
                                    }
                                ))
                                self.last_input_text = ""

                            self.callback(ChatEvent(
                                event_type="user_message",
                                data={
                                    "text": user_text,
                                    "request_index": request_index,
                                    "ide": ide,
                                }
                            ))
                        
                        # No emitir respuesta inline desde `requests`:
                        # VS Code suele repetir esos mismos fragments luego en
                        # `requests[n].response`, lo que duplica UI y TTS.
                
                # Chunk de respuesta del assistant
                elif len(path) >= 3 and path[0] == "requests" and path[2] == "response":
                    request_index = path[1]
                    
                    # Extraer texto de los nuevos chunks
                    chunks = value if isinstance(value, list) else [value]
                    logger.debug(f"[RESPONSE] request={request_index}, chunks_count={len(chunks)}")
                    new_text = self._extract_text_from_chunks(chunks)
                    logger.debug(f"[RESPONSE] extracted_text_len={len(new_text) if new_text else 0}")
                    
                    if new_text:
                        # Acumular texto para esta respuesta
                        if request_index not in self.last_response_text:
                            self.last_response_text[request_index] = ""
                            is_first = True
                        else:
                            is_first = False
                        
                        self.last_response_text[request_index] += new_text
                        
                        logger.debug(f"[EMITTING] response_chunk for request {request_index}, text_len={len(new_text)}")
                        self.callback(ChatEvent(
                            event_type="response_chunk",
                            data={
                                "text": new_text,
                                "accumulated_text": self.last_response_text[request_index],
                                "request_index": request_index,
                                "is_first": is_first,
                                "ide": self._current_ide() or "vscode",
                            }
                        ))
                        logger.debug(f"[EMITTED] response_chunk callback completed")
            
            elif op_type == "set":
                path = op_data.get("path", [])
                value = op_data.get("value")
                
                # Cambio de título
                if path == ["customTitle"]:
                    self.callback(ChatEvent(
                        event_type="title_changed",
                        data={"title": value}
                    ))
                
                elif path == ["inputState", "inputText"] and isinstance(value, str):
                    if value != self.last_input_text:
                        self.last_input_text = value
                        self.callback(ChatEvent(
                            event_type="user_draft",
                            data={
                                "text": value,
                                "request_index": len(session.requests),
                                "ide": self._current_ide() or "vscode",
                                "cleared": not bool(value.strip()),
                            }
                        ))

                elif (
                    len(path) >= 3
                    and path[0] == "requests"
                    and path[2] == "modelState"
                    and isinstance(path[1], int)
                ):
                    request_index = path[1]
                    is_complete = False
                    if isinstance(value, dict):
                        is_complete = bool(value.get("completedAt")) or value.get("value") == 1
                    else:
                        is_complete = value == 1

                    if is_complete and request_index not in self.completed_requests:
                        self.completed_requests.add(request_index)
                        final_text = self.last_response_text.get(request_index, "")
                        self.callback(ChatEvent(
                            event_type="response_chunk",
                            data={
                                "text": "",
                                "accumulated_text": final_text,
                                "request_index": request_index,
                                "is_first": False,
                                "is_complete": True,
                                "ide": self._current_ide() or "vscode",
                            }
                        ))
            
            # Eventos de Cursor (agent-transcripts)
            elif op_type == "cursor_user":
                self.callback(ChatEvent(
                    event_type="user_message",
                    data={
                        "text": op_data.get("text", ""),
                        "request_index": op_data.get("index", 0)
                    }
                ))
                logger.debug(f"[CURSOR] User message: {op_data.get('text', '')[:50]}...")
            
            elif op_type == "cursor_assistant":
                # Decidir qué texto usar según include_thinking
                full_text = op_data.get("text", "")
                response_only = op_data.get("response", full_text)
                
                logger.debug(f"[CURSOR] include_thinking={self.include_thinking}")
                logger.debug(f"[CURSOR] full_text len={len(full_text)}, response_only len={len(response_only)}")
                
                if self.include_thinking:
                    text = full_text  # Texto completo con razonamiento
                else:
                    text = response_only  # Solo respuesta
                
                self.callback(ChatEvent(
                    event_type="response_chunk",
                    data={
                        "text": text,
                        "accumulated_text": text,
                        "request_index": op_data.get("index", 0),
                        "is_first": True,
                        "ide": "cursor"
                    }
                ))
                logger.debug(f"[CURSOR] Enviando {len(text)} chars al TTS")

            elif op_type == "codex_user":
                self.callback(ChatEvent(
                    event_type="user_message",
                    data={
                        "text": op_data.get("text", ""),
                        "request_index": op_data.get("index", 0),
                        "ide": "codex",
                        "phase": op_data.get("phase", ""),
                    }
                ))

            elif op_type == "codex_assistant":
                phase = op_data.get("phase", "")
                if phase == "commentary" and not self.include_codex_progress:
                    continue

                text = op_data.get("text", "")
                if not text:
                    continue

                self.callback(ChatEvent(
                    event_type="response_chunk",
                    data={
                        "text": text,
                        "accumulated_text": text,
                        "request_index": op_data.get("index", 0),
                        "is_first": True,
                        "is_complete": True,
                        "ide": "codex",
                        "phase": phase,
                    }
                ))
    
    def _extract_text_from_chunks(self, chunks: List) -> str:
        """Extrae texto de los chunks de respuesta. Omite toolInvocationSerialized."""
        text_parts = []
        
        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue
            
            kind = chunk.get("kind", "")
            
            # Omitir invocaciones de herramientas (solo generan "bloque de código")
            if kind in ("toolInvocationSerialized", "toolInvocation"):
                continue
            
            # Texto directo
            if kind in ("", None) and "value" in chunk:
                val = chunk.get("value", "")
                if val and isinstance(val, str):
                    text_parts.append(val)
            
            # Markdown content
            elif kind == "markdownContent":
                val = chunk.get("content", {}).get("value", "")
                if val:
                    text_parts.append(val)
        
        return "".join(text_parts)


class CopilotChatWatcher:
    """Watcher principal para sesiones de chat de Copilot."""
    
    def __init__(self, event_callback: Callable[[ChatEvent], None], ide_filter: str = "all"):
        self.event_callback = event_callback
        self.observer: Optional[Observer] = None
        self.handler: Optional[ChatSessionHandler] = None
        self.watched_paths: set = set()
        self.polling_active = False
        self.poll_interval = 0.1  # segundos entre polls - MÁS AGRESIVO
        self._poll_thread = None
        self.ide_filter = ide_filter  # "all", "vscode-insiders", "vscode", "cursor", "kiro"
        self._ide_changed = False  # Flag para forzar re-detección en polling
        self.kiro_parser = KiroParser()  # Parser para archivos JSON de Kiro
        self.kiro_sessions: Dict[str, Dict] = {}  # Cache de sesiones de Kiro
    
    def set_ide_filter(self, ide_filter: str):
        """Cambia el IDE a monitorear."""
        if ide_filter not in SUPPORTED_IDES:
            logger.warning(f"[IDE] Filtro desconocido: {ide_filter}, usando 'all'")
            ide_filter = "all"
        
        old_filter = self.ide_filter
        self.ide_filter = ide_filter
        ide_name = SUPPORTED_IDES[ide_filter]["name"]
        logger.info(f"[IDE] Cambiado de '{old_filter}' a '{ide_filter}' ({ide_name})")
        
        # Resetear el handler para forzar re-detección del archivo más reciente
        if self.handler:
            self.handler.current_session_file = None
            self.handler.file_positions = {}
            self.handler.file_sizes = {}
        
        # Marcar que el IDE cambió para que el polling reinicie
        self._ide_changed = True
        
        return {"ide": ide_filter, "name": ide_name}
    
    def _get_watch_directories(self) -> List[Path]:
        """Obtiene los directorios a monitorear según el IDE seleccionado."""
        dirs = []
        
        # Obtener configuración según filtro de IDE
        ide_config = SUPPORTED_IDES.get(self.ide_filter, SUPPORTED_IDES["all"])
        ide_folders = ide_config["folders"]
        include_cursor = ide_config.get("include_cursor", False)
        include_kiro = ide_config.get("include_kiro", False)
        include_codex = ide_config.get("include_codex", False)
        
        # Detectar si estamos en Docker
        docker_mode = os.environ.get("DOCKER_MODE") == "1"
        appdata_override = os.environ.get("APPDATA_OVERRIDE")
        
        if docker_mode and appdata_override:
            # En Docker: usar rutas montadas (nombres en minúsculas)
            appdata = Path(appdata_override)
            folder_map = {
                "Code - Insiders": "code-insiders",
                "Code": "code",
            }
            variants = []
            for folder in ide_folders:
                docker_folder = folder_map.get(folder, folder.lower())
                variants.append(appdata / docker_folder / "workspaceStorage")
            logger.info(f"[Docker] Buscando en: {appdata} para {ide_folders}")
        else:
            # En Windows/host normal
            appdata = Path(os.environ.get("APPDATA", ""))
            userprofile = Path(os.environ.get("USERPROFILE", ""))
            variants = []
            for folder in ide_folders:
                variants.append(appdata / folder / "User" / "workspaceStorage")
        
        # VS Code / VS Code Insiders
        for variant in variants:
            if variant.exists():
                for ws_dir in variant.iterdir():
                    if ws_dir.is_dir():
                        chat_sessions = ws_dir / "chatSessions"
                        if chat_sessions.exists():
                            dirs.append(chat_sessions)
        
        # globalStorage para sesiones sin workspace (solo en host, solo VS Code)
        if not docker_mode:
            for folder in ide_folders:
                if folder in ["Code - Insiders", "Code"]:
                    empty_sessions = appdata / folder / "User" / "globalStorage" / "emptyWindowChatSessions"
                    if empty_sessions.exists():
                        dirs.append(empty_sessions)
            
            # Cursor: ubicación diferente (~/.cursor/projects/*/agent-transcripts/)
            if include_cursor:
                userprofile = Path(os.environ.get("USERPROFILE", ""))
                cursor_projects = userprofile / ".cursor" / "projects"
                if cursor_projects.exists():
                    for project_dir in cursor_projects.iterdir():
                        if project_dir.is_dir():
                            transcripts = project_dir / "agent-transcripts"
                            if transcripts.exists():
                                # Monitorear cada carpeta de transcript individualmente
                                for transcript_dir in transcripts.iterdir():
                                    if transcript_dir.is_dir() and transcript_dir.name != "subagents":
                                        dirs.append(transcript_dir)
            
            # Kiro: %APPDATA%\Kiro\User\History\*\
            if include_kiro:
                kiro_history = appdata / "Kiro" / "User" / "History"
                if kiro_history.exists():
                    for session_dir in kiro_history.iterdir():
                        if session_dir.is_dir():
                            dirs.append(session_dir)

            if include_codex:
                codex_sessions = userprofile / ".codex" / "sessions"
                if codex_sessions.exists():
                    for year_dir in codex_sessions.iterdir():
                        if not year_dir.is_dir():
                            continue
                        for month_dir in year_dir.iterdir():
                            if not month_dir.is_dir():
                                continue
                            for day_dir in month_dir.iterdir():
                                if day_dir.is_dir():
                                    dirs.append(day_dir)
        
        return dirs
    
    def start(self, use_watchdog: bool = False):
        """Inicia el watcher.
        
        Args:
            use_watchdog: Si True, usa watchdog Observer además del polling.
                          En Windows, watchdog puede causar problemas de bloqueo,
                          así que por defecto solo usamos polling.
        """
        self.handler = ChatSessionHandler(self.event_callback)
        
        # Watchdog Observer - DESHABILITADO por defecto en Windows
        # porque ReadDirectoryChangesW puede causar comportamientos extraños
        # cuando la ventana de consola no tiene foco
        if use_watchdog:
            self.observer = Observer()
            watch_dirs = self._get_watch_directories()
            
            for watch_dir in watch_dirs:
                try:
                    self.observer.schedule(self.handler, str(watch_dir), recursive=False)
                    self.watched_paths.add(watch_dir)
                    logger.info(f"Monitoreando: {watch_dir}")
                except Exception as e:
                    logger.warning(f"No se pudo monitorear {watch_dir}: {e}")
            
            if self.watched_paths:
                self.observer.start()
                logger.info(f"Watchdog iniciado. Monitoreando {len(self.watched_paths)} directorios.")
        else:
            logger.info("[MODO POLLING PURO] Watchdog deshabilitado para evitar bloqueos de Windows")
        
        # Cargar sesión más reciente
        self._load_current_session()
        
        # Polling - más confiable que watchdog en Windows
        self._start_polling()
    
    def _load_current_session(self):
        """
        Registra la posición actual del archivo sin emitir eventos.
        Solo lo que Copilot escriba DESPUÉS de arrancar el servidor se procesa.
        Soporta tanto JSONL (VS Code/Cursor) como JSON (Kiro).
        """
        # Verificar si estamos monitoreando Kiro
        ide_config = SUPPORTED_IDES.get(self.ide_filter, SUPPORTED_IDES["all"])
        include_kiro = ide_config.get("include_kiro", False)
        
        if include_kiro and self.ide_filter in ["kiro", "all"]:
            # Cargar sesión de Kiro
            recent_file = find_most_recent_kiro_session()
            if recent_file:
                logger.info(f"[Kiro] Sesión actual: {recent_file}")
                try:
                    session_data = self.kiro_parser.parse_file(recent_file)
                    if session_data:
                        file_key = str(recent_file)
                        self.kiro_sessions[file_key] = session_data
                        logger.info(f"[Kiro] Cargada sesión con {len(session_data.get('messages', []))} mensajes")
                except Exception as e:
                    logger.error(f"[Kiro] Error cargando sesión: {e}")
        
        # Cargar sesión de VS Code/Cursor (JSONL)
        recent_file = find_most_recent_session_file(self.ide_filter)
        if not recent_file or not self.handler:
            return

        logger.info(f"Sesión actual: {recent_file}")
        file_key = str(recent_file)

        try:
            with open(recent_file, 'r', encoding='utf-8') as f:
                current_lines = sum(1 for _ in f)
        except Exception as e:
            logger.warning(f"No se pudo leer posición inicial: {e}")
            current_lines = 0

        # Marcar posición: el watcher solo procesará líneas DESPUÉS de esta
        self.handler.file_positions[file_key] = current_lines
        self.handler.file_sizes[file_key]     = recent_file.stat().st_size
        self.handler.current_session_file     = recent_file

        # Inicializar parser silenciosamente (necesario para parse_new_lines)
        try:
            session, _ = self.handler.parser.parse_new_lines(recent_file, 0)
            session.last_line_read = current_lines
            if file_key in self.handler.parser.sessions:
                self.handler.parser.sessions[file_key].last_line_read = current_lines
        except Exception as e:
            logger.warning(f"Error inicializando parser: {e}")

        logger.info(f"Monitoreando desde línea {current_lines} — solo mensajes nuevos")
    
    def _start_polling(self):
        """Inicia polling agresivo cada 100ms."""
        self.polling_active = True
        self._stop_event = threading.Event()  # Más robusto que time.sleep contra throttling
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True, name="FilePoller")
        self._poll_thread.start()
        logger.info(f"Polling activo cada {int(self.poll_interval*1000)}ms")
    
    def _poll_loop(self):
        """Loop de polling — detecta cambios por tamaño y mtime del archivo."""
        import time
        import os
        last_checked_file = None
        last_size = 0
        last_mtime = 0
        consecutive_no_change = 0

        while self.polling_active:
            try:
                # Si el IDE cambió, reiniciar tracking
                if self._ide_changed:
                    self._ide_changed = False
                    last_checked_file = None
                    last_size = 0
                    last_mtime = 0
                    logger.info(f"[POLL] IDE cambió, reiniciando tracking para: {self.ide_filter}")
                
                # Polling para Kiro (archivos JSON)
                ide_config = SUPPORTED_IDES.get(self.ide_filter, SUPPORTED_IDES["all"])
                include_kiro = ide_config.get("include_kiro", False)
                
                if include_kiro and self.ide_filter in ["kiro", "all"]:
                    self._poll_kiro_sessions()
                
                recent_file = find_most_recent_session_file(self.ide_filter)
                if recent_file and self.handler:
                    # FORZAR lectura real del archivo - invalidar cache de Windows
                    # Leer los últimos bytes del archivo para detectar cambios reales
                    try:
                        # FILE_FLAG_NO_BUFFERING no está disponible en Python estándar,
                        # pero leer el contenido real fuerza a Windows a ir al disco
                        with open(recent_file, 'rb') as f:
                            f.seek(0, 2)  # Ir al final
                            current_size = f.tell()
                            # Leer últimos 512 bytes para detectar cambios de contenido
                            read_pos = max(0, current_size - 512)
                            f.seek(read_pos)
                            tail_content = f.read()
                        
                        # Usar hash del contenido final como detector de cambios
                        content_hash = hashlib.md5(tail_content).hexdigest()
                        
                        # También obtener mtime
                        stat_info = recent_file.stat()
                        current_mtime = stat_info.st_mtime_ns
                    except Exception as e:
                        logger.warning(f"[POLL] Error leyendo archivo: {e}")
                        stat = recent_file.stat()
                        current_size = stat.st_size
                        current_mtime = stat.st_mtime_ns
                        content_hash = ""

                    # Detectar cambios por archivo, tamaño, mtime O contenido
                    changed = (
                        recent_file != last_checked_file
                        or current_size != last_size
                        or current_mtime != last_mtime
                        or (content_hash and content_hash != getattr(self, '_last_content_hash', ''))
                    )
                    
                    if content_hash:
                        self._last_content_hash = content_hash

                    if changed:
                        consecutive_no_change = 0
                        if recent_file != last_checked_file:
                            logger.debug(f"[POLL] Nuevo archivo: {recent_file.name}")
                        elif current_size != last_size:
                            logger.debug(f"[POLL] Size: {last_size} → {current_size}")
                        elif current_mtime != last_mtime:
                            logger.debug(f"[POLL] mtime cambió (mismo size)")
                        
                        self.handler._process_file_changes(recent_file)
                        last_checked_file = recent_file
                        
                        # BURST MODE: VS Code escribe en chunks pequeños
                        # Hacer varios polls rápidos para capturar toda la escritura
                        for _ in range(5):
                            time.sleep(0.02)  # 20ms entre bursts
                            try:
                                with open(recent_file, 'rb') as f:
                                    f.seek(0, 2)
                                    burst_size = f.tell()
                                if burst_size != last_size:
                                    logger.debug(f"[BURST] Size: {last_size} → {burst_size}")
                                    self.handler._process_file_changes(recent_file)
                                    last_size = burst_size
                            except:
                                break
                        
                        # Re-leer stats después de procesar
                        try:
                            with open(recent_file, 'rb') as f:
                                f.seek(0, 2)
                                last_size = f.tell()
                                f.seek(max(0, last_size - 512))
                                tail = f.read()
                            self._last_content_hash = hashlib.md5(tail).hexdigest()
                            last_mtime = recent_file.stat().st_mtime_ns
                        except:
                            stat = recent_file.stat()
                            last_size = stat.st_size
                            last_mtime = stat.st_mtime_ns
                    else:
                        consecutive_no_change += 1

                # Polling adaptativo: más rápido cuando hay actividad
                # Usamos Event.wait() que es más robusto contra throttling de Windows
                if consecutive_no_change < 10:
                    self._stop_event.wait(0.05)  # 50ms cuando hay actividad reciente
                elif consecutive_no_change < 50:
                    self._stop_event.wait(0.1)   # 100ms normal
                else:
                    self._stop_event.wait(0.2)   # 200ms cuando está inactivo
                    
            except Exception as e:
                logger.error(f"Error en polling: {e}")
                self._stop_event.wait(1)
    
    def _poll_kiro_sessions(self):
        """Polling específico para sesiones de Kiro (archivos JSON)."""
        try:
            recent_file = find_most_recent_kiro_session()
            if not recent_file:
                return
            
            file_key = str(recent_file)
            
            # Parsear el archivo actual
            session_data = self.kiro_parser.parse_file(recent_file)
            if not session_data:
                return
            
            # Comparar con la versión anterior
            old_data = self.kiro_sessions.get(file_key)
            changes = self.kiro_parser.compare_sessions(old_data, session_data)
            
            # Emitir eventos para mensajes nuevos
            for change in changes:
                if change["type"] == "new_message":
                    msg = change["message"]
                    role = msg.get("role", "")
                    content = msg.get("content", "")
                    
                    if role == "user":
                        event = ChatEvent(
                            event_type="user_message",
                            data={
                                "text": content,
                                "request_index": change["index"]
                            }
                        )
                        self.event_callback(event)
                        logger.info(f"[Kiro] Usuario: {content[:50]}...")
                    
                    elif role == "assistant":
                        # Emitir como respuesta completa
                        event = ChatEvent(
                            event_type="response_complete",
                            data={
                                "text": content,
                                "request_index": change["index"]
                            }
                        )
                        self.event_callback(event)
                        logger.info(f"[Kiro] Asistente: {content[:50]}...")
            
            # Actualizar cache
            self.kiro_sessions[file_key] = session_data
            
        except Exception as e:
            logger.error(f"[Kiro] Error en polling: {e}")
    
    def stop(self):
        """Detiene el watcher."""
        self.polling_active = False
        if hasattr(self, '_stop_event'):
            self._stop_event.set()  # Despierta el thread inmediatamente
        if self._poll_thread:
            self._poll_thread.join(timeout=2)
        if self.observer:
            self.observer.stop()
            self.observer.join()
            logger.info("Watcher detenido.")
    
    def get_current_session(self) -> Optional[SessionState]:
        """Obtiene el estado actual de la sesión."""
        if self.handler and self.handler.current_session_file:
            file_key = str(self.handler.current_session_file)
            return self.handler.parser.sessions.get(file_key)
        return None
    
    def poll_once(self) -> bool:
        """
        Ejecuta una iteración del polling.
        Retorna True si se detectaron cambios.
        
        Diseñado para ser llamado desde un asyncio task en lugar de un thread.
        Los threads de Windows se throttlean cuando la consola no tiene foco,
        pero asyncio tasks no.
        """
        if not self.handler:
            return False
        
        try:
            # Si el IDE cambió, reiniciar tracking
            if self._ide_changed:
                self._ide_changed = False
                self._poll_last_file = None
                self._poll_last_size = 0
                self._poll_last_mtime = 0
                self._poll_last_hash = ""
                logger.info(f"[POLL] IDE cambió, reiniciando tracking para: {self.ide_filter}")
            
            recent_file = find_most_recent_session_file(self.ide_filter)
            if not recent_file:
                return False
            
            # Leer estado actual del archivo
            try:
                with open(recent_file, 'rb') as f:
                    f.seek(0, 2)
                    current_size = f.tell()
                    read_pos = max(0, current_size - 512)
                    f.seek(read_pos)
                    tail_content = f.read()
                
                content_hash = hashlib.md5(tail_content).hexdigest()
                current_mtime = recent_file.stat().st_mtime_ns
            except Exception as e:
                logger.warning(f"[POLL] Error leyendo archivo: {e}")
                return False
            
            # Detectar cambios
            last_file = getattr(self, '_poll_last_file', None)
            last_size = getattr(self, '_poll_last_size', 0)
            last_mtime = getattr(self, '_poll_last_mtime', 0)
            last_hash = getattr(self, '_poll_last_hash', '')
            
            changed = (
                recent_file != last_file
                or current_size != last_size
                or current_mtime != last_mtime
                or content_hash != last_hash
            )
            
            if changed:
                self.handler._process_file_changes(recent_file)
                
                # Actualizar estado
                self._poll_last_file = recent_file
                self._poll_last_size = current_size
                self._poll_last_mtime = current_mtime
                self._poll_last_hash = content_hash
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"[POLL] Error en poll_once: {e}")
            return False


if __name__ == "__main__":
    import time
    
    def on_event(event: ChatEvent):
        print(f"[{event.event_type}] {event.data}")
    
    watcher = CopilotChatWatcher(on_event)
    watcher.start()
    
    try:
        print("\nEscuchando eventos... (Ctrl+C para salir)")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nDeteniendo...")
    finally:
        watcher.stop()
