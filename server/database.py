"""
database.py
===========
Persistencia de datos con SQLite.

Tablas:
- sessions: Sesiones de chat (una por archivo JSONL monitoreado)
- messages: Mensajes de cada sesión
- settings: Configuración del usuario (API keys, preferencias)
"""

import sqlite3
import json
import os
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# Ubicación de la base de datos
DB_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DB_DIR / "vibe_voice.db"


@dataclass
class MessageRecord:
    """Registro de un mensaje en la base de datos."""
    id: Optional[int] = None
    session_id: int = 0
    role: str = ""  # user, assistant, system
    text: str = ""
    request_index: int = 0
    timestamp: str = ""
    ide: str = ""  # cursor, vscode, vscode-insiders
    has_thinking: bool = False
    thinking_text: str = ""
    created_at: str = ""


@dataclass
class SessionRecord:
    """Registro de una sesión de chat."""
    id: Optional[int] = None
    name: str = ""
    ide: str = ""
    source_file: str = ""  # Ruta del archivo JSONL original
    created_at: str = ""
    updated_at: str = ""
    message_count: int = 0


class Database:
    """Gestor de base de datos SQLite."""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._ensure_db_dir()
        self._init_db()
    
    def _ensure_db_dir(self):
        """Crea el directorio de datos si no existe."""
        DB_DIR.mkdir(parents=True, exist_ok=True)
    
    @contextmanager
    def _get_connection(self):
        """Context manager para conexiones a la base de datos."""
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
    
    def _init_db(self):
        """Inicializa el esquema de la base de datos."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Tabla de sesiones
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    ide TEXT NOT NULL DEFAULT '',
                    source_file TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    message_count INTEGER DEFAULT 0
                )
            """)
            
            # Tabla de mensajes
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    request_index INTEGER DEFAULT 0,
                    timestamp TEXT DEFAULT '',
                    ide TEXT DEFAULT '',
                    has_thinking INTEGER DEFAULT 0,
                    thinking_text TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                )
            """)
            
            # Tabla de configuración
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    encrypted INTEGER DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
            """)
            
            # Índices para búsquedas rápidas
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_role ON messages(role)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_ide ON sessions(ide)")
            
            logger.info(f"[DB] Base de datos inicializada: {DB_PATH}")
    
    # ══════════════════════════════════════════════════════════════════════════════
    # SESIONES
    # ══════════════════════════════════════════════════════════════════════════════
    
    def create_session(self, name: str, ide: str = "", source_file: str = "") -> int:
        """Crea una nueva sesión y devuelve su ID."""
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO sessions (name, ide, source_file, created_at, updated_at, message_count)
                VALUES (?, ?, ?, ?, ?, 0)
            """, (name, ide, source_file, now, now))
            session_id = cursor.lastrowid
            logger.info(f"[DB] Sesión creada: {session_id} - {name}")
            return session_id
    
    def get_session(self, session_id: int) -> Optional[SessionRecord]:
        """Obtiene una sesión por ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
            row = cursor.fetchone()
            if row:
                return SessionRecord(**dict(row))
            return None
    
    def get_sessions(self, limit: int = 50, offset: int = 0, ide: str = None) -> List[SessionRecord]:
        """Lista sesiones ordenadas por fecha de actualización."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if ide:
                cursor.execute("""
                    SELECT * FROM sessions WHERE ide = ?
                    ORDER BY updated_at DESC LIMIT ? OFFSET ?
                """, (ide, limit, offset))
            else:
                cursor.execute("""
                    SELECT * FROM sessions 
                    ORDER BY updated_at DESC LIMIT ? OFFSET ?
                """, (limit, offset))
            return [SessionRecord(**dict(row)) for row in cursor.fetchall()]
    
    def get_or_create_session_by_source(self, source_file: str, ide: str = "") -> int:
        """Obtiene o crea una sesión basada en el archivo fuente."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM sessions WHERE source_file = ?", (source_file,))
            row = cursor.fetchone()
            if row:
                return row["id"]
        
        # Crear nueva sesión
        name = Path(source_file).stem if source_file else f"Sesión {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        return self.create_session(name=name, ide=ide, source_file=source_file)
    
    def update_session(self, session_id: int, **kwargs):
        """Actualiza campos de una sesión."""
        if not kwargs:
            return
        
        kwargs["updated_at"] = datetime.now().isoformat()
        fields = ", ".join(f"{k} = ?" for k in kwargs.keys())
        values = list(kwargs.values()) + [session_id]
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"UPDATE sessions SET {fields} WHERE id = ?", values)
    
    def delete_session(self, session_id: int):
        """Elimina una sesión y todos sus mensajes."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            cursor.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            logger.info(f"[DB] Sesión eliminada: {session_id}")
    
    # ══════════════════════════════════════════════════════════════════════════════
    # MENSAJES
    # ══════════════════════════════════════════════════════════════════════════════
    
    def add_message(self, session_id: int, role: str, text: str, 
                    request_index: int = 0, timestamp: str = "", 
                    ide: str = "", has_thinking: bool = False,
                    thinking_text: str = "") -> int:
        """Agrega un mensaje a una sesión."""
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO messages 
                (session_id, role, text, request_index, timestamp, ide, has_thinking, thinking_text, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (session_id, role, text, request_index, timestamp, ide, 
                  1 if has_thinking else 0, thinking_text, now))
            
            # Actualizar contador de la sesión
            cursor.execute("""
                UPDATE sessions SET message_count = message_count + 1, updated_at = ?
                WHERE id = ?
            """, (now, session_id))
            
            return cursor.lastrowid
    
    def get_messages(self, session_id: int, limit: int = 100, offset: int = 0) -> List[MessageRecord]:
        """Obtiene mensajes de una sesión ordenados cronológicamente."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM messages WHERE session_id = ?
                ORDER BY created_at ASC LIMIT ? OFFSET ?
            """, (session_id, limit, offset))
            return [MessageRecord(
                id=row["id"],
                session_id=row["session_id"],
                role=row["role"],
                text=row["text"],
                request_index=row["request_index"],
                timestamp=row["timestamp"],
                ide=row["ide"],
                has_thinking=bool(row["has_thinking"]),
                thinking_text=row["thinking_text"],
                created_at=row["created_at"]
            ) for row in cursor.fetchall()]
    
    def get_all_messages(self, limit: int = 200, offset: int = 0, 
                         role: str = None, ide: str = None) -> List[MessageRecord]:
        """Obtiene mensajes de todas las sesiones con filtros opcionales."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            query = "SELECT * FROM messages WHERE 1=1"
            params = []
            
            if role:
                query += " AND role = ?"
                params.append(role)
            if ide:
                query += " AND ide = ?"
                params.append(ide)
            
            query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            
            cursor.execute(query, params)
            return [MessageRecord(
                id=row["id"],
                session_id=row["session_id"],
                role=row["role"],
                text=row["text"],
                request_index=row["request_index"],
                timestamp=row["timestamp"],
                ide=row["ide"],
                has_thinking=bool(row["has_thinking"]),
                thinking_text=row["thinking_text"],
                created_at=row["created_at"]
            ) for row in cursor.fetchall()]
    
    def search_messages(self, query: str, limit: int = 50) -> List[MessageRecord]:
        """Busca mensajes por texto."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM messages 
                WHERE text LIKE ? OR thinking_text LIKE ?
                ORDER BY created_at DESC LIMIT ?
            """, (f"%{query}%", f"%{query}%", limit))
            return [MessageRecord(
                id=row["id"],
                session_id=row["session_id"],
                role=row["role"],
                text=row["text"],
                request_index=row["request_index"],
                timestamp=row["timestamp"],
                ide=row["ide"],
                has_thinking=bool(row["has_thinking"]),
                thinking_text=row["thinking_text"],
                created_at=row["created_at"]
            ) for row in cursor.fetchall()]
    
    def message_exists(self, session_id: int, role: str, text: str, request_index: int) -> bool:
        """Verifica si un mensaje ya existe (para evitar duplicados)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Comparar por hash del texto para evitar comparaciones de texto largo
            cursor.execute("""
                SELECT 1 FROM messages 
                WHERE session_id = ? AND role = ? AND request_index = ?
                AND LENGTH(text) = LENGTH(?)
                LIMIT 1
            """, (session_id, role, request_index, text))
            return cursor.fetchone() is not None
    
    # ══════════════════════════════════════════════════════════════════════════════
    # CONFIGURACIÓN
    # ══════════════════════════════════════════════════════════════════════════════
    
    def set_setting(self, key: str, value: Any, encrypted: bool = False):
        """Guarda una configuración."""
        now = datetime.now().isoformat()
        value_str = json.dumps(value) if not isinstance(value, str) else value
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO settings (key, value, encrypted, updated_at)
                VALUES (?, ?, ?, ?)
            """, (key, value_str, 1 if encrypted else 0, now))
    
    def get_setting(self, key: str, default: Any = None) -> Any:
        """Obtiene una configuración."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            if row:
                try:
                    return json.loads(row["value"])
                except json.JSONDecodeError:
                    return row["value"]
            return default
    
    def get_all_settings(self) -> Dict[str, Any]:
        """Obtiene todas las configuraciones."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value, encrypted FROM settings")
            settings = {}
            for row in cursor.fetchall():
                try:
                    settings[row["key"]] = json.loads(row["value"])
                except json.JSONDecodeError:
                    settings[row["key"]] = row["value"]
            return settings
    
    def delete_setting(self, key: str):
        """Elimina una configuración."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM settings WHERE key = ?", (key,))
    
    # ══════════════════════════════════════════════════════════════════════════════
    # EXPORTACIÓN
    # ══════════════════════════════════════════════════════════════════════════════
    
    def export_session_markdown(self, session_id: int) -> str:
        """Exporta una sesión como Markdown."""
        session = self.get_session(session_id)
        if not session:
            return ""
        
        messages = self.get_messages(session_id, limit=10000)
        
        lines = [
            f"# {session.name}",
            f"",
            f"**IDE:** {session.ide}",
            f"**Fecha:** {session.created_at[:10]}",
            f"**Mensajes:** {session.message_count}",
            f"",
            "---",
            ""
        ]
        
        for msg in messages:
            role_label = "👤 Usuario" if msg.role == "user" else "🤖 Asistente"
            lines.append(f"### {role_label}")
            lines.append(f"")
            
            if msg.has_thinking and msg.thinking_text:
                lines.append(f"<details>")
                lines.append(f"<summary>💭 Razonamiento</summary>")
                lines.append(f"")
                lines.append(msg.thinking_text)
                lines.append(f"")
                lines.append(f"</details>")
                lines.append(f"")
            
            lines.append(msg.text)
            lines.append(f"")
            lines.append("---")
            lines.append("")
        
        return "\n".join(lines)
    
    def export_session_json(self, session_id: int) -> str:
        """Exporta una sesión como JSON."""
        session = self.get_session(session_id)
        if not session:
            return "{}"
        
        messages = self.get_messages(session_id, limit=10000)
        
        data = {
            "session": asdict(session),
            "messages": [asdict(m) for m in messages]
        }
        
        return json.dumps(data, indent=2, ensure_ascii=False)
    
    # ══════════════════════════════════════════════════════════════════════════════
    # ESTADÍSTICAS
    # ══════════════════════════════════════════════════════════════════════════════
    
    def get_stats(self) -> Dict[str, Any]:
        """Obtiene estadísticas generales."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("SELECT COUNT(*) as count FROM sessions")
            total_sessions = cursor.fetchone()["count"]
            
            cursor.execute("SELECT COUNT(*) as count FROM messages")
            total_messages = cursor.fetchone()["count"]
            
            cursor.execute("""
                SELECT ide, COUNT(*) as count FROM sessions 
                GROUP BY ide
            """)
            sessions_by_ide = {row["ide"]: row["count"] for row in cursor.fetchall()}
            
            cursor.execute("""
                SELECT role, COUNT(*) as count FROM messages 
                GROUP BY role
            """)
            messages_by_role = {row["role"]: row["count"] for row in cursor.fetchall()}
            
            return {
                "total_sessions": total_sessions,
                "total_messages": total_messages,
                "sessions_by_ide": sessions_by_ide,
                "messages_by_role": messages_by_role,
                "db_path": str(DB_PATH),
                "db_size_mb": round(DB_PATH.stat().st_size / 1024 / 1024, 2) if DB_PATH.exists() else 0
            }


# Singleton global
db = Database()


def get_db() -> Database:
    """Obtiene la instancia de la base de datos."""
    return db
