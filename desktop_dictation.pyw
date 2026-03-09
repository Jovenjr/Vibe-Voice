"""
App de escritorio flotante para dictado con Whisper local o API.

Flujo:
1. Deja el cursor en cualquier app.
2. Haz clic en "Grabar".
3. Habla.
4. Haz clic en "Detener".
5. La app transcribe y pega el texto automáticamente
   en la última ventana externa que tenía el foco.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import json
import logging
import mimetypes
import os
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

import pyperclip

try:
    import whisper
    WHISPER_AVAILABLE = True
except ImportError:
    whisper = None
    WHISPER_AVAILABLE = False

try:
    from dotenv import load_dotenv
    DOTENV_AVAILABLE = True
except ImportError:
    load_dotenv = None
    DOTENV_AVAILABLE = False

try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    Groq = None
    GROQ_AVAILABLE = False

try:
    import keyboard
    KEYBOARD_AVAILABLE = True
except ImportError:
    keyboard = None
    KEYBOARD_AVAILABLE = False

try:
    import pyautogui
    PYAUTOGUI_AVAILABLE = True
except ImportError:
    pyautogui = None
    PYAUTOGUI_AVAILABLE = False


APP_DIR = Path(__file__).resolve().parent
AUDIO_DIR = APP_DIR / "audio_cache" / "dictation"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = APP_DIR / "dictation_app.log"

if DOTENV_AVAILABLE:
    load_dotenv(APP_DIR / ".env")
    load_dotenv()


def get_default_provider() -> str:
    configured = os.getenv("DICTATION_PROVIDER", "").strip().lower()
    if configured == "api":
        return "openai"
    if configured in {"local", "openai", "groq", "auto"}:
        return configured
    if os.getenv("GROQ_API_KEY", "").strip():
        return "groq"
    if os.getenv("OPENAI_API_KEY", "").strip():
        return "openai"
    return "local"

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8",
)
logger = logging.getLogger("desktop_dictation")


def ensure_console_streams() -> None:
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

SW_RESTORE = 9
WM_HOTKEY = 0x0312
PM_REMOVE = 0x0001
VK_F8 = 0x77

def list_openal_devices() -> list[str]:
    logger.info("Enumerando dispositivos OpenAL")
    try:
        completed = subprocess.run(
            ["ffmpeg", "-hide_banner", "-f", "openal", "-list_devices", "true", "-i", "dummy"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            creationflags=0x08000000,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("No encontré `ffmpeg` en el PATH.") from exc
    except Exception as exc:
        raise RuntimeError(f"No pude enumerar micrófonos con ffmpeg: {exc}") from exc

    text = "\n".join(part for part in [completed.stdout, completed.stderr] if part)
    devices = []
    for line in text.splitlines():
        match = re.search(r"OpenAL capture devices on this system:\s*$", line)
        if match:
            continue
        match = re.search(r"\]\s+(OpenAL Soft on .+)$", line.strip())
        if match:
            devices.append(match.group(1).strip())

    seen = set()
    deduped = []
    for device in devices:
        if device not in seen:
            seen.add(device)
            deduped.append(device)
    return deduped


def format_device_error(error: Exception, devices: list[str]) -> str:
    details = str(error).strip() or "ffmpeg no pudo abrir el micrófono seleccionado."
    if devices:
        available = "\n".join(f"- {device}" for device in devices[:6])
        return f"{details}\n\nMicrófonos detectados ahora:\n{available}"
    return details


class FFmpegRecorder:
    def __init__(self) -> None:
        self.is_recording = False
        self._process: subprocess.Popen | None = None
        self._output_path: Path | None = None
        self._audio_buffer = bytearray()
        self._reader_thread: threading.Thread | None = None
        self._sample_rate = 16000
        self._channels = 1
        self._sample_width = 2

    def _read_stdout(self) -> None:
        if not self._process or not self._process.stdout:
            return
        try:
            while True:
                chunk = self._process.stdout.read(4096)
                if not chunk:
                    break
                self._audio_buffer.extend(chunk)
        except Exception:
            pass

    def start(self, output_path: Path, device_name: str) -> None:
        logger.info("Iniciando grabación en %s con %s", output_path, device_name)
        self.cleanup()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._output_path = output_path
        self._audio_buffer = bytearray()

        command = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "openal",
            "-i",
            device_name,
            "-ac",
            str(self._channels),
            "-ar",
            str(self._sample_rate),
            "-f",
            "s16le",
            "pipe:1",
        ]

        try:
            self._process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=0x08000000,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("No encontré `ffmpeg` en el PATH.") from exc
        except Exception as exc:
            raise RuntimeError(f"No pude iniciar ffmpeg: {exc}") from exc

        self._reader_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader_thread.start()
        time.sleep(0.35)
        if self._process.poll() is not None:
            error = (self._process.stderr.read() or b"").decode("utf-8", errors="replace").strip()
            self.cleanup()
            logger.error("ffmpeg terminó al iniciar: %s", error)
            raise RuntimeError(error or "ffmpeg no pudo abrir el micrófono seleccionado.")

        self.is_recording = True

    def stop_and_save(self) -> Path:
        if not self.is_recording or not self._process or not self._output_path:
            raise RuntimeError("No hay una grabación activa.")
        logger.info("Deteniendo grabación")

        try:
            self._process.terminate()
            self._process.wait(timeout=5)
        except Exception:
            self._process.terminate()
            try:
                self._process.wait(timeout=3)
            except Exception:
                self._process.kill()
                self._process.wait(timeout=3)

        if self._reader_thread:
            self._reader_thread.join(timeout=2)

        error = (self._process.stderr.read() or b"").decode("utf-8", errors="replace").strip()
        output_path = self._output_path
        audio_bytes = bytes(self._audio_buffer)
        self.cleanup()

        if not audio_bytes:
            logger.error("No se capturó audio. ffmpeg stderr=%s", error)
            raise RuntimeError(error or "ffmpeg no produjo un archivo de audio válido.")

        with wave.open(str(output_path), "wb") as wav_file:
            wav_file.setnchannels(self._channels)
            wav_file.setsampwidth(self._sample_width)
            wav_file.setframerate(self._sample_rate)
            wav_file.writeframes(audio_bytes)

        logger.info("Audio guardado en %s (%s bytes)", output_path, len(audio_bytes))
        return output_path

    def cleanup(self) -> None:
        if self._process and self._process.poll() is None:
            try:
                self._process.terminate()
                self._process.wait(timeout=3)
            except Exception:
                pass
        if self._process and self._process.stdout:
            try:
                self._process.stdout.close()
            except Exception:
                pass
        if self._process and self._process.stderr:
            try:
                self._process.stderr.close()
            except Exception:
                pass
        self._process = None
        self._output_path = None
        self._reader_thread = None
        self.is_recording = False


@dataclass
class DictationResult:
    text: str
    audio_path: Path
    pasted: bool
    warning: str = ""


class LocalWhisperTranscriber:
    def __init__(self) -> None:
        self._loaded_name: str | None = None
        self._model = None
        self._lock = threading.Lock()

    def transcribe(self, audio_path: Path, model_name: str, language: str = "es") -> str:
        if not WHISPER_AVAILABLE:
            raise RuntimeError("Whisper local no está instalado. Instala `openai-whisper` o cambia el proveedor a API.")
        logger.info("Transcribiendo %s con modelo %s", audio_path, model_name)
        with self._lock:
            if self._loaded_name != model_name or self._model is None:
                self._model = whisper.load_model(model_name)
                self._loaded_name = model_name
            result = self._model.transcribe(
                str(audio_path),
                language=language,
                fp16=False,
                verbose=None,
                temperature=0.0,
            )
        return (result.get("text") or "").strip()


class OpenAIAPITranscriber:
    def __init__(self) -> None:
        self.endpoint = os.getenv("OPENAI_AUDIO_TRANSCRIPTIONS_URL", "https://api.openai.com/v1/audio/transcriptions")

    def is_configured(self) -> bool:
        return bool(os.getenv("OPENAI_API_KEY", "").strip())

    def transcribe(self, audio_path: Path, model_name: str, language: str = "es") -> str:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("Configura `OPENAI_API_KEY` para usar transcripción por API.")

        logger.info("Transcribiendo %s con OpenAI API modelo %s", audio_path, model_name)
        fields = {
            "model": model_name,
            "language": language,
            "response_format": "json",
        }
        body, content_type = self._build_multipart(audio_path, fields)
        request = urllib.request.Request(
            self.endpoint,
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": content_type,
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            message = raw
            try:
                payload = json.loads(raw)
                message = payload.get("error", {}).get("message") or raw
            except Exception:
                pass
            raise RuntimeError(f"OpenAI API devolvió {exc.code}: {message}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"No pude conectar con OpenAI API: {exc.reason}") from exc
        except Exception as exc:
            raise RuntimeError(f"No pude transcribir por API: {exc}") from exc

        text = (payload.get("text") or "").strip()
        if not text:
            raise RuntimeError("La API no devolvió texto.")
        return text

    def _build_multipart(self, audio_path: Path, fields: dict[str, str]) -> tuple[bytes, str]:
        boundary = f"----CodexBoundary{uuid.uuid4().hex}"
        file_bytes = audio_path.read_bytes()
        mime_type = mimetypes.guess_type(audio_path.name)[0] or "application/octet-stream"
        chunks: list[bytes] = []

        for key, value in fields.items():
            chunks.append(
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                    f"{value}\r\n"
                ).encode("utf-8")
            )

        chunks.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="{audio_path.name}"\r\n'
                f"Content-Type: {mime_type}\r\n\r\n"
            ).encode("utf-8")
        )
        chunks.append(file_bytes)
        chunks.append(b"\r\n")
        chunks.append(f"--{boundary}--\r\n".encode("utf-8"))

        return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class GroqTranscriber:
    def __init__(self) -> None:
        self._client = None
        self._lock = threading.Lock()

    def is_configured(self) -> bool:
        return bool(os.getenv("GROQ_API_KEY", "").strip())

    def transcribe(self, audio_path: Path, model_name: str, language: str = "es") -> str:
        if not GROQ_AVAILABLE:
            raise RuntimeError("El SDK `groq` no está instalado.")

        api_key = os.getenv("GROQ_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("Configura `GROQ_API_KEY` para usar transcripción con Groq.")

        logger.info("Transcribiendo %s con Groq modelo %s", audio_path, model_name)
        with self._lock:
            if self._client is None:
                self._client = Groq(api_key=api_key)

            with open(audio_path, "rb") as file:
                transcription = self._client.audio.transcriptions.create(
                    file=(audio_path.name, file.read()),
                    model=model_name,
                    language=language,
                    temperature=0,
                    response_format="verbose_json",
                )

        text = getattr(transcription, "text", "") or ""
        if not text and isinstance(transcription, dict):
            text = transcription.get("text", "")
        text = text.strip()
        if not text:
            raise RuntimeError("Groq no devolvió texto.")
        return text


class HybridTranscriber:
    def __init__(self) -> None:
        self.local = LocalWhisperTranscriber()
        self.openai = OpenAIAPITranscriber()
        self.groq = GroqTranscriber()

    def transcribe(
        self,
        audio_path: Path,
        provider: str,
        local_model_name: str,
        openai_model_name: str,
        groq_model_name: str,
        language: str = "es",
    ) -> str:
        if provider == "local":
            return self.local.transcribe(audio_path, model_name=local_model_name, language=language)

        if provider in {"api", "openai"}:
            return self.openai.transcribe(audio_path, model_name=openai_model_name, language=language)

        if provider == "groq":
            return self.groq.transcribe(audio_path, model_name=groq_model_name, language=language)

        if provider == "auto":
            if self.groq.is_configured():
                try:
                    return self.groq.transcribe(audio_path, model_name=groq_model_name, language=language)
                except Exception as exc:
                    logger.warning("Groq falló en modo auto; pruebo OpenAI/local: %s", exc)
            if self.openai.is_configured():
                try:
                    return self.openai.transcribe(audio_path, model_name=openai_model_name, language=language)
                except Exception as exc:
                    logger.warning("OpenAI falló en modo auto; uso Whisper local: %s", exc)
            return self.local.transcribe(audio_path, model_name=local_model_name, language=language)

        raise RuntimeError(f"Proveedor de transcripción desconocido: {provider}")


class WindowTracker:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.last_external_hwnd: int | None = None
        self._root_hwnd = root.winfo_id()
        self._poll_foreground()

    def _poll_foreground(self) -> None:
        hwnd = user32.GetForegroundWindow()
        if hwnd and hwnd != self._root_hwnd:
            self.last_external_hwnd = hwnd
        self.root.after(250, self._poll_foreground)


class GlobalHotkeyManager:
    def __init__(self, root: tk.Tk, callback, hotkey_id: int = 1) -> None:
        self.root = root
        self.callback = callback
        self.hotkey_id = hotkey_id
        self.enabled = False
        self.polling_enabled = False
        self._was_pressed = False

    def bind_f8(self) -> None:
        registered = bool(user32.RegisterHotKey(None, self.hotkey_id, 0, VK_F8))
        self.enabled = registered
        self.polling_enabled = True
        logger.info("Hotkey F8 backend: register=%s polling=%s", registered, True)
        self._poll()
        self._poll_key_state()

    def _poll(self) -> None:
        if not self.polling_enabled:
            return
        msg = wintypes.MSG()
        while self.enabled and user32.PeekMessageW(ctypes.byref(msg), None, WM_HOTKEY, WM_HOTKEY, PM_REMOVE):
            if msg.message == WM_HOTKEY and msg.wParam == self.hotkey_id:
                logger.info("Hotkey F8 detectado por RegisterHotKey")
                self.callback()
        self.root.after(40, self._poll)

    def _poll_key_state(self) -> None:
        if not self.polling_enabled:
            return

        is_pressed = bool(user32.GetAsyncKeyState(VK_F8) & 0x8000)
        if is_pressed and not self._was_pressed:
            logger.info("Hotkey F8 detectado por GetAsyncKeyState")
            self.callback()
        self._was_pressed = is_pressed
        self.root.after(35, self._poll_key_state)

    def unbind(self) -> None:
        self.polling_enabled = False
        self._was_pressed = False
        if self.enabled:
            user32.UnregisterHotKey(None, self.hotkey_id)
            self.enabled = False


def focus_window(hwnd: int | None) -> bool:
    if not hwnd:
        return False

    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)

    current_foreground = user32.GetForegroundWindow()
    current_thread = kernel32.GetCurrentThreadId()
    foreground_thread = user32.GetWindowThreadProcessId(current_foreground, None)
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)

    attached_foreground = False
    attached_target = False

    try:
        if foreground_thread and foreground_thread != current_thread:
            attached_foreground = bool(user32.AttachThreadInput(foreground_thread, current_thread, True))
        if target_thread and target_thread != current_thread:
            attached_target = bool(user32.AttachThreadInput(target_thread, current_thread, True))

        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetFocus(hwnd)
        time.sleep(0.12)
        return user32.GetForegroundWindow() == hwnd
    finally:
        if attached_target:
            user32.AttachThreadInput(target_thread, current_thread, False)
        if attached_foreground:
            user32.AttachThreadInput(foreground_thread, current_thread, False)


def paste_with_clipboard(text: str, target_hwnd: int | None) -> tuple[bool, str]:
    if not text.strip():
        return False, "Whisper no devolvió texto."

    pyperclip.copy(text)
    warning = ""
    focused = focus_window(target_hwnd)
    if not focused:
        warning = "No pude recuperar el foco; dejé el texto en el portapapeles."

    time.sleep(0.10)

    try:
        if KEYBOARD_AVAILABLE:
            keyboard.send("ctrl+v")
            return True, warning
        if PYAUTOGUI_AVAILABLE:
            pyautogui.hotkey("ctrl", "v")
            return True, warning
    except Exception as exc:
        warning = f"No pude pegar automáticamente: {exc}. Dejé el texto en el portapapeles."

    return False, warning or "No encontré un backend para pegar automáticamente."


class DictationApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Dictado Whisper")
        self.root.geometry("380x410")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)

        self.recorder = FFmpegRecorder()
        self.transcriber = HybridTranscriber()
        self.window_tracker = WindowTracker(root)
        self.hotkey_manager = GlobalHotkeyManager(root, lambda: self.root.after(0, self.toggle_recording))
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()

        self.is_busy = False

        self.devices = list_openal_devices()
        self.device_var = tk.StringVar(value=self.devices[0] if self.devices else "")
        self.provider_var = tk.StringVar(value=get_default_provider())
        self.model_var = tk.StringVar(value="base")
        self.openai_model_var = tk.StringVar(value=os.getenv("OPENAI_AUDIO_MODEL", "whisper-1"))
        self.groq_model_var = tk.StringVar(value=os.getenv("GROQ_AUDIO_MODEL", "whisper-large-v3-turbo"))
        self.status_var = tk.StringVar(value="Listo para dictar")
        self.detail_var = tk.StringVar(value="Deja el cursor en tu app y pulsa Grabar.")
        self.hotkey_var = tk.StringVar(value="F8")
        self.hotkey_handle = None
        self.hotkey_backend = tk.StringVar(value="WinAPI")

        self._build_ui()
        self._bind_hotkeys()
        self._drain_events()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=14)
        frame.pack(fill="both", expand=True)

        title = ttk.Label(frame, text="Dictado con Whisper", font=("Segoe UI", 11, "bold"))
        title.pack(anchor="w")

        provider_row = ttk.Frame(frame)
        provider_row.pack(fill="x", pady=(10, 6))
        ttk.Label(provider_row, text="Proveedor:").pack(side="left")
        ttk.OptionMenu(provider_row, self.provider_var, self.provider_var.get(), "local", "groq", "openai", "auto").pack(side="left", padx=(8, 0))

        self.local_model_row = ttk.Frame(frame)
        self.local_model_row.pack(fill="x", pady=(0, 6))
        ttk.Label(self.local_model_row, text="Modelo local:").pack(side="left")
        ttk.OptionMenu(self.local_model_row, self.model_var, self.model_var.get(), "tiny", "base", "small").pack(side="left", padx=(8, 0))

        self.groq_model_row = ttk.Frame(frame)
        ttk.Label(self.groq_model_row, text="Modelo Groq:").pack(side="left")
        ttk.OptionMenu(self.groq_model_row, self.groq_model_var, self.groq_model_var.get(), "whisper-large-v3", "whisper-large-v3-turbo").pack(side="left", padx=(8, 0))

        self.openai_model_row = ttk.Frame(frame)
        ttk.Label(self.openai_model_row, text="Modelo OpenAI:").pack(side="left")
        ttk.OptionMenu(self.openai_model_row, self.openai_model_var, self.openai_model_var.get(), "whisper-1", "gpt-4o-mini-transcribe").pack(side="left", padx=(8, 0))

        device_row = ttk.Frame(frame)
        device_row.pack(fill="x", pady=(0, 8))
        ttk.Label(device_row, text="Micrófono:").pack(side="left")
        self.device_menu = ttk.OptionMenu(device_row, self.device_var, self.device_var.get(), *(self.devices or ["Sin dispositivos"]))
        self.device_menu.pack(side="left", padx=(8, 0), fill="x", expand=True)
        ttk.Button(device_row, text="↻", width=3, command=self.refresh_devices).pack(side="left", padx=(6, 0))

        self.main_button = ttk.Button(frame, text="Grabar", command=self.toggle_recording)
        self.main_button.pack(fill="x", pady=(8, 8), ipady=8)

        ttk.Label(frame, textvariable=self.status_var, foreground="#2563eb").pack(anchor="w")
        ttk.Label(frame, textvariable=self.detail_var, wraplength=300, justify="left").pack(anchor="w", pady=(6, 8))
        ttk.Label(frame, text="Hotkey global: F8 para iniciar/detener sin enfocar esta ventana.").pack(anchor="w", pady=(0, 8))

        ttk.Label(frame, text="Última transcripción:").pack(anchor="w")
        self.output = tk.Text(frame, height=5, wrap="word")
        self.output.pack(fill="both", expand=True)
        self.output.configure(font=("Segoe UI", 9))

        self.provider_var.trace_add("write", lambda *_: self._update_provider_ui())
        self._update_provider_ui()

        if not self.devices:
            self.main_button.configure(state="disabled")
            self.status_var.set("Sin micrófono")
            self.detail_var.set("No encontré micrófonos vía ffmpeg/OpenAL. Revisa permisos de micrófono en Windows.")

    def _update_provider_ui(self) -> None:
        provider = self.provider_var.get()
        self.local_model_row.pack_forget()
        self.groq_model_row.pack_forget()
        self.openai_model_row.pack_forget()

        if provider == "groq":
            self.groq_model_row.pack(fill="x", pady=(0, 6), before=self.main_button)
        elif provider in {"api", "openai"}:
            self.openai_model_row.pack(fill="x", pady=(0, 6), before=self.main_button)
        elif provider == "auto":
            self.local_model_row.pack(fill="x", pady=(0, 6), before=self.main_button)
            self.groq_model_row.pack(fill="x", pady=(0, 6), before=self.main_button)
            self.openai_model_row.pack(fill="x", pady=(0, 6), before=self.main_button)
        else:
            self.local_model_row.pack(fill="x", pady=(0, 6), before=self.main_button)

    def _describe_provider(self) -> str:
        provider = self.provider_var.get()
        if provider == "groq":
            return f"Groq ({self.groq_model_var.get()})"
        if provider in {"api", "openai"}:
            return f"OpenAI API ({self.openai_model_var.get()})"
        if provider == "auto":
            return f"auto: Groq → OpenAI → local ({self.groq_model_var.get()} / {self.openai_model_var.get()} / {self.model_var.get()})"
        return f"Whisper local ({self.model_var.get()})"

    def _bind_hotkeys(self) -> None:
        try:
            self.hotkey_manager.bind_f8()
            self.hotkey_backend.set("WinAPI")
        except Exception as exc:
            logger.warning("No pude registrar F8 con WinAPI: %s", exc)
            if not KEYBOARD_AVAILABLE:
                self.detail_var.set("No pude registrar F8 global; por ahora usa el botón.")
                return
            try:
                self.hotkey_handle = keyboard.add_hotkey("f8", lambda: self.root.after(0, self.toggle_recording))
                self.hotkey_backend.set("keyboard")
            except Exception as inner_exc:
                self.detail_var.set(f"No pude registrar F8 global: {inner_exc}")

    def _unbind_hotkeys(self) -> None:
        self.hotkey_manager.unbind()
        if KEYBOARD_AVAILABLE and self.hotkey_handle is not None:
            try:
                keyboard.remove_hotkey(self.hotkey_handle)
            except Exception:
                pass
            self.hotkey_handle = None

    def refresh_devices(self) -> None:
        self._reload_devices(preserve_selection=False, update_status=True)

    def _reload_devices(self, preserve_selection: bool = True, update_status: bool = False) -> list[str]:
        previous = self.device_var.get()
        try:
            devices = list_openal_devices()
        except Exception as exc:
            if update_status:
                self.status_var.set("Error de audio")
                self.detail_var.set(str(exc))
            raise

        self.devices = devices
        menu = self.device_menu["menu"]
        menu.delete(0, "end")
        values = self.devices or ["Sin dispositivos"]
        for device in values:
            menu.add_command(label=device, command=tk._setit(self.device_var, device))

        selected = values[0]
        if preserve_selection and previous in self.devices:
            selected = previous
        self.device_var.set(selected)
        self.main_button.configure(state="normal" if self.devices else "disabled")
        if update_status and self.devices:
            self.status_var.set("Listo para dictar")
            if selected != previous and previous:
                self.detail_var.set(f"Micrófono actualizado a: {selected}")
            else:
                self.detail_var.set("Micrófono detectado. Ya puedes grabar.")
        elif update_status and not self.devices:
            self.status_var.set("Sin micrófono")
            self.detail_var.set("No encontré micrófonos vía ffmpeg/OpenAL. Revisa permisos de micrófono en Windows.")
        return self.devices

    def toggle_recording(self) -> None:
        if self.is_busy and not self.recorder.is_recording:
            return
        if self.recorder.is_recording:
            self.stop_recording()
        else:
            self.start_recording()

    def start_recording(self) -> None:
        try:
            devices = self._reload_devices(preserve_selection=True, update_status=False)
        except Exception as exc:
            messagebox.showerror("Error de audio", str(exc))
            return

        if not devices:
            self.status_var.set("Sin micrófono")
            self.detail_var.set("No encontré micrófonos vía ffmpeg/OpenAL. Revisa permisos de micrófono en Windows.")
            messagebox.showerror("Sin micrófono", "No encontré un micrófono disponible.")
            return

        try:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            audio_path = AUDIO_DIR / f"dictation_{timestamp}.wav"
            preferred_device = self.device_var.get()
            candidates = [preferred_device] + [device for device in devices if device != preferred_device]
            last_error = None

            for candidate in candidates:
                try:
                    self.recorder.start(audio_path, candidate)
                    if candidate != preferred_device:
                        self.device_var.set(candidate)
                        self.detail_var.set(f"Cambié al micrófono disponible: {candidate}")
                    break
                except Exception as exc:
                    logger.warning("No pude abrir %s: %s", candidate, exc)
                    last_error = exc
                    self.recorder.cleanup()
            else:
                raise RuntimeError(format_device_error(last_error or RuntimeError("No pude iniciar la grabación."), devices))
        except Exception as exc:
            logger.exception("Error al iniciar grabación")
            messagebox.showerror("Error al grabar", f"No pude iniciar la grabación.\n\n{exc}")
            return

        self.is_busy = True
        self.status_var.set("Grabando…")
        self.detail_var.set("Habla ahora. Pulsa Detener para transcribir y pegar.")
        self.main_button.configure(text="Detener", state="normal")

    def stop_recording(self) -> None:
        self.main_button.configure(state="disabled")
        self.status_var.set("Transcribiendo…")
        self.detail_var.set(f"Procesando audio con {self._describe_provider()}. Puedes dejar el cursor donde quieras pegar.")

        provider = self.provider_var.get()
        model_name = self.model_var.get()
        openai_model_name = self.openai_model_var.get()
        groq_model_name = self.groq_model_var.get()
        target_hwnd = self._get_target_hwnd()

        worker = threading.Thread(
            target=self._transcribe_worker,
            args=(provider, model_name, openai_model_name, groq_model_name, target_hwnd),
            daemon=True,
        )
        worker.start()

    def _transcribe_worker(
        self,
        provider: str,
        model_name: str,
        openai_model_name: str,
        groq_model_name: str,
        target_hwnd: int | None,
    ) -> None:
        try:
            recorded_path = self.recorder.stop_and_save()
            text = self.transcriber.transcribe(
                recorded_path,
                provider=provider,
                local_model_name=model_name,
                openai_model_name=openai_model_name,
                groq_model_name=groq_model_name,
            )
            pasted, warning = paste_with_clipboard(text, target_hwnd)
            self.events.put(("success", DictationResult(text=text, audio_path=recorded_path, pasted=pasted, warning=warning)))
        except Exception as exc:
            logger.exception("Error en worker de transcripción")
            self.events.put(("error", exc))

    def _get_target_hwnd(self) -> int | None:
        hwnd = user32.GetForegroundWindow()
        if hwnd and hwnd != self.root.winfo_id():
            return hwnd
        return self.window_tracker.last_external_hwnd

    def _drain_events(self) -> None:
        try:
            while True:
                event_name, payload = self.events.get_nowait()
                if event_name == "success":
                    self._handle_success(payload)
                else:
                    self._handle_error(payload)
        except queue.Empty:
            pass
        self.root.after(120, self._drain_events)

    def _handle_success(self, result: DictationResult) -> None:
        logger.info("Transcripción completada; pasted=%s", result.pasted)
        self.is_busy = False
        self.main_button.configure(text="Grabar", state="normal")
        self.status_var.set("Texto listo")
        detail = "Transcripción pegada automáticamente."
        if result.warning:
            detail = result.warning
        elif not result.pasted:
            detail = "Texto copiado al portapapeles."
        self.detail_var.set(detail)
        self.output.delete("1.0", "end")
        self.output.insert("1.0", result.text)

    def _handle_error(self, exc: Exception) -> None:
        logger.exception("Error manejado en GUI: %s", exc)
        self.is_busy = False
        self.recorder.cleanup()
        self.main_button.configure(text="Grabar", state="normal")
        self.status_var.set("Error")
        self.detail_var.set(f"{exc} | log: {LOG_FILE.name}")
        messagebox.showerror("Error", f"{exc}\n\nVer log: {LOG_FILE}")

    def on_close(self) -> None:
        self._unbind_hotkeys()
        self.recorder.cleanup()
        self.root.destroy()


def main() -> None:
    ensure_console_streams()
    logger.info("Arrancando app de dictado")
    root = tk.Tk()
    def _report_callback_exception(exc_type, exc_value, exc_traceback):
        logger.error(
            "Tkinter callback exception:\n%s",
            "".join(traceback.format_exception(exc_type, exc_value, exc_traceback)),
        )
        messagebox.showerror("Error", f"{exc_value}\n\nVer log: {LOG_FILE}")
    root.report_callback_exception = _report_callback_exception
    try:
        ttk.Style(root).theme_use("vista")
    except Exception:
        pass
    DictationApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
