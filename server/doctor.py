"""
Doctor de entorno para Vibe Voice.

Valida prerequisitos y estado operativo basico para reducir friccion de instalacion.
"""

from __future__ import annotations

import argparse
import importlib
import shutil
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from platform_paths import (
    get_codex_sessions_dir,
    get_copilot_session_state_dir,
    get_cursor_projects_dir,
    get_kiro_history_dir,
    get_workspace_storage_roots,
)


ROOT_DIR = Path(__file__).resolve().parent.parent


@dataclass
class CheckResult:
    level: str  # OK, WARN, FAIL
    name: str
    detail: str


def _port_open(host: str, port: int, timeout: float = 0.4) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def _check_import(module_name: str) -> bool:
    try:
        importlib.import_module(module_name)
        return True
    except Exception:
        return False


def _fmt_path(path: Path) -> str:
    return str(path).replace("\\", "/")


def run_checks() -> list[CheckResult]:
    results: list[CheckResult] = []

    py_ok = sys.version_info >= (3, 11)
    results.append(CheckResult(
        "OK" if py_ok else "FAIL",
        "Python",
        f"{sys.version.split()[0]} (requerido >= 3.11)",
    ))

    required_imports = [
        ("websockets", "websockets"),
        ("watchdog", "watchdog"),
        ("edge-tts", "edge_tts"),
        ("python-dotenv", "dotenv"),
    ]
    for label, mod in required_imports:
        ok = _check_import(mod)
        results.append(CheckResult(
            "OK" if ok else "FAIL",
            f"Dependencia requerida: {label}",
            "instalada" if ok else f"falta ({mod})",
        ))

    optional_imports = [
        ("pygame", "pygame", "audio local en Windows"),
        ("groq", "groq", "STT Groq"),
        ("google-genai", "google.genai", "Gemini STT/LLM"),
        ("whisper", "whisper", "STT local"),
        ("google-cloud-speech", "google.cloud.speech", "STT Google Cloud"),
        ("pyautogui", "pyautogui", "bridge local Windows"),
        ("pyperclip", "pyperclip", "bridge local Windows"),
        ("requests", "requests", "integracion Telegram"),
    ]
    for label, mod, purpose in optional_imports:
        ok = _check_import(mod)
        results.append(CheckResult(
            "OK" if ok else "WARN",
            f"Dependencia opcional: {label}",
            f"instalada ({purpose})" if ok else f"no instalada ({purpose})",
        ))

    env_file = ROOT_DIR / ".env"
    results.append(CheckResult(
        "OK" if env_file.exists() else "WARN",
        "Archivo .env",
        _fmt_path(env_file) if env_file.exists() else "no encontrado (copia .env.example)",
    ))

    ffmpeg_ok = shutil.which("ffmpeg") is not None
    results.append(CheckResult(
        "OK" if ffmpeg_ok else "WARN",
        "ffmpeg",
        "en PATH" if ffmpeg_ok else "no encontrado (recomendado para audio/STT)",
    ))

    ui_dir = ROOT_DIR / "ui"
    results.append(CheckResult(
        "OK" if ui_dir.exists() else "FAIL",
        "Directorio UI",
        _fmt_path(ui_dir) if ui_dir.exists() else "no encontrado",
    ))

    data_dir = ROOT_DIR / "data"
    data_dir.mkdir(exist_ok=True)
    writable = data_dir.exists() and data_dir.is_dir()
    results.append(CheckResult(
        "OK" if writable else "FAIL",
        "Directorio data",
        _fmt_path(data_dir) if writable else "no se pudo crear/acceder",
    ))

    session_paths: Iterable[tuple[str, Path]] = [
        ("Codex sessions", get_codex_sessions_dir()),
        ("Copilot session-state", get_copilot_session_state_dir()),
        ("Cursor projects", get_cursor_projects_dir()),
        ("Kiro history", get_kiro_history_dir()),
    ]
    for label, path in session_paths:
        exists = path.exists()
        results.append(CheckResult(
            "OK" if exists else "WARN",
            label,
            _fmt_path(path) if exists else f"no encontrado ({_fmt_path(path)})",
        ))

    vscode_roots = get_workspace_storage_roots(["Code - Insiders", "Code"])
    vscode_exists = [p for p in vscode_roots if p.exists()]
    results.append(CheckResult(
        "OK" if vscode_exists else "WARN",
        "VS Code workspaceStorage",
        ", ".join(_fmt_path(p) for p in vscode_exists) if vscode_exists else "no detectado",
    ))

    ws_up = _port_open("127.0.0.1", 8765)
    ui_up = _port_open("127.0.0.1", 8080)
    results.append(CheckResult(
        "OK" if ws_up else "WARN",
        "WebSocket local (127.0.0.1:8765)",
        "activo" if ws_up else "inactivo",
    ))
    results.append(CheckResult(
        "OK" if ui_up else "WARN",
        "UI local (127.0.0.1:8080)",
        "activa" if ui_up else "inactiva",
    ))

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Validador de entorno para Vibe Voice")
    parser.add_argument("--strict", action="store_true", help="trata WARN como error (exit code != 0)")
    args = parser.parse_args()

    results = run_checks()
    fails = [r for r in results if r.level == "FAIL"]
    warns = [r for r in results if r.level == "WARN"]

    print("== Vibe Voice Doctor ==")
    for r in results:
        print(f"[{r.level:4}] {r.name}: {r.detail}")

    print("")
    print(f"Resumen: OK={len([r for r in results if r.level == 'OK'])} WARN={len(warns)} FAIL={len(fails)}")

    if fails:
        return 1
    if args.strict and warns:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
