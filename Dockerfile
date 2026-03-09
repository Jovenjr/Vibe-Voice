FROM python:3.11-slim

WORKDIR /app

# Instalar dependencias del sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Copiar requirements e instalar dependencias Python
COPY server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código del servidor
COPY server/ ./server/
COPY ui/ ./ui/

# Puerto WebSocket y HTTP
EXPOSE 8765 8080

# Variable de entorno para indicar que estamos en Docker
ENV DOCKER_MODE=1
ENV PYTHONUNBUFFERED=1

# Directorio de trabajo para el servidor
WORKDIR /app/server

CMD ["python", "-u", "main.py"]
