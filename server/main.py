"""
main.py
=======
Servidor WebSocket que transmite eventos del chat de Copilot en tiempo real.

Uso:
    python main.py [--port 8765] [--host 0.0.0.0] [--ui-port 8080]

El servidor:
1. Monitorea los archivos JSONL de sesiones de chat
2. Detecta nuevos mensajes y respuestas
3. Transmite eventos a todos los clientes conectados via WebSocket
4. Sirve la UI en un puerto HTTP separado
"""

import asyncio
import json
import argparse
import logging
import sys
import threading
import http.server
import socketserver
from typing import Set, Optional
from pathlib import Path
from functools import partial

# Forzar salida sin buffer para que la consola muestre logs en tiempo real
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

# ── FIX: Optimizaciones de Windows para evitar throttling ─────────────────────
# Windows reduce la prioridad y throttlea procesos en ventanas sin foco.
# Esto causa que los mensajes no se procesen hasta que interactúas con la ventana.

def _optimize_windows_process():
    """Configura el proceso para evitar throttling y bloqueos de Windows."""
    try:
        import ctypes
        import ctypes.wintypes
        k = ctypes.windll.kernel32
        
        # ══ CONSOLA ══════════════════════════════════════════════════════════════
        # La consola de Windows puede "pausar" el proceso de varias formas:
        # - QuickEdit mode: clic en consola pausa todo
        # - Mark mode: seleccionar texto pausa todo
        # - Buffer lleno: si hay mucho output, se pausa esperando scroll
        
        h_in = k.GetStdHandle(-10)   # STD_INPUT_HANDLE
        h_out = k.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        
        if h_in and h_in != -1:
            mode = ctypes.wintypes.DWORD()
            if k.GetConsoleMode(h_in, ctypes.byref(mode)):
                # Deshabilitar TODOS los modos que pueden causar bloqueo:
                # ENABLE_QUICK_EDIT_MODE = 0x0040
                # ENABLE_INSERT_MODE = 0x0020
                # ENABLE_MOUSE_INPUT = 0x0010 (puede causar eventos que bloquean)
                new_mode = mode.value & ~(0x0040 | 0x0020 | 0x0010)
                # Habilitar ENABLE_EXTENDED_FLAGS para que los cambios apliquen
                new_mode |= 0x0080
                k.SetConsoleMode(h_in, new_mode)
                print(f"[Windows] Console input mode: {hex(mode.value)} -> {hex(new_mode)}")
        
        if h_out and h_out != -1:
            mode = ctypes.wintypes.DWORD()
            if k.GetConsoleMode(h_out, ctypes.byref(mode)):
                # Habilitar ENABLE_VIRTUAL_TERMINAL_PROCESSING para mejor output
                # y DISABLE_NEWLINE_AUTO_RETURN para evitar bloqueos por buffer
                new_mode = mode.value | 0x0004
                k.SetConsoleMode(h_out, new_mode)
        
        # ══ PRIORIDAD ════════════════════════════════════════════════════════════
        current_process = k.GetCurrentProcess()
        k.SetPriorityClass(current_process, 0x0080)  # HIGH_PRIORITY_CLASS
        
        # ══ ANTI-IDLE ════════════════════════════════════════════════════════════
        # ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED
        k.SetThreadExecutionState(0x80000000 | 0x00000001 | 0x00000040)
        
        # ══ TIMER ════════════════════════════════════════════════════════════════
        try:
            ctypes.windll.winmm.timeBeginPeriod(1)
        except Exception:
            pass
        
        print("[Windows] Proceso optimizado: consola desbloqueada, prioridad alta")
        
    except Exception as e:
        print(f"[Windows] No se pudieron aplicar optimizaciones: {e}")

_optimize_windows_process()

import websockets
from websockets.server import WebSocketServerProtocol

from file_watcher import CopilotChatWatcher, ChatEvent
from jsonl_parser import JSONLParser, find_most_recent_session_file
from tts_engine import ServerTTS
from telegram_input import get_telegram_input_handler
from database import get_db, Database


