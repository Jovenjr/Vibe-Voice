"""
Puente local para Vibe Voice.

Expone un WebSocket local en ws://127.0.0.1:8766 para que la UI web pueda
entregar texto transcrito y pegarlo con Ctrl+V en la app de Windows donde
quede el cursor activo.
"""

from __future__ import annotations

import asyncio
import ctypes
import ctypes.wintypes
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    PYAUTOGUI_AVAILABLE = True
except ImportError:
    pyautogui = None
    PYAUTOGUI_AVAILABLE = False

try:
    import pyperclip
    PYPERCLIP_AVAILABLE = True
except ImportError:
    pyperclip = None
    PYPERCLIP_AVAILABLE = False

try:
    import websockets
    from websockets.server import WebSocketServerProtocol
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    websockets = None
    WebSocketServerProtocol = object
    WEBSOCKETS_AVAILABLE = False


APP_DIR = Path(__file__).resolve().parent
LOG_FILE = APP_DIR / "paste_bridge.log"
HOST = os.getenv("VIBE_VOICE_BRIDGE_HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = int(os.getenv("VIBE_VOICE_BRIDGE_PORT", "8766"))
DEFAULT_SEND_ENTER = os.getenv("VIBE_VOICE_BRIDGE_SEND_ENTER", "0").strip() == "1"
BRIDGE_NAME = os.getenv("VIBE_VOICE_BRIDGE_NAME", os.getenv("COMPUTERNAME", "Windows")).strip() or "Windows"

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8",
)
logger = logging.getLogger("paste_bridge")
CLIENTS: set[WebSocketServerProtocol] = set()
MAIN_LOOP: asyncio.AbstractEventLoop | None = None
LAST_TARGET_HWND: int | None = None
HOTKEY_ID = 1
WM_HOTKEY = 0x0312
VK_F8 = 0x77


def ensure_console_streams() -> None:
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


def show_message_box(message: str, title: str = "Vibe Voice Paste Bridge") -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, message, title, 0x00000010)
    except Exception:
        pass


def dependency_message() -> str:
    missing = []
    if not PYAUTOGUI_AVAILABLE:
        missing.append("pyautogui")
    if not PYPERCLIP_AVAILABLE:
        missing.append("pyperclip")
    if missing:
        return "Faltan dependencias locales: " + ", ".join(missing)
    return "Listo para pegar texto en Windows. F8 inicia/detiene dictado."


def _user32():
    return ctypes.windll.user32


def get_foreground_window() -> int | None:
    try:
        hwnd = int(_user32().GetForegroundWindow())
        return hwnd or None
    except Exception:
        return None


def focus_window(hwnd: int | None) -> None:
    if not hwnd:
        return
    try:
        user32 = _user32()
        user32.ShowWindow(hwnd, 5)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.08)
    except Exception:
        logger.exception("No se pudo recuperar el foco de la ventana objetivo")


async def broadcast_json(payload: dict) -> None:
    stale: list[WebSocketServerProtocol] = []
    for client in list(CLIENTS):
        try:
            await send_json(client, payload)
        except Exception:
            stale.append(client)
    for client in stale:
        CLIENTS.discard(client)


def notify_hotkey_pressed() -> None:
    if MAIN_LOOP is None:
        return
    future = asyncio.run_coroutine_threadsafe(
        broadcast_json(
            {
                "type": "bridge.hotkey",
                "hotkey": "F8",
                "action": "toggle_stt",
            }
        ),
        MAIN_LOOP,
    )
    try:
        future.result(timeout=2)
    except Exception:
        logger.exception("No se pudo notificar la hotkey a la UI")


def hotkey_loop() -> None:
    user32 = _user32()
    if not user32.RegisterHotKey(None, HOTKEY_ID, 0, VK_F8):
        logger.error("No se pudo registrar la hotkey F8")
        return

    logger.info("Hotkey global registrada: F8")
    msg = ctypes.wintypes.MSG()
    try:
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                global LAST_TARGET_HWND
                LAST_TARGET_HWND = get_foreground_window()
                logger.info("F8 detectada. Ventana objetivo: %s", LAST_TARGET_HWND)
                notify_hotkey_pressed()
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
    finally:
        user32.UnregisterHotKey(None, HOTKEY_ID)


def paste_text(text: str, send_enter: bool = False) -> None:
    if not PYPERCLIP_AVAILABLE or not PYAUTOGUI_AVAILABLE:
        raise RuntimeError(dependency_message())

    focus_window(LAST_TARGET_HWND)
    pyperclip.copy(text)
    time.sleep(0.08)
    pyautogui.hotkey("ctrl", "v")
    if send_enter:
        time.sleep(0.12)
        pyautogui.press("enter")


async def send_json(websocket: WebSocketServerProtocol, payload: dict) -> None:
    await websocket.send(json.dumps(payload, ensure_ascii=False))


async def handle_connection(websocket: WebSocketServerProtocol) -> None:
    logger.info("Cliente conectado al puente local")
    CLIENTS.add(websocket)
    await send_json(
        websocket,
        {
            "type": "hello",
            "ok": True,
            "bridge_name": BRIDGE_NAME,
            "message": dependency_message(),
            "send_enter_default": DEFAULT_SEND_ENTER,
            "hotkey": "F8",
        },
    )

    try:
        async for raw_message in websocket:
            try:
                payload = json.loads(raw_message)
            except json.JSONDecodeError:
                await send_json(websocket, {"type": "error", "message": "JSON inválido"})
                continue

            action = payload.get("type") or payload.get("action") or ""
            if action in {"hello", "ping"}:
                await send_json(
                    websocket,
                    {
                        "type": "bridge.status",
                        "ok": True,
                        "bridge_name": BRIDGE_NAME,
                        "message": dependency_message(),
                        "hotkey": "F8",
                    },
                )
                continue

            if action != "paste":
                await send_json(websocket, {"type": "error", "message": f"Acción no soportada: {action}"})
                continue

            text = payload.get("text")
            if not isinstance(text, str) or not text.strip():
                await send_json(websocket, {"type": "paste.result", "ok": False, "message": "Texto vacío"})
                continue

            send_enter = bool(payload.get("send_enter", DEFAULT_SEND_ENTER))
            try:
                paste_text(text, send_enter=send_enter)
                logger.info("Texto pegado (%s chars)", len(text))
                await send_json(
                    websocket,
                    {
                        "type": "paste.result",
                        "ok": True,
                        "message": "Texto pegado correctamente.",
                        "char_count": len(text),
                        "send_enter": send_enter,
                    },
                )
            except Exception as exc:
                logger.exception("No se pudo pegar el texto")
                await send_json(
                    websocket,
                    {
                        "type": "paste.result",
                        "ok": False,
                        "message": str(exc),
                    },
                )
    finally:
        CLIENTS.discard(websocket)


async def main() -> None:
    global MAIN_LOOP
    if not WEBSOCKETS_AVAILABLE:
        message = "Instala la dependencia 'websockets' para ejecutar el puente local."
        logger.error(message)
        show_message_box(message)
        raise SystemExit(1)

    MAIN_LOOP = asyncio.get_running_loop()
    threading.Thread(target=hotkey_loop, daemon=True, name="VibeVoiceHotkey").start()
    logger.info("Iniciando puente local en ws://%s:%s", HOST, PORT)
    async with websockets.serve(
        handle_connection,
        HOST,
        PORT,
        ping_interval=20,
        ping_timeout=20,
        max_size=1_000_000,
    ):
        await asyncio.Future()


if __name__ == "__main__":
    ensure_console_streams()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Puente local detenido manualmente")
