"""
run_hidden.pyw
==============
Ejecuta el servidor Vibe Voice sin ventana de consola.

Al usar extensión .pyw, Windows usa pythonw.exe que no crea ninguna ventana.
El proceso corre 100% en background — sin QuickEdit Mode, sin suspensión.

Los logs se guardan en: server/vibe_voice.log
Para ver logs en tiempo real: 
    powershell: Get-Content server\vibe_voice.log -Wait
    cmd:        type server\vibe_voice.log (estático) 
"""

import sys
import os
import subprocess
from pathlib import Path

# Directorio del script
HERE = Path(__file__).parent
SERVER_DIR = HERE / "server"
LOG_FILE   = SERVER_DIR / "vibe_voice.log"

# Lanzar main.py con python.exe (no pythonw) para que pueda usar subprocesos,
# pero sin ventana visible gracias a DETACHED_PROCESS + CREATE_NO_WINDOW
CREATE_NO_WINDOW   = 0x08000000
DETACHED_PROCESS   = 0x00000008

# Usar python.exe explícito (no pythonw.exe) para que funcionen los threads
python_exe = sys.executable.replace("pythonw.exe", "python.exe")

proc = subprocess.Popen(
    [python_exe, "-u", str(SERVER_DIR / "main.py")],
    stdout=open(LOG_FILE, "w", encoding="utf-8", buffering=1),
    stderr=subprocess.STDOUT,
    creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS,
    cwd=str(SERVER_DIR),
)

# Escribir PID para poder matar el proceso después
(HERE / "server" / "server.pid").write_text(str(proc.pid))