class NonBlockingHandler(logging.Handler):
    """Handler de logging que no bloquea - usa una cola interna."""
    
    def __init__(self):
        super().__init__()
        self._queue = []
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._writer, daemon=True)
        self._thread.start()
    
    def emit(self, record):
        try:
            msg = self.format(record)
            with self._lock:
                self._queue.append(msg)
        except Exception:
            pass
    
    def _writer(self):
        import time
        while True:
            msgs = None
            with self._lock:
                if self._queue:
                    msgs = self._queue[:]
                    self._queue.clear()
            if msgs:
                try:
                    for msg in msgs:
                        print(msg, file=sys.stderr)
                except Exception:
                    pass
            time.sleep(0.1)  # Batch writes cada 100ms


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[NonBlockingHandler()],
    force=True
)
logger = logging.getLogger(__name__)


def infer_ide_from_path(file_path: str) -> str:
    """Infere el origen del chat según la ruta del archivo fuente."""
    normalized = (file_path or "").lower()
    if ".codex\\sessions" in normalized:
        return "codex"
    if "\\.cursor\\projects" in normalized:
        return "cursor"
    if "\\kiro\\user\\history" in normalized:
        return "kiro"
    if "\\code - insiders\\" in normalized:
        return "vscode-insiders"
    if "\\code\\user\\" in normalized:
        return "vscode"
    return ""


