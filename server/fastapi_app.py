from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import asyncio
import logging
from pathlib import Path

from main import CopilotWebSocketServer

logger = logging.getLogger(__name__)

app = FastAPI(title="Vibe Voice")

# Montar UI estática (sirve index.html como SPA)
ui_dir = Path(__file__).parent.parent / "ui"
app.mount("/", StaticFiles(directory=str(ui_dir), html=True), name="ui")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.on_event("startup")
async def startup_event():
    # Iniciar el servidor WebSocket existente (usa puerto 8765 por defecto)
    logger.info("Startup: iniciando CopilotWebSocketServer background task")
    loop = asyncio.get_event_loop()
    server = CopilotWebSocketServer(host="localhost", port=8765)
    # Guardar referencia para shutdown
    app.state.copilot_server = server
    # Ejecutar start() como tarea en segundo plano
    loop.create_task(server.start())


@app.on_event("shutdown")
async def shutdown_event():
    server = getattr(app.state, "copilot_server", None)
    if server:
        logger.info("Shutdown: deteniendo CopilotWebSocketServer")
        server.stop()
