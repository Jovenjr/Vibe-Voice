# Security Policy

## Supported Versions

Este proyecto está en evolución activa. Por ahora, la rama principal es la única versión soportada para correcciones de seguridad.

## Reporting a Vulnerability

Si encuentras una vulnerabilidad, no publiques detalles sensibles en un issue público de inmediato.

Recomendación:

- describe el problema de forma privada al mantenedor
- incluye pasos para reproducirlo
- explica el impacto esperado
- adjunta evidencia mínima necesaria

Mientras no exista un canal privado dedicado, usa GitHub de forma responsable y evita exponer secretos, tokens, rutas locales o datos personales.

## Operational Security Notes

Vibe Voice puede operar funciones de entrada/salida remota (UI web, Telegram input, STT/TTS, bridge local de pegado). Esas funciones deben considerarse privilegiadas.

Recomendaciones minimas:

- Mantener backend/UI en loopback y publicar solo mediante HTTPS reverse proxy.
- Aplicar allowlist por IP/CIDR y firewall.
- Proteger `TELEGRAM_BOT_TOKEN`, API keys y credenciales asociadas.
- No exponer el bridge local (`127.0.0.1:8766`) fuera del host.
- Desactivar funciones remotas cuando no se esten usando.