class CopilotWebSocketServer:
    """Servidor WebSocket para transmitir eventos de chat en tiempo real."""
    
    def __init__(self, host: str = "localhost", port: int = 8765, llm_model: str = None):
        self.host = host
        self.port = port
        self.clients: Set[WebSocketServerProtocol] = set()
        self.watcher: Optional[CopilotChatWatcher] = None
        self.event_queue: asyncio.Queue = None  # Se inicializa en start()
        self.parser = JSONLParser()
        self.loop: Optional[asyncio.AbstractEventLoop] = None  # Event loop principal
        self.tts = ServerTTS(llm_model=llm_model, audio_callback=self._on_audio_ready)
        self.telegram_input = get_telegram_input_handler(on_message_callback=self._on_telegram_input)
        
        # Base de datos para persistencia
        self.db: Database = get_db()
        self.current_session_id: Optional[int] = None
        self._last_saved_request_index: int = -1
    
    def _on_telegram_input(self, text: str):
        """Callback cuando se recibe un mensaje de Telegram."""
        logger.info(f"[TELEGRAM] Mensaje recibido: {text[:50]}...")
        # Notificar a los clientes
        if self.loop and self.clients:
            self.loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self.broadcast({
                    "event": "telegram_input_received",
                    "text": text[:200]
                }))
            )
    
    def _on_audio_ready(self, audio_url: str):
        """Callback para cuando hay audio TTS listo (modo Docker)."""
        if self.loop and self.clients:
            self.loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self.broadcast({
                    "event": "tts_audio",
                    "url": audio_url
                }))
            )
    
    async def register(self, websocket: WebSocketServerProtocol):
        """Registra un nuevo cliente."""
        self.clients.add(websocket)
        logger.info(f"Cliente conectado. Total: {len(self.clients)}")
        
        # Enviar estado actual de la sesión al nuevo cliente
        await self._send_current_state(websocket)
    
    async def unregister(self, websocket: WebSocketServerProtocol):
        """Desregistra un cliente."""
        self.clients.discard(websocket)
        logger.info(f"Cliente desconectado. Total: {len(self.clients)}")
    
    async def _send_current_state(self, websocket: WebSocketServerProtocol):
        """Envía el estado actual de la sesión al cliente."""
        try:
            ide_filter = self.watcher.ide_filter if self.watcher else "all"
            recent_file = find_most_recent_session_file(ide_filter)
            if not recent_file:
                await websocket.send(json.dumps({
                    "event": "no_session",
                    "message": "No hay sesiones de chat disponibles"
                }))
                return
            
            session = self.parser.parse_file(recent_file)
            messages = self.parser.get_all_messages(session)
            
            # Enviar info de sesión
            await websocket.send(json.dumps({
                "event": "session_state",
                "session_id": session.session_id,
                "title": session.custom_title or "Sin título",
                "file": str(recent_file),
                "message_count": len(messages),
                "ide": infer_ide_from_path(str(recent_file)) or ide_filter,
            }))
            
            # Enviar historial de mensajes
            for msg in messages:
                await websocket.send(json.dumps({
                    "event": "history_message",
                    "role": msg.role,
                    "text": msg.text,
                    "request_index": msg.request_index,
                    "timestamp": msg.timestamp,
                    "ide": infer_ide_from_path(str(recent_file)) or ide_filter,
                }))
            
            await websocket.send(json.dumps({
                "event": "history_complete",
                "total_messages": len(messages)
            }))
            
        except Exception as e:
            logger.error(f"Error enviando estado: {e}")
            await websocket.send(json.dumps({
                "event": "error",
                "message": str(e)
            }))
    
    async def broadcast(self, message: dict):
        """Envía un mensaje a todos los clientes. Timeout individual por cliente."""
        if not self.clients:
            return
        message_json = json.dumps(message, ensure_ascii=False)
        disconnected = set()
        for client in list(self.clients):
            try:
                await asyncio.wait_for(client.send(message_json), timeout=2.0)
            except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed, Exception):
                disconnected.add(client)
        for client in disconnected:
            self.clients.discard(client)
    
    def on_chat_event(self, event: ChatEvent):
        """Callback para eventos del watcher (ejecuta en thread del watcher)."""
        logger.debug(f"[CALLBACK] on_chat_event: {event.event_type}")
        if self.loop and self.event_queue:
            # call_soon_threadsafe es instantáneo y no bloquea el thread del watcher.
            # run_coroutine_threadsafe + future.result() bloqueaba el watcher hasta 1s
            # y podía causar que se perdieran eventos durante ese tiempo.
            self.loop.call_soon_threadsafe(self.event_queue.put_nowait, event)
            logger.debug(f"[CALLBACK] Event queued: {event.event_type}")
        else:
            logger.warning(f"[CALLBACK] Cannot queue event: loop or queue not ready")
    
    async def event_processor(self):
        """Procesa eventos de la queue y los transmite a clientes."""
        logger.info("[PROCESSOR] Event processor started")
        while True:
            try:
                logger.debug("[PROCESSOR] Waiting for event...")
                event = await self.event_queue.get()
                logger.debug(f"[PROCESSOR] Got event from queue: {event.event_type}")
                await self.broadcast(event.to_dict())
                
                # Guardar en base de datos
                await self._save_event_to_db(event)
                
                # TTS del servidor para respuestas del AI
                if event.event_type == "response_chunk":
                    self.tts.process_chunk(
                        event.data.get("request_index", 0),
                        event.data.get("accumulated_text", event.data.get("text", "")),
                        event.data.get("is_first", False),
                        is_history=event.data.get("is_history", False),
                        is_complete=event.data.get("is_complete", False),
                        ide=event.data.get("ide", ""),
                    )
                    text_preview = event.data.get("text", "")[:50]
                    logger.debug(f"Chunk: {text_preview}...")
                else:
                    logger.info(f"Evento: {event.event_type}")
                    
            except Exception as e:
                logger.error(f"Error procesando evento: {e}")
    
    async def _save_event_to_db(self, event: ChatEvent):
        """Guarda eventos relevantes en la base de datos."""
        try:
            # Asegurar que tenemos una sesión activa
            if self.current_session_id is None:
                source_file = ""
                if self.watcher and self.watcher.handler and self.watcher.handler.current_session_file:
                    source_file = str(self.watcher.handler.current_session_file)
                ide = infer_ide_from_path(source_file) or (self.watcher.ide_filter if self.watcher else "all")
                self.current_session_id = self.db.get_or_create_session_by_source(
                    source_file=source_file,
                    ide=ide
                )
                logger.info(f"[DB] Sesión activa: {self.current_session_id}")
            
            data = event.data
            request_index = data.get("request_index", 0)
            
            # Guardar mensajes del usuario
            if event.event_type == "user_message":
                text = data.get("text", "")
                if text and not self.db.message_exists(
                    self.current_session_id, "user", text, request_index
                ):
                    self.db.add_message(
                        session_id=self.current_session_id,
                        role="user",
                        text=text,
                        request_index=request_index,
                        timestamp=data.get("timestamp", ""),
                        ide=data.get("ide", "") or infer_ide_from_path(str(getattr(self.watcher.handler, "current_session_file", "")))
                    )
                    logger.debug(f"[DB] Guardado mensaje usuario: {text[:50]}...")

            elif event.event_type == "response_chunk" and data.get("is_complete"):
                text = data.get("accumulated_text", data.get("text", ""))
                if text and request_index > self._last_saved_request_index:
                    self.db.add_message(
                        session_id=self.current_session_id,
                        role="assistant",
                        text=text,
                        request_index=request_index,
                        timestamp=data.get("timestamp", ""),
                        ide=data.get("ide", "") or infer_ide_from_path(str(getattr(self.watcher.handler, "current_session_file", ""))),
                        has_thinking=False,
                        thinking_text=""
                    )
                    self._last_saved_request_index = request_index
                    logger.debug(f"[DB] Guardada respuesta completa desde chunk: {text[:50]}...")
            
            # Guardar respuestas completas del asistente
            elif event.event_type == "response_complete":
                text = data.get("text", "")
                thinking = data.get("thinking", "")
                
                if text and request_index > self._last_saved_request_index:
                    self.db.add_message(
                        session_id=self.current_session_id,
                        role="assistant",
                        text=text,
                        request_index=request_index,
                        timestamp=data.get("timestamp", ""),
                        ide=data.get("ide", ""),
                        has_thinking=bool(thinking),
                        thinking_text=thinking
                    )
                    self._last_saved_request_index = request_index
                    logger.debug(f"[DB] Guardada respuesta asistente: {text[:50]}...")
            
            # Nuevo archivo = nueva sesión
            elif event.event_type == "file_changed":
                new_file = data.get("file_path", "")
                if new_file:
                    ide = infer_ide_from_path(new_file) or (self.watcher.ide_filter if self.watcher else "all")
                    self.current_session_id = self.db.get_or_create_session_by_source(
                        source_file=new_file,
                        ide=ide
                    )
                    self._last_saved_request_index = -1
                    logger.info(f"[DB] Nueva sesión para archivo: {new_file}")
                    
        except Exception as e:
            logger.error(f"[DB] Error guardando evento: {e}")
    
    async def handler(self, websocket: WebSocketServerProtocol, path: str = ""):
        """Handler para conexiones WebSocket."""
        await self.register(websocket)
        
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    await self.handle_client_message(websocket, data)
                except json.JSONDecodeError:
                    await websocket.send(json.dumps({
                        "event": "error",
                        "message": "JSON inválido"
                    }))
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            await self.unregister(websocket)
    
    async def handle_client_message(self, websocket: WebSocketServerProtocol, data: dict):
        """Maneja mensajes del cliente."""
        action = data.get("action")
        
        if action == "get_state":
            await self._send_current_state(websocket)
        
        elif action == "ping":
            await websocket.send(json.dumps({"event": "pong", "_t": data.get("_t")}))

        elif action == "tts_stop":
            self.tts.stop_audio()
            await websocket.send(json.dumps({"event": "tts_stopped"}))
        
        elif action == "tts_pause":
            self.tts.pause()
            await websocket.send(json.dumps({"event": "tts_paused"}))
        
        elif action == "tts_resume":
            self.tts.resume()
            await websocket.send(json.dumps({"event": "tts_resumed"}))
        
        elif action == "tts_skip":
            self.tts.skip_current()
            await websocket.send(json.dumps({"event": "tts_skipped"}))
        
        elif action == "tts_status":
            status = self.tts.get_playback_status()
            await websocket.send(json.dumps({"event": "tts_playback_status", **status}))
        
        # Controles de TTS del servidor
        elif action == "tts_enable":
            self.tts.set_enabled(data.get("enabled", False))
            await websocket.send(json.dumps({
                "event": "tts_status",
                "enabled": self.tts.enabled
            }))
        
        elif action == "tts_set_rate":
            rate = data.get("rate", 200)
            self.tts.set_rate(rate)
            await websocket.send(json.dumps({
                "event": "tts_rate_set",
                "rate": rate
            }))
        
        elif action == "tts_set_voice":
            voice_index = data.get("voice_index", 0)
            self.tts.set_voice(voice_index)
            await websocket.send(json.dumps({
                "event": "tts_voice_set",
                "voice_index": voice_index
            }))
        
        elif action == "tts_get_voices":
            voices = self.tts.get_voices()
            await websocket.send(json.dumps({
                "event": "tts_voices",
                "voices": voices
            }))
        
        elif action == "tts_get_status":
            await websocket.send(json.dumps({
                "event": "tts_status",
                "enabled": self.tts.enabled,
                "rate": self.tts.rate,
                "llm_enabled": getattr(self.tts, 'llm_enabled', False)
            }))
        
        elif action == "tts_llm_enable":
            self.tts.set_llm_enabled(data.get("enabled", True))
            await websocket.send(json.dumps({
                "event": "tts_llm_status",
                "enabled": self.tts.llm_enabled
            }))
        
        elif action == "tts_telegram_enable":
            success = self.tts.set_telegram_enabled(data.get("enabled", False))
            status = self.tts.get_telegram_status()
            await websocket.send(json.dumps({
                "event": "tts_telegram_status",
                **status,
                "success": success
            }))
        
        elif action == "tts_telegram_status":
            status = self.tts.get_telegram_status()
            await websocket.send(json.dumps({
                "event": "tts_telegram_status",
                **status
            }))
        
        # Telegram Input (recibir mensajes y pegarlos en Cursor)
        elif action == "telegram_input_enable":
            success = self.telegram_input.set_enabled(data.get("enabled", False))
            status = self.telegram_input.get_status()
            await websocket.send(json.dumps({
                "event": "telegram_input_status",
                **status,
                "success": success
            }))
        
        elif action == "telegram_input_status":
            status = self.telegram_input.get_status()
            await websocket.send(json.dumps({
                "event": "telegram_input_status",
                **status
            }))
        
        # Control de IDE a monitorear
        elif action == "set_ide":
            ide_filter = data.get("ide", "all")
            if self.watcher:
                result = self.watcher.set_ide_filter(ide_filter)
                await self.broadcast({
                    "event": "ide_changed",
                    **result
                })
        
        elif action == "get_ides":
            from file_watcher import SUPPORTED_IDES
            ides = [{"id": k, "name": v["name"]} for k, v in SUPPORTED_IDES.items()]
            current = self.watcher.ide_filter if self.watcher else "all"
            await websocket.send(json.dumps({
                "event": "ides_list",
                "ides": ides,
                "current": current
            }))
        
        elif action == "set_include_thinking":
            enabled = data.get("enabled", False)
            if self.watcher and self.watcher.handler:
                self.watcher.handler.include_thinking = enabled
                logger.info(f"[CONFIG] Include thinking: {enabled}")
            await websocket.send(json.dumps({
                "event": "include_thinking_set",
                "enabled": enabled
            }))

        elif action == "set_include_codex_progress":
            enabled = data.get("enabled", True)
            if self.watcher and self.watcher.handler:
                self.watcher.handler.include_codex_progress = enabled
                logger.info(f"[CONFIG] Include Codex progress: {enabled}")
            await websocket.send(json.dumps({
                "event": "include_codex_progress_set",
                "enabled": enabled
            }))
        
        # Forzar re-escaneo de archivos
        elif action == "force_refresh":
            if self.watcher:
                # Usar poll_once que busca el archivo más reciente y procesa cambios
                changed = self.watcher.poll_once()
                if changed:
                    logger.debug("[FORCE] Cambios detectados y procesados")
            await websocket.send(json.dumps({
                "event": "refresh_triggered",
                "status": "ok"
            }))
        
        # ══════════════════════════════════════════════════════════════════════════
        # HISTORIAL Y BASE DE DATOS
        # ══════════════════════════════════════════════════════════════════════════
        
        elif action == "db_get_sessions":
            limit = data.get("limit", 50)
            offset = data.get("offset", 0)
            ide = data.get("ide", None)
            sessions = self.db.get_sessions(limit=limit, offset=offset, ide=ide)
            await websocket.send(json.dumps({
                "event": "db_sessions",
                "sessions": [
                    {
                        "id": s.id,
                        "name": s.name,
                        "ide": s.ide,
                        "created_at": s.created_at,
                        "updated_at": s.updated_at,
                        "message_count": s.message_count
                    } for s in sessions
                ]
            }))
        
        elif action == "db_get_messages":
            session_id = data.get("session_id")
            limit = data.get("limit", 100)
            offset = data.get("offset", 0)
            
            if session_id:
                messages = self.db.get_messages(session_id, limit=limit, offset=offset)
            else:
                messages = self.db.get_all_messages(limit=limit, offset=offset)
            
            await websocket.send(json.dumps({
                "event": "db_messages",
                "session_id": session_id,
                "messages": [
                    {
                        "id": m.id,
                        "session_id": m.session_id,
                        "role": m.role,
                        "text": m.text,
                        "request_index": m.request_index,
                        "timestamp": m.timestamp,
                        "ide": m.ide,
                        "has_thinking": m.has_thinking,
                        "thinking_text": m.thinking_text if m.has_thinking else "",
                        "created_at": m.created_at
                    } for m in messages
                ]
            }))
        
        elif action == "db_search":
            query = data.get("query", "")
            limit = data.get("limit", 50)
            if query:
                messages = self.db.search_messages(query, limit=limit)
                await websocket.send(json.dumps({
                    "event": "db_search_results",
                    "query": query,
                    "messages": [
                        {
                            "id": m.id,
                            "session_id": m.session_id,
                            "role": m.role,
                            "text": m.text[:500],  # Limitar para búsqueda
                            "created_at": m.created_at
                        } for m in messages
                    ]
                }))
            else:
                await websocket.send(json.dumps({
                    "event": "db_search_results",
                    "query": "",
                    "messages": []
                }))
        
        elif action == "db_export_session":
            session_id = data.get("session_id")
            format_type = data.get("format", "markdown")  # markdown o json
            
            if session_id:
                if format_type == "json":
                    content = self.db.export_session_json(session_id)
                else:
                    content = self.db.export_session_markdown(session_id)
                
                await websocket.send(json.dumps({
                    "event": "db_export",
                    "session_id": session_id,
                    "format": format_type,
                    "content": content
                }))
            else:
                await websocket.send(json.dumps({
                    "event": "error",
                    "message": "session_id requerido"
                }))
        
        elif action == "db_delete_session":
            session_id = data.get("session_id")
            if session_id:
                self.db.delete_session(session_id)
                await websocket.send(json.dumps({
                    "event": "db_session_deleted",
                    "session_id": session_id
                }))
            else:
                await websocket.send(json.dumps({
                    "event": "error",
                    "message": "session_id requerido"
                }))
        
        elif action == "db_stats":
            stats = self.db.get_stats()
            await websocket.send(json.dumps({
                "event": "db_stats",
                **stats
            }))
        
        elif action == "db_get_settings":
            settings = self.db.get_all_settings()
            await websocket.send(json.dumps({
                "event": "db_settings",
                "settings": settings
            }))
        
        elif action == "db_set_setting":
            key = data.get("key")
            value = data.get("value")
            encrypted = data.get("encrypted", False)
            if key is not None:
                self.db.set_setting(key, value, encrypted)
                await websocket.send(json.dumps({
                    "event": "db_setting_saved",
                    "key": key
                }))
        
        else:
            await websocket.send(json.dumps({
                "event": "unknown_action",
                "action": action
            }))
    
    async def start(self):
        """Inicia el servidor."""
        # Guardar referencia al event loop actual (necesario para callbacks desde otros threads)
        self.loop = asyncio.get_running_loop()
        self.event_queue = asyncio.Queue()
        
        # Iniciar TTS
        self.tts.start()
        
        # Iniciar watcher (sin thread de polling - usaremos asyncio)
        self.watcher = CopilotChatWatcher(self.on_chat_event)
        self.watcher.start(use_watchdog=False)
        # Detener el thread de polling si se inició
        self.watcher.polling_active = False
        if hasattr(self.watcher, '_stop_event'):
            self.watcher._stop_event.set()
        
        # Iniciar procesador de eventos
        asyncio.create_task(self.event_processor())
        asyncio.create_task(self._heartbeat())
        
        # Polling basado en asyncio (no se throttlea como los threads)
        asyncio.create_task(self._asyncio_polling())

        # Iniciar servidor WebSocket
        logger.info(f"Iniciando servidor WebSocket en ws://{self.host}:{self.port}")

        async with websockets.serve(
            self.handler, self.host, self.port,
            ping_interval=20, ping_timeout=10,
        ):
            logger.info("Servidor listo. Esperando conexiones...")
            await asyncio.Future()  # Run forever
    
    async def _asyncio_polling(self):
        """
        Polling de archivos usando asyncio en lugar de threads.
        
        Los threads de Windows se throttlean cuando la consola no tiene foco,
        pero asyncio tasks no. Esto resuelve el problema de VS Code.
        """
        logger.info("[ASYNCIO-POLL] Iniciando polling basado en asyncio")
        consecutive_no_change = 0
        
        while True:
            try:
                # Poll
                changed = self.watcher.poll_once()
                
                if changed:
                    consecutive_no_change = 0
                    # Burst mode: varios polls rápidos tras detectar cambio
                    for _ in range(5):
                        await asyncio.sleep(0.02)
                        self.watcher.poll_once()
                else:
                    consecutive_no_change += 1
                
                # Polling adaptativo
                if consecutive_no_change < 10:
                    await asyncio.sleep(0.05)  # 50ms cuando hay actividad
                elif consecutive_no_change < 50:
                    await asyncio.sleep(0.1)   # 100ms normal
                else:
                    await asyncio.sleep(0.2)   # 200ms cuando está inactivo
                    
            except Exception as e:
                logger.error(f"[ASYNCIO-POLL] Error: {e}")
                await asyncio.sleep(1)
    
    async def _heartbeat(self):
        """Heartbeat cada 5s — mantiene WebSocket vivo y permite detectar si el servidor responde."""
        while True:
            await asyncio.sleep(5)
            if self.clients:
                await self.broadcast({"event": "heartbeat", "ts": __import__("time").time()})
    
    def stop(self):
        """Detiene el servidor."""
        if self.watcher:
            self.watcher.stop()
        if self.tts:
            self.tts.stop()


