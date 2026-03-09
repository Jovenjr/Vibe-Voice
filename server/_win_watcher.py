"""
_win_watcher.py
===============
Watcher de archivos de latencia mínima usando ReadDirectoryChangesW de Windows.

ReadDirectoryChangesW es una syscall de Windows que bloquea el thread hasta que
el kernel detecta un cambio en el directorio — sin polling, sin sleep().
Latencia típica: 1-5ms desde que el proceso escribe hasta que este código lo sabe.

Esto es lo que usa internamente Windows Explorer para actualizar en tiempo real.
"""

import os
import ctypes
import ctypes.wintypes
import threading
import logging
from pathlib import Path
from typing import Callable, Set

log = logging.getLogger(__name__)

# ── Constantes de la Win32 API ────────────────────────────────────────────────
FILE_LIST_DIRECTORY         = 0x0001
FILE_SHARE_READ             = 0x00000001
FILE_SHARE_WRITE            = 0x00000002
FILE_SHARE_DELETE           = 0x00000004
OPEN_EXISTING               = 3
FILE_FLAG_BACKUP_SEMANTICS  = 0x02000000
FILE_FLAG_OVERLAPPED        = 0x40000000
INVALID_HANDLE_VALUE        = ctypes.wintypes.HANDLE(-1).value

# Qué cambios notificar
FILE_NOTIFY_CHANGE_FILE_NAME  = 0x00000001
FILE_NOTIFY_CHANGE_SIZE       = 0x00000008
FILE_NOTIFY_CHANGE_LAST_WRITE = 0x00000010

WATCH_FLAGS = (
    FILE_NOTIFY_CHANGE_FILE_NAME |
    FILE_NOTIFY_CHANGE_SIZE      |
    FILE_NOTIFY_CHANGE_LAST_WRITE
)

BUFFER_SIZE = 65536  # 64KB — suficiente para múltiples eventos


class _FileNotifyInfo(ctypes.Structure):
    _fields_ = [
        ("NextEntryOffset", ctypes.wintypes.DWORD),
        ("Action",          ctypes.wintypes.DWORD),
        ("FileNameLength",  ctypes.wintypes.DWORD),
        ("FileName",        ctypes.wintypes.WCHAR * 1),  # variable length
    ]


def _watch_directory(directory: str, callback: Callable[[str], None], stop_event: threading.Event):
    """
    Hilo que usa ReadDirectoryChangesW para detectar cambios con latencia mínima.
    Llama callback(filepath_absoluto) cada vez que un .jsonl cambia.
    """
    kernel32 = ctypes.windll.kernel32

    handle = kernel32.CreateFileW(
        directory,
        FILE_LIST_DIRECTORY,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        None,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )

    if handle == INVALID_HANDLE_VALUE:
        log.warning("_win_watcher: no se pudo abrir directorio '%s' (error %d)", directory, kernel32.GetLastError())
        return

    log.debug("_win_watcher: escuchando '%s' con ReadDirectoryChangesW", directory)

    buf = ctypes.create_string_buffer(BUFFER_SIZE)
    bytes_returned = ctypes.wintypes.DWORD(0)

    try:
        while not stop_event.is_set():
            ok = kernel32.ReadDirectoryChangesW(
                handle,
                buf,
                BUFFER_SIZE,
                False,   # bWatchSubtree = False (solo este directorio)
                WATCH_FLAGS,
                ctypes.byref(bytes_returned),
                None,    # sin OVERLAPPED → llamada bloqueante
                None,
            )

            if stop_event.is_set():
                break

            if not ok or bytes_returned.value == 0:
                continue

            # Parsear los registros FILE_NOTIFY_INFORMATION
            offset = 0
            while offset < bytes_returned.value:
                # Leer campos manualmente (estructura de tamaño variable)
                next_offset  = ctypes.wintypes.DWORD.from_buffer(buf, offset).value
                # action     = ctypes.wintypes.DWORD.from_buffer(buf, offset + 4).value
                name_len     = ctypes.wintypes.DWORD.from_buffer(buf, offset + 8).value
                name_buf     = buf.raw[offset + 12: offset + 12 + name_len]
                filename     = name_buf.decode("utf-16-le", errors="replace")

                if filename.endswith(".jsonl"):
                    full_path = os.path.join(directory, filename)
                    try:
                        callback(full_path)
                    except Exception as exc:
                        log.error("_win_watcher: callback error: %s", exc)

                if next_offset == 0:
                    break
                offset += next_offset

    finally:
        kernel32.CloseHandle(handle)
        log.debug("_win_watcher: handle cerrado para '%s'", directory)


class WinDirectoryWatcher:
    """
    Watcher de latencia mínima para Windows.
    Reemplaza el polling sleep() por ReadDirectoryChangesW.

    Uso:
        watcher = WinDirectoryWatcher()
        watcher.watch("/ruta/chatSessions", callback_fn)
        watcher.start()
        ...
        watcher.stop()
    """

    def __init__(self):
        self._watched: dict[str, threading.Thread] = {}
        self._stop = threading.Event()

    def watch(self, directory: str, callback: Callable[[str], None]):
        """Registra un directorio para monitorear."""
        if directory in self._watched:
            return
        t = threading.Thread(
            target=_watch_directory,
            args=(directory, callback, self._stop),
            daemon=True,
            name=f"WinWatcher-{Path(directory).name[:20]}",
        )
        self._watched[directory] = t
        if not self._stop.is_set():
            t.start()
            log.info("WinWatcher: monitoreando '%s'", directory)

    def start(self):
        """Inicia todos los threads que aún no hayan arrancado."""
        for t in self._watched.values():
            if not t.is_alive():
                t.start()

    def stop(self):
        """Detiene todos los threads de forma limpia."""
        self._stop.set()
        # ReadDirectoryChangesW bloqueante se desbloquea al cerrar el handle.
        # Los threads son daemon=True, el OS los limpia al salir.
        log.info("WinWatcher: detenido (%d directorios)", len(self._watched))

    @property
    def count(self) -> int:
        return len(self._watched)


# ── Disponibilidad ────────────────────────────────────────────────────────────

def is_available() -> bool:
    """True si estamos en Windows y ctypes.windll existe."""
    try:
        return hasattr(ctypes, "windll") and os.name == "nt"
    except Exception:
        return False
