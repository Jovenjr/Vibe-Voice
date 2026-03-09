import os
import re

# Examinar archivo .pb de Antigravity conversations
pb_file = r'C:\Users\joven\.gemini\antigravity\conversations\68e6172b-e371-4362-9044-f7b2442a1e47.pb'

with open(pb_file, 'rb') as f:
    data = f.read()
    
print(f"Tamaño: {len(data)} bytes")
print(f"\nPrimeros 300 bytes (hex + ascii):")
for i in range(0, min(300, len(data)), 32):
    chunk = data[i:i+32]
    hex_str = ' '.join(f'{b:02x}' for b in chunk)
    ascii_str = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
    print(f"{i:04d}: {hex_str:<96} {ascii_str}")

# Buscar strings legibles
print("\n\nStrings encontradas (primeras 30):")
strings = re.findall(b'[\x20-\x7e]{8,}', data)
for s in strings[:30]:
    decoded = s.decode('ascii', errors='ignore')
    if len(decoded) > 100:
        decoded = decoded[:100] + "..."
    print(f"  - {decoded}")