def start_http_server(port: int, directory: Path, audio_dir: Path = None):
    """Inicia un servidor HTTP para servir la UI y archivos de audio."""
    
    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)
        
        def log_message(self, format, *args):
            if args and '404' in str(args[0]):
                logger.warning(f"HTTP 404: {args}")
        
        def do_GET(self):
            # Servir archivos de audio desde /audio/
            if self.path.startswith('/audio/') and audio_dir:
                filename = self.path[7:]  # Quitar '/audio/'
                audio_file = audio_dir / filename
                if audio_file.exists() and audio_file.suffix == '.mp3':
                    self.send_response(200)
                    self.send_header('Content-Type', 'audio/mpeg')
                    self.send_header('Content-Length', audio_file.stat().st_size)
                    self.send_header('Access-Control-Allow-Origin', '*')
                    self.end_headers()
                    with open(audio_file, 'rb') as f:
                        self.wfile.write(f.read())
                    return
                else:
                    self.send_error(404, 'Audio not found')
                    return
            # Servir archivos normales de la UI
            super().do_GET()
        
        def end_headers(self):
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
            self.send_header('Access-Control-Allow-Origin', '*')
            super().end_headers()
    
    with socketserver.TCPServer(("", port), QuietHandler) as httpd:
        logger.info(f"Servidor HTTP para UI en http://localhost:{port}")
        httpd.serve_forever()


