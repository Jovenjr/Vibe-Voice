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
import re
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime

from platform_paths import (
    get_copilot_session_state_dir,
    get_codex_sessions_dir,
    get_cursor_projects_dir,
    get_empty_window_chat_roots,
    get_workspace_storage_roots,
)

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
        Soporta formato VS Code (kind 0/1/2), Cursor (role + message),
        Codex CLI (type + payload) y Copilot CLI (type + data).
        """
        entry = json.loads(line.strip())

        # Detectar formato Codex CLI
        if "type" in entry and "payload" in entry:
            return self._parse_codex_line(entry, state)

        # Detectar formato Copilot CLI
        if "type" in entry and "data" in entry:
            return self._parse_copilot_line(entry, state)

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

    def _parse_copilot_line(self, entry: Dict, state: Dict) -> Tuple[str, Any]:
        """Parsea una línea en formato Copilot CLI events.jsonl."""
        event_type = entry.get("type", "")
        data = entry.get("data", {})
        timestamp = self._parse_codex_timestamp(entry.get("timestamp", ""))

        if event_type == "session.start" and isinstance(data, dict):
            session_id = data.get("sessionId", "")
            context = data.get("context", {}) if isinstance(data.get("context"), dict) else {}
            cwd = context.get("cwd", "")
            git_root = context.get("gitRoot", "")
            branch = context.get("branch", "")
            if session_id:
                state["sessionId"] = session_id
            state["customTitle"] = Path(cwd or git_root).name if (cwd or git_root) else "Copilot CLI"
            state["copilot_meta"] = {
                "cwd": cwd,
                "git_root": git_root,
                "branch": branch,
                "repository": context.get("repository", ""),
                "producer": data.get("producer", ""),
                "session_format_version": data.get("version", ""),
                "copilot_version": data.get("copilotVersion", ""),
            }
            self._remember_copilot_model(state, data)
            return ("copilot_meta", state["copilot_meta"])

        self._remember_copilot_model(state, data)

        if event_type == "user.message" and isinstance(data, dict):
            text = (data.get("content") or "").strip()
            if not text:
                return ("unknown", entry)
            messages = state.setdefault("copilot_messages", [])
            message_data = {
                "role": "user",
                "text": text,
                "timestamp": timestamp,
                "index": len(messages),
            }
            messages.append(message_data)
            self._update_copilot_activity(
                state,
                status="waiting",
                label="Esperando agente",
                detail="Mensaje enviado",
                timestamp=timestamp,
            )
            return ("copilot_user", message_data)

        if event_type == "assistant.turn_start":
            snapshot = self._update_copilot_activity(
                state,
                status="reasoning",
                label="Pensando",
                detail="Copilot comenzo un turno",
                timestamp=timestamp,
            )
            return ("copilot_activity", snapshot)

        if event_type == "assistant.message" and isinstance(data, dict):
            messages = state.setdefault("copilot_messages", [])
            content = (data.get("content") or "").strip()
            if content:
                message_data = {
                    "role": "assistant",
                    "text": content,
                    "timestamp": timestamp,
                    "index": len(messages),
                }
                messages.append(message_data)
                self._update_copilot_activity(
                    state,
                    status="final",
                    label="Respuesta lista",
                    detail=content[:180],
                    timestamp=timestamp,
                )
                return ("copilot_assistant", message_data)

            tool_requests = data.get("toolRequests", [])
            if isinstance(tool_requests, list) and tool_requests:
                detail = self._format_copilot_tool_detail(tool_requests[-1])
                snapshot = self._update_copilot_activity(
                    state,
                    status="working",
                    label="Preparando herramientas",
                    detail=detail,
                    timestamp=timestamp,
                )
                return ("copilot_activity", snapshot)
            return ("unknown", entry)

        if event_type == "tool.execution_start" and isinstance(data, dict):
            snapshot = self._update_copilot_activity(
                state,
                status="tool_running",
                label="Ejecutando herramienta",
                detail=self._format_copilot_tool_detail(data),
                timestamp=timestamp,
                tool_call=data,
            )
            return ("copilot_activity", snapshot)

        if event_type == "tool.execution_complete" and isinstance(data, dict):
            success = bool(data.get("success", False))
            snapshot = self._update_copilot_activity(
                state,
                status="working" if success else "error",
                label="Procesando resultados" if success else "Error",
                detail=self._format_copilot_tool_result(data),
                timestamp=timestamp,
                tool_output=data,
            )
            return ("copilot_activity", snapshot)

        if event_type == "assistant.turn_end":
            snapshot = self._update_copilot_activity(
                state,
                status="final",
                label="Terminado",
                detail="Turno completado",
                timestamp=timestamp,
            )
            return ("copilot_activity", snapshot)

        if event_type == "session.error" and isinstance(data, dict):
            snapshot = self._update_copilot_activity(
                state,
                status="error",
                label="Error",
                detail=(data.get("message") or data.get("errorType") or "Error de sesion")[:180],
                timestamp=timestamp,
            )
            return ("copilot_activity", snapshot)

        if event_type == "session.info" and isinstance(data, dict):
            detail = (data.get("message") or "").strip()
            if detail:
                snapshot = self._update_copilot_activity(
                    state,
                    status="waiting" if "login" in detail.lower() else "working",
                    label="Info de sesion",
                    detail=detail[:180],
                    timestamp=timestamp,
                )
                return ("copilot_activity", snapshot)

        return ("unknown", entry)

    def _extract_model_name(self, value: Any, depth: int = 0) -> str:
        """Busca nombres de modelo en payloads anidados de Copilot CLI."""
        if depth > 4:
            return ""
        if isinstance(value, dict):
            for key in ("model", "modelId", "model_id"):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
            for nested_value in value.values():
                candidate = self._extract_model_name(nested_value, depth + 1)
                if candidate:
                    return candidate
            return ""
        if isinstance(value, list):
            for item in value:
                candidate = self._extract_model_name(item, depth + 1)
                if candidate:
                    return candidate
        return ""

    def _remember_copilot_model(self, state: Dict, payload: Any) -> str:
        """Guarda el modelo más reciente y el historial corto de modelos usados."""
        model_name = self._extract_model_name(payload)
        if not model_name:
            return ""

        models_used = state.setdefault("copilot_models", [])
        if model_name in models_used:
            models_used.remove(model_name)
        models_used.append(model_name)
        if len(models_used) > 6:
            del models_used[:-6]

        meta = state.setdefault("copilot_meta", {})
        meta["model"] = model_name
        meta["models_used"] = list(models_used)
        return model_name

    def _parse_codex_line(self, entry: Dict, state: Dict) -> Tuple[str, Any]:
        """Parsea una línea en formato Codex CLI rollout JSONL."""
        line_type = entry.get("type", "")
        payload = entry.get("payload", {})
        timestamp = self._parse_codex_timestamp(entry.get("timestamp", ""))

        if line_type == "session_meta":
            session_id = payload.get("id", "")
            cwd = payload.get("cwd", "")
            if session_id:
                state["sessionId"] = session_id
            state["customTitle"] = Path(cwd).name if cwd else "Codex CLI"
            state["codex_meta"] = payload
            return ("codex_meta", payload)

        if line_type == "event_msg" and isinstance(payload, dict):
            event_type = payload.get("type")
            if event_type == "task_started":
                snapshot = self._update_codex_activity(
                    state,
                    status="working",
                    label="Iniciando tarea",
                    detail="Codex comenzo a trabajar",
                    timestamp=timestamp,
                    task_started=True,
                )
                return ("codex_activity", snapshot)

            if event_type == "agent_message":
                phase = payload.get("phase", "")
                detail = (payload.get("message") or "").strip()
                snapshot = self._update_codex_activity(
                    state,
                    status="commentary" if phase == "commentary" else "working",
                    label="Comentando" if phase == "commentary" else "Trabajando",
                    detail=detail[:180],
                    timestamp=timestamp,
                )
                return ("codex_activity", snapshot)

            if event_type == "token_count":
                snapshot = self._update_codex_activity(
                    state,
                    status="working",
                    label="Trabajando",
                    detail="Actualizando conteo de tokens",
                    timestamp=timestamp,
                )
                return ("codex_activity", snapshot)

            if event_type == "user_message":
                text = (payload.get("message") or "").strip()
                if not text:
                    return ("unknown", entry)
                if "codex_messages" not in state:
                    state["codex_messages"] = []
                message_data = {
                    "role": "user",
                    "text": text,
                    "phase": "user_message",
                    "timestamp": timestamp,
                    "index": len(state["codex_messages"]),
                }
                state["codex_messages"].append(message_data)
                self._update_codex_activity(
                    state,
                    status="working",
                    label="Esperando agente",
                    detail="Mensaje enviado",
                    timestamp=timestamp,
                )
                return ("codex_user", message_data)
            if event_type == "task_complete":
                snapshot = self._update_codex_activity(
                    state,
                    status="final",
                    label="Terminado",
                    detail="Tarea completada",
                    timestamp=timestamp,
                    task_completed=True,
                )
                return ("codex_activity", snapshot)
            return ("unknown", entry)

        if line_type == "response_item" and isinstance(payload, dict):
            payload_type = payload.get("type")

            if payload_type == "reasoning":
                snapshot = self._update_codex_activity(
                    state,
                    status="reasoning",
                    label="Pensando",
                    detail="Razonamiento interno activo",
                    timestamp=timestamp,
                )
                return ("codex_activity", snapshot)

            if payload_type == "function_call":
                tool_name = payload.get("name", "tool")
                tool_detail = self._format_codex_tool_detail(payload)
                snapshot = self._update_codex_activity(
                    state,
                    status="tool_running",
                    label="Ejecutando herramienta",
                    detail=tool_detail,
                    timestamp=timestamp,
                    tool_call=payload,
                )
                return ("codex_activity", snapshot)

            if payload_type == "function_call_output":
                output_text = payload.get("output", "") if isinstance(payload.get("output", ""), str) else ""
                exit_code = self._extract_tool_exit_code(output_text)
                if exit_code not in (None, 0):
                    snapshot = self._update_codex_activity(
                        state,
                        status="error",
                        label="Error",
                        detail=f"Herramienta fallo (exit {exit_code})",
                        timestamp=timestamp,
                        tool_output=payload,
                    )
                    return ("codex_activity", snapshot)

                snapshot = self._update_codex_activity(
                    state,
                    status="working",
                    label="Procesando resultados",
                    detail="Herramienta terminada",
                    timestamp=timestamp,
                    tool_output=payload,
                )
                return ("codex_activity", snapshot)

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

        message_data = {
            "role": role,
            "text": text,
            "phase": payload.get("phase", ""),
            "timestamp": timestamp,
            "index": len(state["codex_messages"]),
        }
        state["codex_messages"].append(message_data)
        phase = payload.get("phase", "")
        if phase == "commentary":
            self._update_codex_activity(
                state,
                status="commentary",
                label="Comentando",
                detail=text[:180],
                timestamp=timestamp,
            )
        else:
            self._update_codex_activity(
                state,
                status="final",
                label="Respuesta lista",
                detail=text[:180],
                timestamp=timestamp,
                mark_final_message=True,
            )

        if role == "user":
            return ("codex_user", message_data)
        return ("codex_assistant", message_data)

    def _ensure_codex_activity(self, state: Dict) -> Dict[str, Any]:
        activity = state.get("codex_activity")
        if not isinstance(activity, dict):
            activity = {
                "status": "idle",
                "label": "Sin actividad",
                "detail": "",
                "timestamp": 0,
                "_open_tools": {},
                "active_task_count": 0,
                "last_task_started_at": 0,
                "last_task_completed_at": 0,
                "has_final_message": False,
            }
            state["codex_activity"] = activity
        activity.setdefault("_open_tools", {})
        activity.setdefault("active_task_count", 0)
        activity.setdefault("last_task_started_at", 0)
        activity.setdefault("last_task_completed_at", 0)
        activity.setdefault("has_final_message", False)
        return activity

    def _snapshot_codex_activity(self, activity: Dict[str, Any]) -> Dict[str, Any]:
        return self._snapshot_activity(activity)

    def _snapshot_activity(self, activity: Dict[str, Any]) -> Dict[str, Any]:
        open_tools = activity.get("_open_tools", {})
        tool_names = [tool.get("label", "") for tool in open_tools.values() if tool.get("label")]
        current_tool = tool_names[-1] if tool_names else ""
        pending_approval_count = sum(1 for tool in open_tools.values() if tool.get("requires_confirmation"))
        return {
            "status": activity.get("status", "idle"),
            "label": activity.get("label", "Sin actividad"),
            "detail": activity.get("detail", ""),
            "timestamp": activity.get("timestamp", 0),
            "open_tool_count": len(open_tools),
            "pending_approval_count": pending_approval_count,
            "current_tool": current_tool,
            "open_tools": tool_names[-3:],
            "active_task_count": activity.get("active_task_count", 0),
        }

    def _parse_tool_arguments(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        arguments = payload.get("arguments")
        if isinstance(arguments, str):
            try:
                return json.loads(arguments)
            except Exception:
                return {}
        if isinstance(arguments, dict):
            return arguments
        return {}

    def _format_codex_tool_detail(self, payload: Dict[str, Any]) -> str:
        name = payload.get("name", "tool")
        detail = name
        parsed_args = self._parse_tool_arguments(payload)

        if name == "exec_command":
            cmd = parsed_args.get("cmd", "")
            if cmd:
                detail = f"{name}: {cmd[:80]}"
        elif name == "apply_patch":
            detail = "apply_patch"
        elif parsed_args:
            first_key = next(iter(parsed_args.keys()), "")
            if first_key:
                detail = f"{name}: {first_key}"
        return detail[:180]

    def _extract_tool_exit_code(self, output_text: str) -> Optional[int]:
        """Extrae código de salida desde function_call_output si está presente."""
        if not output_text:
            return None
        match = re.search(r"Process exited with code\s+(-?\d+)", output_text)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
        return None

    def _tool_requires_confirmation(self, payload: Dict[str, Any]) -> bool:
        parsed_args = self._parse_tool_arguments(payload)
        return parsed_args.get("sandbox_permissions") == "require_escalated"

    def _ensure_copilot_activity(self, state: Dict) -> Dict[str, Any]:
        activity = state.get("copilot_activity")
        if not isinstance(activity, dict):
            activity = {
                "status": "idle",
                "label": "Sin actividad",
                "detail": "",
                "timestamp": 0,
                "_open_tools": {},
            }
            state["copilot_activity"] = activity
        activity.setdefault("_open_tools", {})
        return activity

    def _format_copilot_tool_detail(self, payload: Dict[str, Any]) -> str:
        name = payload.get("toolName") or payload.get("name") or "tool"
        args = payload.get("arguments", {})
        detail = name
        if isinstance(args, dict):
            if name in ("bash", "read_bash", "write_bash", "stop_bash", "list_bash"):
                command = args.get("command", "")
                if command:
                    detail = f"{name}: {command[:80]}"
            elif name == "view":
                path = args.get("path", "")
                if path:
                    detail = f"{name}: {path}"
            elif name == "ask_user":
                question = args.get("question", "")
                if question:
                    detail = f"{name}: {question[:80]}"
            else:
                first_key = next(iter(args.keys()), "")
                if first_key:
                    detail = f"{name}: {first_key}"
        return detail[:180]

    def _copilot_tool_requires_confirmation(self, payload: Dict[str, Any]) -> bool:
        tool_name = payload.get("toolName") or payload.get("name") or ""
        return tool_name == "ask_user"

    def _format_copilot_tool_result(self, payload: Dict[str, Any]) -> str:
        result = payload.get("result", {})
        if isinstance(result, dict):
            text = (result.get("content") or result.get("detailedContent") or "").strip()
            if text:
                return text[:180]
        return "Herramienta terminada" if payload.get("success", False) else "Herramienta fallo"

    def _update_copilot_activity(
        self,
        state: Dict,
        *,
        status: str,
        label: str,
        detail: str,
        timestamp: int,
        tool_call: Optional[Dict[str, Any]] = None,
        tool_output: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        activity = self._ensure_copilot_activity(state)
        open_tools = activity["_open_tools"]

        if tool_call:
            call_id = tool_call.get("toolCallId", "")
            if call_id:
                requires_confirmation = self._copilot_tool_requires_confirmation(tool_call)
                open_tools[call_id] = {
                    "name": tool_call.get("toolName", "tool"),
                    "label": self._format_copilot_tool_detail(tool_call),
                    "requires_confirmation": requires_confirmation,
                }
                if requires_confirmation:
                    status = "waiting_input"
                    label = "Esperando confirmacion"
                    detail = open_tools[call_id]["label"]

        if tool_output:
            call_id = tool_output.get("toolCallId", "")
            success = bool(tool_output.get("success", False))
            if call_id:
                open_tools.pop(call_id, None)
            if not success:
                status = "error"
                label = "Error"
            elif any(tool.get("requires_confirmation") for tool in open_tools.values()):
                status = "waiting_input"
                label = "Esperando confirmacion"
                detail = list(open_tools.values())[-1].get("label", detail)
            elif open_tools:
                status = "tool_running"
                label = "Ejecutando herramienta"
                detail = list(open_tools.values())[-1].get("label", detail)

        activity["status"] = status
        activity["label"] = label
        activity["detail"] = detail
        activity["timestamp"] = timestamp
        return self._snapshot_activity(activity)

    def _update_codex_activity(
        self,
        state: Dict,
        *,
        status: str,
        label: str,
        detail: str,
        timestamp: int,
        tool_call: Optional[Dict[str, Any]] = None,
        tool_output: Optional[Dict[str, Any]] = None,
        task_started: bool = False,
        task_completed: bool = False,
        mark_final_message: bool = False,
    ) -> Dict[str, Any]:
        activity = self._ensure_codex_activity(state)
        open_tools = activity["_open_tools"]
        if task_started:
            activity["active_task_count"] = max(0, int(activity.get("active_task_count", 0))) + 1
            activity["last_task_started_at"] = timestamp
            activity["has_final_message"] = False

        if task_completed:
            activity["active_task_count"] = max(0, int(activity.get("active_task_count", 0)) - 1)
            activity["last_task_completed_at"] = timestamp
            activity["has_final_message"] = True
            open_tools.clear()

        if mark_final_message:
            activity["has_final_message"] = True
            activity["active_task_count"] = 0
            open_tools.clear()

        if tool_call:
            call_id = tool_call.get("call_id", "")
            if call_id:
                requires_confirmation = self._tool_requires_confirmation(tool_call)
                open_tools[call_id] = {
                    "name": tool_call.get("name", "tool"),
                    "label": self._format_codex_tool_detail(tool_call),
                    "requires_confirmation": requires_confirmation,
                }
                if requires_confirmation:
                    status = "waiting_input"
                    label = "Esperando confirmacion"
                    detail = open_tools[call_id]["label"]

        if tool_output:
            call_id = tool_output.get("call_id", "")
            if call_id:
                open_tools.pop(call_id, None)
            if any(tool.get("requires_confirmation") for tool in open_tools.values()):
                status = "waiting_input"
                label = "Esperando confirmacion"
                detail = list(open_tools.values())[-1].get("label", detail)
            elif open_tools:
                status = "tool_running"
                label = "Ejecutando herramienta"
                detail = list(open_tools.values())[-1].get("label", detail)

        if status not in {"error", "final"}:
            if any(tool.get("requires_confirmation") for tool in open_tools.values()):
                status = "waiting_input"
                label = "Esperando confirmacion"
                detail = list(open_tools.values())[-1].get("label", detail)
            elif open_tools:
                status = "tool_running"
                label = "Ejecutando herramienta"
                detail = list(open_tools.values())[-1].get("label", detail)
            elif activity.get("active_task_count", 0) <= 0:
                if activity.get("has_final_message") or activity.get("last_task_completed_at", 0) >= activity.get("last_task_started_at", 0):
                    status = "final"
                    label = "Terminado"
                else:
                    status = "idle"
                    label = "Sin actividad"

        activity["status"] = status
        activity["label"] = label
        activity["detail"] = detail
        activity["timestamp"] = timestamp
        return self._snapshot_codex_activity(activity)

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

        if session.raw_state.get("copilot_messages"):
            return [
                ChatMessage(
                    role=msg.get("role", ""),
                    text=msg.get("text", ""),
                    timestamp=msg.get("timestamp", 0),
                    request_index=msg.get("index", 0),
                    is_complete=True
                )
                for msg in session.raw_state.get("copilot_messages", [])
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

    def get_agent_activity(self, session: SessionState) -> Optional[Dict[str, Any]]:
        """Obtiene el snapshot actual de actividad del agente para la sesión."""
        activity = session.raw_state.get("codex_activity")
        if isinstance(activity, dict):
            return self._snapshot_codex_activity(activity)
        activity = session.raw_state.get("copilot_activity")
        if isinstance(activity, dict):
            return self._snapshot_activity(activity)
        return None

    def get_codex_activity(self, session: SessionState) -> Optional[Dict[str, Any]]:
        """Compatibilidad con código existente."""
        activity = session.raw_state.get("codex_activity")
        if not isinstance(activity, dict):
            return None
        return self._snapshot_codex_activity(activity)

    def get_session_metadata(self, session: SessionState) -> Dict[str, Any]:
        """Obtiene metadatos persistibles y listos para la UI."""
        raw_state = session.raw_state if isinstance(session.raw_state, dict) else {}

        if isinstance(raw_state.get("copilot_meta"), dict):
            meta = dict(raw_state.get("copilot_meta", {}))
            models_used = meta.get("models_used", raw_state.get("copilot_models", []))
            if not isinstance(models_used, list):
                models_used = []
            cwd = meta.get("cwd") or meta.get("git_root") or ""
            return {
                "source_kind": "copilot",
                "cwd": meta.get("cwd", ""),
                "cwd_name": Path(cwd).name if cwd else "",
                "git_root": meta.get("git_root", ""),
                "branch": meta.get("branch", ""),
                "repository": meta.get("repository", ""),
                "model": meta.get("model", ""),
                "models_used": models_used,
                "producer": meta.get("producer", ""),
                "copilot_version": meta.get("copilot_version", ""),
            }

        if isinstance(raw_state.get("codex_meta"), dict):
            meta = dict(raw_state.get("codex_meta", {}))
            cwd = meta.get("cwd", "")
            return {
                "source_kind": "codex",
                "cwd": cwd,
                "cwd_name": Path(cwd).name if cwd else "",
                "git_root": meta.get("git_root", ""),
                "branch": meta.get("branch", ""),
                "repository": meta.get("repository", ""),
                "model": meta.get("model", ""),
                "models_used": [],
            }

        return {
            "source_kind": "",
            "cwd": "",
            "cwd_name": "",
            "git_root": "",
            "branch": "",
            "repository": "",
            "model": "",
            "models_used": [],
        }


# IDEs soportados
SUPPORTED_IDES = {
    "all": {"name": "Todos", "folders": ["Code - Insiders", "Code"], "include_cursor": True, "include_kiro": True, "include_codex": True, "include_copilot": True},
    "vscode-insiders": {"name": "VS Code Insiders", "folders": ["Code - Insiders"], "include_cursor": False, "include_kiro": False, "include_codex": False, "include_copilot": False},
    "vscode": {"name": "VS Code", "folders": ["Code"], "include_cursor": False, "include_kiro": False, "include_codex": False, "include_copilot": False},
    "cursor": {"name": "Cursor", "folders": [], "include_cursor": True, "include_kiro": False, "include_codex": False, "include_copilot": False},
    "kiro": {"name": "Kiro", "folders": [], "include_cursor": False, "include_kiro": True, "include_codex": False, "include_copilot": False},
    "codex": {"name": "Codex CLI", "folders": [], "include_cursor": False, "include_kiro": False, "include_codex": True, "include_copilot": False},
    "copilot": {"name": "Copilot CLI", "folders": [], "include_cursor": False, "include_kiro": False, "include_codex": False, "include_copilot": True},
}


def find_most_recent_session_file(ide_filter: str = "all") -> Optional[Path]:
    """
    Busca el archivo JSONL de sesión más reciente.
    
    Args:
        ide_filter: "all", "vscode-insiders", "vscode", o "cursor"
    """
    import logging
    logger = logging.getLogger(__name__)
    
    # Obtener configuración según filtro
    ide_config = SUPPORTED_IDES.get(ide_filter, SUPPORTED_IDES["all"])
    folders = ide_config["folders"]
    include_cursor = ide_config.get("include_cursor", False)
    include_codex = ide_config.get("include_codex", False)
    include_copilot = ide_config.get("include_copilot", False)
    
    logger.debug(f"[FIND] Filtro: {ide_filter}, folders: {folders}, cursor: {include_cursor}")
    
    best_file = None
    best_mtime = 0
    
    # Buscar en VS Code / VS Code Insiders
    for ws_storage in get_workspace_storage_roots(folders):
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
    for empty_sessions in get_empty_window_chat_roots(folders):
        if empty_sessions.exists():
            for jsonl_file in empty_sessions.glob("*.jsonl"):
                mtime = jsonl_file.stat().st_mtime
                if mtime > best_mtime:
                    best_mtime = mtime
                    best_file = jsonl_file
    
    # Buscar en Cursor (ubicación diferente: ~/.cursor/projects/*/agent-transcripts/*/*.jsonl)
    if include_cursor:
        cursor_projects = get_cursor_projects_dir()
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
        codex_sessions = get_codex_sessions_dir()
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

    if include_copilot:
        copilot_sessions = get_copilot_session_state_dir()
        if copilot_sessions.exists():
            logger.debug(f"[FIND] Buscando en Copilot CLI: {copilot_sessions}")
            for jsonl_file in copilot_sessions.glob("*/events.jsonl"):
                try:
                    mtime = jsonl_file.stat().st_mtime
                except FileNotFoundError:
                    continue
                if mtime > best_mtime:
                    best_mtime = mtime
                    best_file = jsonl_file
                    logger.debug(f"[FIND] Candidato Copilot CLI: {jsonl_file}")
        else:
            logger.debug(f"[FIND] No existe carpeta Copilot CLI: {copilot_sessions}")
    
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
