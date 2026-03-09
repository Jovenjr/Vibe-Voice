import os
import re
import sys
from pathlib import Path


def resolve_pb_file() -> Path:
    if len(sys.argv) > 1 and sys.argv[1].strip():
        return Path(sys.argv[1]).expanduser()

    env_path = os.getenv("WINDSURF_PB_FILE", "").strip()
    if env_path:
        return Path(env_path).expanduser()

    print("Uso: python server/check_windsurf.py <ruta_al_archivo.pb>")
    print("O define la variable de entorno WINDSURF_PB_FILE")
    sys.exit(1)


pb_file = resolve_pb_file()

try:
    data = pb_file.read_bytes()
except (FileNotFoundError, PermissionError) as exc:
    print(f"Error: no se pudo abrir el archivo '{pb_file}'.")
    print(f"Detalle: {exc}")
    sys.exit(1)

print(f"Tamaño: {len(data)} bytes")
print("\nPrimeros 300 bytes (hex + ascii):")
for i in range(0, min(300, len(data)), 32):
    chunk = data[i:i + 32]
    hex_str = ' '.join(f'{b:02x}' for b in chunk)
    ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
    print(f"{i:04d}: {hex_str:<96} {ascii_str}")

print("\n\nStrings encontradas (primeras 30):")
strings = re.findall(b'[\x20-\x7e]{8,}', data)
for item in strings[:30]:
    decoded = item.decode('ascii', errors='ignore')
    if len(decoded) > 100:
        decoded = decoded[:100] + "..."
    print(f"  - {decoded}")