async def main():
    parser = argparse.ArgumentParser(description="Copilot Chat WebSocket Server")
    parser.add_argument("--host", default="localhost", help="Host WebSocket (default: localhost)")
    parser.add_argument("--port", type=int, default=8765, help="Puerto WebSocket (default: 8765)")
    parser.add_argument("--ui-port", type=int, default=8080, help="Puerto HTTP para UI (default: 8080)")
    parser.add_argument("--llm-model", default=None, help="Modelo Gemini para procesar TTS (usa GEMINI_MODEL del .env)")
    args = parser.parse_args()
    
    # Directorio de la UI (relativo al script)
    ui_dir = Path(__file__).parent.parent / "ui"
    audio_dir = Path(__file__).parent / "audio_cache"
    
    # Crear directorio de audio si no existe (para Docker)
    audio_dir.mkdir(exist_ok=True)
    
    if not ui_dir.exists():
        logger.error(f"Directorio UI no encontrado: {ui_dir}")
        return
    
    # Iniciar servidor HTTP en thread separado
    http_thread = threading.Thread(
        target=start_http_server,
        args=(args.ui_port, ui_dir, audio_dir),
        daemon=True
    )
    http_thread.start()
    
    # Iniciar servidor WebSocket
    server = CopilotWebSocketServer(host=args.host, port=args.port, llm_model=args.llm_model)
    
    logger.info(f"\n{'='*50}")
    logger.info(f"  ABRE EN EL NAVEGADOR: http://localhost:{args.ui_port}")
    logger.info(f"{'='*50}\n")
    
    try:
        await server.start()
    except KeyboardInterrupt:
        logger.info("Interrumpido por usuario")
    finally:
        server.stop()


if __name__ == "__main__":
    asyncio.run(main())
