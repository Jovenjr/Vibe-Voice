"""
Helpers de rutas multiplataforma para sesiones locales de Vibe Voice.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, List


DOCKER_FOLDER_MAP = {
    "Code - Insiders": "code-insiders",
    "Code": "code",
}


def get_home_dir() -> Path:
    """Retorna el home del usuario actual en Windows o Linux."""
    return Path(os.environ.get("USERPROFILE") or os.environ.get("HOME") or Path.home())


def get_config_dir() -> Path:
    """Retorna APPDATA en Windows o XDG_CONFIG_HOME/~/.config en Linux."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata)

    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home)

    return get_home_dir() / ".config"


def normalize_path_for_match(path: str | Path | None) -> str:
    """Normaliza separadores para hacer matching estable entre SOs."""
    return str(path or "").replace("\\", "/").lower()


def infer_ide_from_path(file_path: str | Path | None) -> str:
    """Infere el IDE según la ruta del archivo de sesión."""
    normalized = normalize_path_for_match(file_path)

    if "/.codex/sessions/" in normalized:
        return "codex"
    if "/.copilot/session-state/" in normalized:
        return "copilot"
    if "/.cursor/projects/" in normalized:
        return "cursor"
    if "/kiro/user/history/" in normalized:
        return "kiro"
    if "/code - insiders/" in normalized:
        return "vscode-insiders"
    if "/code/user/" in normalized:
        return "vscode"

    return ""


def get_workspace_storage_roots(ide_folders: Iterable[str]) -> List[Path]:
    """Retorna roots de workspaceStorage considerando host o Docker."""
    docker_mode = os.environ.get("DOCKER_MODE") == "1"
    appdata_override = os.environ.get("APPDATA_OVERRIDE")

    roots: List[Path] = []
    if docker_mode and appdata_override:
        base = Path(appdata_override)
        for folder in ide_folders:
            docker_folder = DOCKER_FOLDER_MAP.get(folder, folder.lower())
            roots.append(base / docker_folder / "workspaceStorage")
        return roots

    config_dir = get_config_dir()
    for folder in ide_folders:
        roots.append(config_dir / folder / "User" / "workspaceStorage")
    return roots


def get_empty_window_chat_roots(ide_folders: Iterable[str]) -> List[Path]:
    """Retorna roots de emptyWindowChatSessions fuera de Docker."""
    if os.environ.get("DOCKER_MODE") == "1":
        return []

    config_dir = get_config_dir()
    roots: List[Path] = []
    for folder in ide_folders:
        if folder in ("Code - Insiders", "Code"):
            roots.append(config_dir / folder / "User" / "globalStorage" / "emptyWindowChatSessions")
    return roots


def get_cursor_projects_dir() -> Path:
    return get_home_dir() / ".cursor" / "projects"


def get_codex_sessions_dir() -> Path:
    override = os.environ.get("CODEX_SESSIONS_OVERRIDE")
    if override:
        return Path(override)
    return get_home_dir() / ".codex" / "sessions"


def get_copilot_session_state_dir() -> Path:
    override = os.environ.get("COPILOT_SESSIONS_OVERRIDE")
    if override:
        return Path(override)
    return get_home_dir() / ".copilot" / "session-state"


def get_kiro_history_dir() -> Path:
    return get_config_dir() / "Kiro" / "User" / "History"
