# Instalacion local (modo recomendado)

Este es el camino recomendado para la mayoria de usuarios: todo local, sin exponer puertos a Internet.

## 1) Clonar

```bash
git clone https://github.com/Jovenjr/Vibe-Voice.git
cd Vibe-Voice
```

## 2) Crear entorno virtual

Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows:

```bat
python -m venv .venv
.venv\Scripts\activate
```

## 3) Instalar dependencias

```bash
pip install -r server/requirements.txt
```

## 4) Configurar `.env`

Linux:

```bash
cp .env.example .env
```

Windows:

```bat
copy .env.example .env
```

Completa solo lo que vayas a usar (por ejemplo STT/TTS/Telegram).

## 5) Validar entorno

Linux:

```bash
./doctor.sh
```

Windows:

```bat
doctor.bat
```

## 6) Iniciar servicio

Linux:

```bash
./start_server.sh
```

Windows:

```bat
start_server.bat
```

Abrir en navegador: `http://127.0.0.1:8080`

## 7) Estado y parada

Linux:

```bash
./status_server.sh
./stop_server.sh
```

Windows:

```bat
status_server.bat
stop_server.bat
```
