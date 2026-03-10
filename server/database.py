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

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

# Ubicación de la base de datos
DB_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DB_DIR / "vibe_voice.db"
SETTINGS_KEY_PATH = DB_DIR / ".settings.key"
ENCRYPTED_PREFIX = "enc:"


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
    cwd: str = ""
    git_root: str = ""
    branch: str = ""
    repository: str = ""
    model: str = ""
    models_used: str = ""
    archived: bool = False
    archived_at: str = ""


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
        self._cipher = self._build_cipher()
        self._init_db()
        self._migrate_legacy_encrypted_settings()
    
    def _ensure_db_dir(self):
        """Crea el directorio de datos si no existe."""
        DB_DIR.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(DB_DIR, 0o700)
        except OSError:
            pass

    def _build_cipher(self) -> Fernet:
        """Construye el cifrador para secretos persistidos."""
        env_key = os.getenv("VIBE_VOICE_SETTINGS_KEY", "").strip()
        if env_key:
            key_bytes = env_key.encode("utf-8")
        else:
            if not SETTINGS_KEY_PATH.exists():
                SETTINGS_KEY_PATH.write_bytes(Fernet.generate_key())
                try:
                    os.chmod(SETTINGS_KEY_PATH, 0o600)
                except OSError:
                    pass
            key_bytes = SETTINGS_KEY_PATH.read_bytes().strip()
        return Fernet(key_bytes)

    def _serialize_setting(self, value: Any) -> str:
        return json.dumps(value) if not isinstance(value, str) else value

    def _encrypt_setting_value(self, value_str: str) -> str:
        token = self._cipher.encrypt(value_str.encode("utf-8")).decode("utf-8")
        return f"{ENCRYPTED_PREFIX}{token}"

    def _decrypt_setting_value(self, value_str: str, encrypted: bool) -> str:
        if not encrypted:
            return value_str
        if not value_str.startswith(ENCRYPTED_PREFIX):
            return value_str
        token = value_str[len(ENCRYPTED_PREFIX):].encode("utf-8")
        return self._cipher.decrypt(token).decode("utf-8")

    def _decode_setting_value(self, value_str: str, encrypted: bool) -> Any:
        raw_value = self._decrypt_setting_value(value_str, encrypted)
        try:
            return json.loads(raw_value)
        except json.JSONDecodeError:
            return raw_value

    def _migrate_legacy_encrypted_settings(self):
        """Convierte settings marcados como encrypted pero aún guardados en claro."""
        migrated = 0
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM settings WHERE encrypted = 1")
            rows = cursor.fetchall()
            for row in rows:
                value_str = row["value"]
                if isinstance(value_str, str) and value_str.startswith(ENCRYPTED_PREFIX):
                    continue
                cursor.execute(
                    "UPDATE settings SET value = ?, updated_at = ? WHERE key = ?",
                    (
                        self._encrypt_setting_value(value_str),
                        datetime.now().isoformat(),
                        row["key"],
                    )
                )
                migrated += 1
        if migrated:
            logger.info(f"[DB] Settings sensibles migrados a cifrado real: {migrated}")
    
    @contextmanager
    def _get_connection(self):
        """Context manager para conexiones a la base de datos."""
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            os.chmod(DB_PATH, 0o600)
        except OSError:
            pass
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
                    message_count INTEGER DEFAULT 0,
                    cwd TEXT NOT NULL DEFAULT '',
                    git_root TEXT NOT NULL DEFAULT '',
                    branch TEXT NOT NULL DEFAULT '',
                    repository TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    models_used TEXT NOT NULL DEFAULT '',
                    archived INTEGER NOT NULL DEFAULT 0,
                    archived_at TEXT NOT NULL DEFAULT ''
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
            self._migrate_session_columns(cursor)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_archived ON sessions(archived)")
            
            logger.info(f"[DB] Base de datos inicializada: {DB_PATH}")
        try:
            os.chmod(DB_PATH, 0o600)
        except OSError:
            pass

    def _migrate_session_columns(self, cursor):
        """Agrega columnas nuevas a sessions sin romper bases existentes."""
        cursor.execute("PRAGMA table_info(sessions)")
        existing_columns = {row["name"] for row in cursor.fetchall()}
        desired_columns = {
            "cwd": "TEXT NOT NULL DEFAULT ''",
            "git_root": "TEXT NOT NULL DEFAULT ''",
            "branch": "TEXT NOT NULL DEFAULT ''",
            "repository": "TEXT NOT NULL DEFAULT ''",
            "model": "TEXT NOT NULL DEFAULT ''",
            "models_used": "TEXT NOT NULL DEFAULT ''",
            "archived": "INTEGER NOT NULL DEFAULT 0",
            "archived_at": "TEXT NOT NULL DEFAULT ''",
        }

        for column_name, column_def in desired_columns.items():
            if column_name in existing_columns:
                continue
            cursor.execute(f"ALTER TABLE sessions ADD COLUMN {column_name} {column_def}")
            logger.info(f"[DB] Columna migrada en sessions: {column_name}")
    
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

    def get_session_by_source(self, source_file: str) -> Optional[SessionRecord]:
        """Obtiene una sesión por su archivo fuente."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM sessions WHERE source_file = ?", (source_file,))
            row = cursor.fetchone()
            if row:
                return SessionRecord(**dict(row))
            return None
    
    def get_sessions(
        self,
        limit: int = 50,
        offset: int = 0,
        ide: str = None,
        archived: Optional[bool] = False,
    ) -> List[SessionRecord]:
        """Lista sesiones ordenadas por fecha de actualización."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            clauses = []
            params = []

            if ide:
                clauses.append("ide = ?")
                params.append(ide)
            if archived is True:
                clauses.append("archived = 1")
            elif archived is False:
                clauses.append("archived = 0")

            query = "SELECT * FROM sessions"
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            query += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            cursor.execute(query, params)
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

    def replace_session_messages(
        self,
        session_id: int,
        messages: List[Dict[str, Any]],
        *,
        name: Optional[str] = None,
        ide: Optional[str] = None,
        source_file: Optional[str] = None,
        session_fields: Optional[Dict[str, Any]] = None,
    ):
        """Reemplaza todos los mensajes de una sesión usando un snapshot completo."""
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))

            for msg in messages:
                cursor.execute("""
                    INSERT INTO messages
                    (session_id, role, text, request_index, timestamp, ide, has_thinking, thinking_text, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    session_id,
                    msg.get("role", ""),
                    msg.get("text", ""),
                    msg.get("request_index", 0),
                    msg.get("timestamp", ""),
                    msg.get("ide", ""),
                    1 if msg.get("has_thinking", False) else 0,
                    msg.get("thinking_text", ""),
                    now,
                ))

            updates = {
                "updated_at": now,
                "message_count": len(messages),
            }
            if name is not None:
                updates["name"] = name
            if ide is not None:
                updates["ide"] = ide
            if source_file is not None:
                updates["source_file"] = source_file
            if session_fields:
                for key, value in session_fields.items():
                    if value is None:
                        continue
                    updates[key] = value

            fields = ", ".join(f"{key} = ?" for key in updates.keys())
            values = list(updates.values()) + [session_id]
            cursor.execute(f"UPDATE sessions SET {fields} WHERE id = ?", values)

    def set_session_archived(self, session_id: int, archived: bool) -> None:
        """Archiva o desarchiva una sesión."""
        now = datetime.now().isoformat()
        archived_at = now if archived else ""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE sessions
                SET archived = ?, archived_at = ?, updated_at = ?
                WHERE id = ?
            """, (1 if archived else 0, archived_at, now, session_id))
    
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
    
    def search_messages(
        self,
        query: str,
        limit: int = 50,
        archived: Optional[bool] = False,
    ) -> List[MessageRecord]:
        """Busca mensajes por texto."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            sql = """
                SELECT m.* FROM messages m
                JOIN sessions s ON s.id = m.session_id
                WHERE (m.text LIKE ? OR m.thinking_text LIKE ?)
            """
            params = [f"%{query}%", f"%{query}%"]
            if archived is True:
                sql += " AND s.archived = 1"
            elif archived is False:
                sql += " AND s.archived = 0"
            sql += " ORDER BY m.created_at DESC LIMIT ?"
            params.append(limit)
            cursor.execute(sql, params)
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
        value_str = self._serialize_setting(value)
        if encrypted:
            value_str = self._encrypt_setting_value(value_str)
        
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
            cursor.execute("SELECT value, encrypted FROM settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            if row:
                try:
                    return self._decode_setting_value(row["value"], bool(row["encrypted"]))
                except InvalidToken:
                    logger.error(f"[DB] No se pudo descifrar setting '{key}'")
                    return default
            return default
    
    def get_all_settings(self) -> Dict[str, Any]:
        """Obtiene todas las configuraciones."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value, encrypted FROM settings")
            settings = {}
            for row in cursor.fetchall():
                try:
                    settings[row["key"]] = self._decode_setting_value(row["value"], bool(row["encrypted"]))
                except InvalidToken:
                    logger.error(f"[DB] No se pudo descifrar setting '{row['key']}'")
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

            cursor.execute("SELECT COUNT(*) as count FROM sessions WHERE archived = 0")
            active_sessions = cursor.fetchone()["count"]

            cursor.execute("SELECT COUNT(*) as count FROM sessions WHERE archived = 1")
            archived_sessions = cursor.fetchone()["count"]
            
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
                "active_sessions": active_sessions,
                "archived_sessions": archived_sessions,
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
